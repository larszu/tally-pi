"""Eine Uebersetzung zwischen „die Lampe brennt" und „der Pin liegt tief".

BEFUND (Defektformen-Sweep, Form `zwei-rechnungen`, gemessen 2026-09-07).
Diese eine Umrechnung stand an VIER Stellen, drei davon voneinander
unabhaengig gerechnet:

  1. Auto-Treiber    `TALLY_OUTPUTS.set(bcm, on_air != active_high)`
  2. Diagnose (Soll) `sw_on = sw_low if not active_high else (not sw_low)`
  3. Diagnose (Ist)  `kernel_on = (pin_level == 0) if not active_high else …`
  4. Browser, ZWEIMAL: `action = isHigh ? 'latch-off' : 'latch-on'`
     (`setup-guide.html`, Geraetekarte und Diagnose-Tabelle — Zeichen fuer
     Zeichen dieselbe Zeile, zweimal abgeschrieben)

Zwei davon waren nicht nur doppelt, sondern falsch:

* Die beiden Browser-Stellen lasen ihre Polaritaet aus VERSCHIEDENEN Quellen.
  Die Geraetekarte nahm `devices[i]` — den ungespeicherten Formularstand; die
  Diagnose-Tabelle nahm `devs[i]` aus `/tally-diagnostics` — den gespeicherten.
  Wer die Polaritaet umstellte und nicht speicherte, hatte zwei Knoepfe fuer
  denselben Pin, die einander entgegengesetzte Kommandos schickten.
* Der Browser schickte einen PEGEL, kein Anliegen. Fuer eine active-high Lampe
  hiess „einschalten" `latch-off`, und genau so landete es im Ereignis-Log
  (`log_event("tally-out", action=action)`). Fuer jede active-high Lampe
  protokollierte das Log das Gegenteil dessen, was passiert ist.

Diese Datei haelt beides fest: die Wahrheitstabelle der einen Funktion, und
dass die drei uebrigen Stellen sie benutzen statt selbst zu rechnen.

Lauf: `python3 -m unittest discover -s tests`.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_server as gs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class Wahrheitstabelle(unittest.TestCase):
    """`tally_pin_treibt_tief` — vier Zeilen, mehr gibt es nicht."""

    def test_active_low_lampe_an_zieht_tief(self):
        # Der Normalfall: Relaisplatine, IN auf LOW = Relais zieht an.
        self.assertIs(gs.tally_pin_treibt_tief(False, True), True)

    def test_active_low_lampe_aus_laesst_hoch(self):
        self.assertIs(gs.tally_pin_treibt_tief(False, False), False)

    def test_active_high_lampe_an_laesst_hoch(self):
        # Direkt angeschlossene LED gegen GND: HIGH = Strom = Licht.
        self.assertIs(gs.tally_pin_treibt_tief(True, True), False)

    def test_active_high_lampe_aus_zieht_tief(self):
        self.assertIs(gs.tally_pin_treibt_tief(True, False), True)


class WannBrenntSie(unittest.TestCase):
    """`tally_lampe_soll_leuchten` — die andere Haelfte, ohne Polaritaet."""

    def test_pgm_nur_bei_pgm(self):
        self.assertIs(gs.tally_lampe_soll_leuchten("pgm", "pgm"), True)
        for zustand in ("pvw", "safe", "offline", "unknown"):
            self.assertIs(gs.tally_lampe_soll_leuchten("pgm", zustand), False,
                          f"'{zustand}' darf die Lampe nicht anmachen")

    def test_pgm_pvw_auch_bei_pvw(self):
        self.assertIs(gs.tally_lampe_soll_leuchten("pgm_pvw", "pgm"), True)
        self.assertIs(gs.tally_lampe_soll_leuchten("pgm_pvw", "pvw"), True)
        for zustand in ("safe", "offline", "unknown"):
            self.assertIs(gs.tally_lampe_soll_leuchten("pgm_pvw", zustand), False)

    def test_offline_macht_keine_lampe_an(self):
        # Wichtig genug fuer einen eigenen Test: „der Mischer ist weg" ist
        # kein On-Air. Eine Lampe, die bei Verbindungsverlust angeht, sagt
        # dem Raum das Gegenteil der Wahrheit.
        for trigger in ("pgm", "pgm_pvw"):
            self.assertIs(gs.tally_lampe_soll_leuchten(trigger, "offline"), False)


class PolaritaetNachschlagen(unittest.TestCase):
    """`tally_out_polaritaet` — der Kern holt sie aus der GESPEICHERTEN Config."""

    CFG = {"devices": [
        {"id": "cam1", "out_gpio": 17, "out_active_high": True},
        {"id": "cam2", "out_gpio": 22, "out_active_high": False},
        {"id": "cam3", "out_gpio": 23},
    ]}

    def test_findet_das_geraet_zum_pin(self):
        self.assertIs(gs.tally_out_polaritaet(self.CFG, 17), True)
        self.assertIs(gs.tally_out_polaritaet(self.CFG, 22), False)

    def test_ohne_angabe_active_low(self):
        self.assertIs(gs.tally_out_polaritaet(self.CFG, 23), False)

    def test_unbekannter_pin_faellt_auf_die_sichere_vorgabe(self):
        # Nicht auf None und nicht auf einen Krach: dieselbe Vorgabe wie im
        # Formular und im Schema.
        self.assertIs(gs.tally_out_polaritaet(self.CFG, 99), False)
        self.assertIs(gs.tally_out_polaritaet({}, 17), False)


class BeideWegeSindSichEinig(unittest.TestCase):
    """Der Auto-Treiber und der Hand-Knopf muessen denselben Pegel wollen.

    Das ist die Zusicherung, die der Defekt gebrochen hat: zwei Wege zu
    „mach die Lampe an", die bei active-high verschiedene Pegel schickten.
    """

    def test_active_high_auto_und_hand_wollen_dasselbe(self):
        auto = gs.tally_pin_treibt_tief(
            True, gs.tally_lampe_soll_leuchten("pgm", "pgm"))
        hand = gs.tally_pin_treibt_tief(True, True)   # was `lamp-on` tut
        self.assertIs(auto, hand)

    def test_active_low_auto_und_hand_wollen_dasselbe(self):
        auto = gs.tally_pin_treibt_tief(
            False, gs.tally_lampe_soll_leuchten("pgm", "pgm"))
        hand = gs.tally_pin_treibt_tief(False, True)
        self.assertIs(auto, hand)


class DieDiagnoseRechnetNichtSelbst(unittest.TestCase):
    """`build_tally_diagnostics` liest den Pegel zurueck — ueber dieselbe Funktion.

    Gefahren wird gegen einen gefaelschten Pin-Zustand, ohne GPIO: die
    Diagnose muss fuer eine active-high Lampe, die auf HIGH liegt, „an" sagen
    und nicht „aus".
    """

    def _diagnose(self, active_high, pin_level, sw_low):
        cfg = {"atem_ip": "10.0.0.1", "devices": [{
            "id": "cam1", "name": "Kamera 1", "input": 1, "me": 1,
            "out_gpio": 17, "out_trigger": "pgm",
            "out_active_high": active_high,
        }]}
        atem = {"connected": True, "pgm": {"0": 1}, "pvw": {}, "aux": {}}
        alt = (gs.load_tally_config, gs.get_atem_state, gs.parse_gpio_state)
        werte, latched = dict(gs.TALLY_OUTPUTS._values), set(gs.TALLY_OUTPUTS._latched)
        gs.load_tally_config = lambda: cfg
        gs.get_atem_state = lambda: atem
        gs.parse_gpio_state = lambda: {"17": {"value": pin_level}}
        gs.TALLY_OUTPUTS._values = {17: sw_low}
        gs.TALLY_OUTPUTS._latched = set()
        try:
            return gs.build_tally_diagnostics()["devices"][0]
        finally:
            gs.load_tally_config, gs.get_atem_state, gs.parse_gpio_state = alt
            gs.TALLY_OUTPUTS._values, gs.TALLY_OUTPUTS._latched = werte, latched

    def test_active_high_auf_high_heisst_an(self):
        d = self._diagnose(active_high=True, pin_level=1, sw_low=False)
        self.assertEqual(d["state"], "pgm")
        self.assertIs(d["sw_on"], True, "die Lampe brennt — die Diagnose muss es sagen")
        self.assertIs(d["kernel_on"], True)
        self.assertIs(d["consistent"], True)

    def test_active_low_auf_low_heisst_an(self):
        d = self._diagnose(active_high=False, pin_level=0, sw_low=True)
        self.assertIs(d["sw_on"], True)
        self.assertIs(d["kernel_on"], True)
        self.assertIs(d["consistent"], True)

    def test_widerspruch_zwischen_software_und_kernel_faellt_auf(self):
        # Gegenprobe zu den beiden darueber: `consistent` ist nicht einfach
        # immer True.
        d = self._diagnose(active_high=False, pin_level=1, sw_low=True)
        self.assertIs(d["sw_on"], True)
        self.assertIs(d["kernel_on"], False)
        self.assertIs(d["consistent"], False)


class NurEineStelleRechnet(unittest.TestCase):
    """Quelltext-Waechter: die Umrechnung steht genau einmal.

    Verhalten laesst sich hier nicht pruefen — die zwei abgeschriebenen
    Stellen sassen im Browser, und das Repo hat keinen Browser-Lauf. Was
    pruefbar ist: dass niemand die Polaritaet ein zweites Mal von Hand
    umdreht.
    """

    def test_kern_dreht_die_polaritaet_nur_in_der_einen_funktion(self):
        quelle = (ROOT / "guide_server.py").read_text(encoding="utf-8")
        koerper = quelle.split("def tally_pin_treibt_tief(")[1].split("\ndef ")[0]
        draussen = quelle.replace(koerper, "")
        schuldige = [
            zeile.strip() for zeile in draussen.splitlines()
            # Kommentare zaehlen nicht — der Kopf der Funktion ZITIERT die
            # alten Zeilen absichtlich, damit man den Befund wiederfindet.
            if not zeile.lstrip().startswith("#")
            and "active_high" in zeile
            and ("!=" in zeile or "not active_high" in zeile)
        ]
        self.assertEqual(schuldige, [],
                         "Polaritaet wird ausserhalb von tally_pin_treibt_tief "
                         f"noch einmal gedreht: {schuldige}")

    def test_die_oberflaeche_schickt_das_anliegen_nicht_den_pegel(self):
        seite = (ROOT / "setup-guide.html").read_text(encoding="utf-8")
        # Zeilenkommentare raus: der erklaerende Kommentar auf der
        # Geraetekarte nennt die alte Zeile beim Namen.
        ohne_kommentar = "\n".join(
            z for z in seite.splitlines() if not z.lstrip().startswith("//"))
        for pegel in ("latch-on", "latch-off"):
            self.assertNotIn(
                pegel, ohne_kommentar,
                f"die Oberflaeche schickt weiterhin den Pegel '{pegel}' statt "
                "'lamp-on'/'lamp-off' — dann rechnet sie die Polaritaet selbst, "
                "und zwar aus einer anderen Quelle als der Kern")

    def test_der_kern_kennt_das_anliegen(self):
        # Gegenstueck zum Waechter darueber: er waere auch gruen, wenn die
        # Oberflaeche gar nichts mehr schickte.
        quelle = (ROOT / "guide_server.py").read_text(encoding="utf-8")
        self.assertIn('action in ("lamp-on", "lamp-off")', quelle)
        seite = (ROOT / "setup-guide.html").read_text(encoding="utf-8")
        self.assertIn("'lamp-on'", seite)


if __name__ == "__main__":
    unittest.main()
