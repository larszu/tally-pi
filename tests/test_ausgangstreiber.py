"""Treiberart eines Tally-Ausgangs: open-drain oder push-pull, aus der Polaritaet.

BEFUND 2026-09-28. Der Kopfkommentar ueber `TallyOutputs` versprach
„open-drain", der Code forderte fuer JEDE Lampe `Drive.PUSH_PULL` an. Ein
5-V-Relaismodul sah so im Ruhezustand 3,3 V gegen seine 5 V — Reststrom durch
die Optokoppler-LED, Restglimmen, Rueckspeisung in einen Pin, der nur 3,3 V
vertraegt. Und der Read-back-„Bug" auf BCM 20/22 mit `pinctrl`-Hammer passte
zu einem Pin, den etwas von aussen haelt.

Diese Datei haelt fest:
  1. die Regel ist EINE Funktion (`tally_ausgang_treiber`), zwei Zeilen Wahrheit;
  2. `configure()` gibt sie an libgpiod weiter — je Leitung, in einer Anforderung;
  3. bei open-drain ist ein Read-back-Mismatch eine MELDUNG, kein `pinctrl dh`.
     `pinctrl dh` wuerde die Leitung heimlich auf push-pull umstellen und gegen
     die Ursache treiben.

Kein libgpiod auf dem Testrechner: ein nachgebautes `gpiod`-Modul zeichnet auf,
was angefordert wurde. Es erfindet keine Pegel — der eine Test, der einen
fremden Pegel braucht, stellt ihn ausdruecklich ein.

Lauf: `python3 -m unittest discover -s tests`.
"""
import enum
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_server as gs  # noqa: E402


class Regel(unittest.TestCase):
    def test_active_low_ist_open_drain(self):
        self.assertEqual(gs.tally_ausgang_treiber(False), "open-drain")

    def test_active_high_ist_push_pull(self):
        self.assertEqual(gs.tally_ausgang_treiber(True), "push-pull")

    def test_je_pin_ohne_polaritaet_ist_open_drain(self):
        # Alte Aufrufer ohne Polaritaet: der Fall, der nie Strom nach aussen drueckt.
        self.assertEqual(gs.treiber_je_pin([17, 22]),
                         {17: "open-drain", 22: "open-drain"})

    def test_je_pin_mit_polaritaet(self):
        self.assertEqual(gs.treiber_je_pin([17, 22], {22: True}),
                         {17: "open-drain", 22: "push-pull"})


# ── Ein nachgebautes gpiod ──────────────────────────────────────────────────

class _Drive(enum.Enum):
    PUSH_PULL = 1
    OPEN_DRAIN = 2
    OPEN_SOURCE = 3


class _Value(enum.Enum):
    INACTIVE = 0
    ACTIVE = 1


class _Direction(enum.Enum):
    OUTPUT = 1


class _Bias(enum.Enum):
    DISABLED = 0


class _LineSettings:
    def __init__(self, **kw):
        self.kw = kw


class _Request:
    """Merkt sich Schreibzugriffe; `fremd` erzwingt einen Pegel von aussen."""
    def __init__(self, config):
        self.config = config
        self.werte = {b: s.kw["output_value"] for b, s in config.items()}
        self.fremd = {}
        self.released = False

    def set_value(self, bcm, v):
        self.werte[bcm] = v

    def get_value(self, bcm):
        return self.fremd.get(bcm, self.werte[bcm])

    def release(self):
        self.released = True


def _fake_gpiod():
    gpiod = types.ModuleType("gpiod")
    line = types.ModuleType("gpiod.line")
    line.Drive, line.Value, line.Direction, line.Bias = _Drive, _Value, _Direction, _Bias
    gpiod.line = line
    gpiod.LineSettings = _LineSettings
    gpiod.requests = []

    def request_lines(chip, consumer, config):
        req = _Request(config)
        gpiod.requests.append(req)
        return req
    gpiod.request_lines = request_lines
    return gpiod, line


class MitNachgebautemGpiod(unittest.TestCase):
    def setUp(self):
        self.gpiod, line = _fake_gpiod()
        self._alt = {k: sys.modules.get(k) for k in ("gpiod", "gpiod.line")}
        sys.modules["gpiod"] = self.gpiod
        sys.modules["gpiod.line"] = line
        self.pinctrl_aufrufe = []
        self._alt_pinctrl = gs.TallyOutputs._pinctrl_set
        gs.TallyOutputs._pinctrl_set = staticmethod(
            lambda bcm, high: self.pinctrl_aufrufe.append((bcm, high)))
        self.out = gs.TallyOutputs()

    def tearDown(self):
        gs.TallyOutputs._pinctrl_set = self._alt_pinctrl
        for k, v in self._alt.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_configure_gibt_je_leitung_ihre_treiberart(self):
        self.out.configure([17, 22], {17: False, 22: True})
        self.assertIsNone(self.out.state()["error"])
        req = self.gpiod.requests[-1]
        self.assertEqual(req.config[17].kw["drive"], _Drive.OPEN_DRAIN)
        self.assertEqual(req.config[22].kw["drive"], _Drive.PUSH_PULL)
        # Ruhe ist in beiden Faellen „HIGH" — was die Lampe daraus macht,
        # entscheidet tally_pin_treibt_tief, nicht die Anforderung.
        self.assertEqual(req.config[17].kw["output_value"], _Value.ACTIVE)
        self.assertEqual(req.config[22].kw["output_value"], _Value.ACTIVE)
        self.assertEqual(self.out.state()["treiber"], {"17": "open-drain", "22": "push-pull"})

    def test_polaritaetswechsel_fordert_neu_an(self):
        self.out.configure([17], {17: False})
        self.out.configure([17], {17: True})
        self.assertEqual(len(self.gpiod.requests), 2)
        self.assertTrue(self.gpiod.requests[0].released)
        self.assertEqual(self.gpiod.requests[1].config[17].kw["drive"], _Drive.PUSH_PULL)

    def test_open_drain_mismatch_meldet_statt_pinctrl(self):
        self.out.configure([17], {17: False})
        req = self.gpiod.requests[-1]
        self.out.set(17, True)                # erst AN (ziehen) …
        req.fremd[17] = _Value.INACTIVE       # … dann haelt etwas die Leitung unten
        ereignisse = []
        alt = gs.log_event
        gs.log_event = lambda kind, **f: ereignisse.append((kind, f))
        try:
            ok, _ = self.out.set(17, False)   # AUS = nicht mehr ziehen
        finally:
            gs.log_event = alt
        self.assertTrue(ok)
        self.assertEqual(self.pinctrl_aufrufe, [])
        arten = [k for k, _ in ereignisse]
        self.assertIn("pin_extern_gehalten", arten)

    def test_push_pull_mismatch_nutzt_weiter_pinctrl(self):
        # Der bisherige Weg bleibt fuer push-pull: dort kann der Pin treiben.
        self.out.configure([22], {22: True})
        req = self.gpiod.requests[-1]
        self.out.set(22, True)
        req.fremd[22] = _Value.INACTIVE
        alt = gs.log_event
        gs.log_event = lambda kind, **f: None
        try:
            self.out.set(22, False)
        finally:
            gs.log_event = alt
        self.assertEqual(self.pinctrl_aufrufe, [(22, True)])


if __name__ == "__main__":
    unittest.main()
