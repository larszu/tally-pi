"""Ein Pin kann nur EINEM gehoeren — und das wusste keiner der zwei Vertraege.

BEFUND (Defektformen-Sweep, Form `vertrag-nur-feldnamen`, gemessen
2026-09-07). Der Pi fuehrt zwei Listen ueber dieselben physischen Leitungen:

    tally.json      Geraete mit `out_gpio` (Lampe) und `in_gpio` (Taster)
    bindings.json   Companion-Bindungen mit `bcm`

Beide haben einen ordentlichen Waechter, und beide pruefen ausschliesslich
ihre EIGENEN Feldnamen. `validate_tally_config` fuehrt sogar ein `seen_gpio`
und lehnt einen doppelt vergebenen Pin ab — aber nur innerhalb der
Geraeteliste. `validate_binding` prueft `bcm in BCM_TO_PIN` und sonst nichts:
nicht, ob zwei Bindungen auf demselben Pin sitzen, und erst recht nicht, ob
der Pin schon eine Tally-Lampe traegt.

Was dabei herauskommt, ist kein Datenfehler, sondern ein Geraetefehler:
`pi-gpio-watcher` fordert die Leitung als EINGANG an, waehrend der
Guide-Server sie als AUSGANG haelt. libgpiod gibt eine Leitung nur einmal
heraus; wer zweiter ist, bekommt EBUSY. Je nach Startreihenfolge geht
entweder die Lampe nicht an oder der Taster nicht — und keine Seite sagt,
warum. Beide Oberflaechen zeigen ihre Zeile als gespeichert und gueltig.

Lauf: `python3 -m unittest discover -s tests`.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import guide_server as gs  # noqa: E402


def geraet(did, **felder):
    return {"id": did, "name": did.upper(), "input": 1, **felder}


def bindung(bcm, **felder):
    return {"source": "pi", "bcm": bcm,
            "action": {"kind": "press", "page": 1, "row": 0, "column": 0},
            **felder}


class DerKonfliktZwischenDenListen(unittest.TestCase):
    """Das, was keiner der beiden Waechter allein sehen kann."""

    def test_lampe_und_bindung_auf_demselben_pin(self):
        cfg = {"devices": [geraet("cam1", out_gpio=17)]}
        with self.assertRaises(ValueError) as ctx:
            gs.pruefe_pin_konflikte(cfg, [bindung(17)])
        self.assertIn("17", str(ctx.exception))
        self.assertIn("Tally-Ausgang", str(ctx.exception))
        self.assertIn("Companion-Bindung", str(ctx.exception))

    def test_taster_und_bindung_auf_demselben_pin(self):
        cfg = {"devices": [geraet("cam1", in_gpio=22)]}
        with self.assertRaises(ValueError):
            gs.pruefe_pin_konflikte(cfg, [bindung(22)])

    def test_zwei_bindungen_auf_demselben_pin(self):
        # Auch das sah `validate_binding` nicht: es prueft je Eintrag.
        with self.assertRaises(ValueError) as ctx:
            gs.pruefe_pin_konflikte({}, [bindung(5), bindung(5)])
        self.assertIn("5", str(ctx.exception))

    def test_die_meldung_sagt_WOFUER_der_pin_schon_da_ist(self):
        # „GPIO 17 doppelt" allein hilft nicht: der Nutzer hat zwei
        # Oberflaechen und muss wissen, in welcher er suchen soll.
        cfg = {"devices": [geraet("cam1", out_gpio=17)]}
        with self.assertRaises(ValueError) as ctx:
            gs.pruefe_pin_konflikte(cfg, [bindung(17)])
        self.assertIn("CAM1", str(ctx.exception))


class WasErlaubtBleibt(unittest.TestCase):
    """Gegenprobe: die Pruefung darf nicht ins Gegenteil kippen."""

    def test_verschiedene_pins_sind_in_ordnung(self):
        cfg = {"devices": [geraet("cam1", out_gpio=17, in_gpio=22),
                           geraet("cam2", out_gpio=23)]}
        gs.pruefe_pin_konflikte(cfg, [bindung(5), bindung(6)])

    def test_geraete_ohne_pins_stoeren_nicht(self):
        cfg = {"devices": [geraet("cam1"), geraet("cam2")]}
        gs.pruefe_pin_konflikte(cfg, [bindung(17)])

    def test_numato_bindungen_belegen_keinen_pi_pin(self):
        # Sie haengen an einem USB-Relaisboard, nicht am Header.
        cfg = {"devices": [geraet("cam1", out_gpio=17)]}
        gs.pruefe_pin_konflikte(cfg, [{"source": "numato", "channel": 17}])
        # Und der Fall, der es scharf macht: ein `bcm`, das vom Umstellen im
        # Formular stehengeblieben ist. Ohne den `source`-Filter waere das
        # ein erfundener Konflikt — die Bindung fasst den Pin nicht an.
        # (Die erste Fassung dieses Tests hatte das `bcm` NICHT gesetzt und
        # blieb deshalb gruen, als die Gegenprobe den Filter entfernte.)
        gs.pruefe_pin_konflikte(cfg, [{"source": "numato", "channel": 3, "bcm": 17}])

    def test_leere_listen_krachen_nicht(self):
        gs.pruefe_pin_konflikte({}, [])
        gs.pruefe_pin_konflikte({"devices": None}, None)


class DieBelegungsliste(unittest.TestCase):
    """`pin_belegung` — dieselbe Frage, als Auskunft statt als Fehler."""

    def test_nennt_jeden_vergebenen_pin(self):
        cfg = {"devices": [geraet("cam1", out_gpio=17, in_gpio=22)]}
        belegt = gs.pin_belegung(cfg, [bindung(5)])
        self.assertEqual(sorted(belegt), [5, 17, 22])
        self.assertIn("Tally-Ausgang", belegt[17])
        self.assertIn("Taster", belegt[22])
        self.assertIn("Companion", belegt[5])


class BeideEndpunkteFragen(unittest.TestCase):
    """Quelltext-Waechter: eine Pruefung, die nur ein Weg aufruft, ist keine."""

    QUELLE = (ROOT / "guide_server.py").read_text(encoding="utf-8")

    def test_beide_schreibwege_rufen_die_pruefung(self):
        # Zaehlen statt suchen: einmal beim Speichern der Bindungen, einmal
        # beim Speichern der Tally-Konfiguration. Stuende sie nur an einer
        # Stelle, koennte man den Konflikt ueber den anderen Weg anlegen.
        rufe = re.findall(r"^\s*pruefe_pin_konflikte\(", self.QUELLE, re.M)
        self.assertEqual(len(rufe), 2, f"gefunden: {len(rufe)}")

    def test_der_bindungs_weg_prueft_gegen_die_tally_konfiguration(self):
        self.assertIn("pruefe_pin_konflikte(load_tally_config(), data)", self.QUELLE)

    def test_der_tally_weg_prueft_gegen_die_bindungen(self):
        self.assertIn("pruefe_pin_konflikte(data, load_bindings())", self.QUELLE)


class DerBenannteWiderspruch(unittest.TestCase):
    """Die zwei Listen sind sich uneinig, welche Pins es gibt.

    NICHT behoben, mit Absicht: `USABLE_BCMS` laesst 17 Pins zu (ohne I2C,
    SPI, UART), `BCM_TO_PIN` alle 26. Enger zu ziehen wuerde bestehende,
    laufende Installationen ungueltig machen — wer SPI abgeschaltet hat,
    benutzt BCM 7..11 zu Recht. Dieser Test haelt fest, dass der Unterschied
    BEKANNT ist und nicht aus Versehen entsteht.
    """

    def test_der_unterschied_ist_dokumentiert(self):
        nur_bindungen = sorted(set(gs.BCM_TO_PIN) - set(gs.USABLE_BCMS))
        self.assertEqual(nur_bindungen, [2, 3, 7, 8, 9, 10, 11, 14, 15],
                         "I2C, SPI und UART — wenn sich das aendert, gehoert "
                         "der Absatz im Quelltext nachgezogen")
        quelle = (ROOT / "guide_server.py").read_text(encoding="utf-8")
        self.assertIn("BCM 14 (UART TX)", quelle,
                      "der Widerspruch muss im Quelltext benannt stehen, "
                      "sonst haelt ihn spaeter jemand fuer ein Versehen")


if __name__ == "__main__":
    unittest.main()
