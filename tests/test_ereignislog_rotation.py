"""Das Ereignis-Log an seiner Rotationsgrenze — mit der ECHTEN Grenze.

BEFUND (Defektformen-Sweep, Form `fixture-erreicht-grenze-nicht`, gemessen
2026-09-08). `log_event` dreht die Datei bei `EVENT_LOG_MAX` (1 MB) nach
`events.log.1`. Dieser Zweig ist nie gelaufen: kein Test hat je so viel
geschrieben.

Und weil er nie lief, ist auch nie aufgefallen, dass der LESER ihm nicht
folgt. `read_event_log` sah nur `events.log` an. In dem Moment, in dem die
Datei dreht, hat die neue Datei genau eine Zeile — und das Log-Fenster, in
dem gerade jemand 200 Ereignisse las, zeigt eine. Die anderen liegen in
`events.log.1`, und niemand las sie.

`pi-guide` laeuft als Dienst durch und schreibt bei jedem Tally-Wechsel und
jedem ATEM-Buswechsel eine Zeile; 1 MB sind rund 9000 davon.

DIESE DATEI ARBEITET MIT DER ECHTEN GRENZE. `EVENT_LOG_MAX` wird nicht
heruntergesetzt — das waere derselbe Fehler noch einmal: eine Pruefung, die
die Grenze nur nachspielt statt sie zu erreichen. Geschrieben wird deshalb
wirklich ueber 1 MB, in einem Rutsch, damit es schnell bleibt.

Lauf: `python3 -m unittest discover -s tests`.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import guide_server as gs  # noqa: E402


class RotationsGrenze(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tallylog-"))
        self._alt = gs.EVENT_LOG_FILE
        gs.EVENT_LOG_FILE = self.dir / "events.log"

    def tearDown(self):
        gs.EVENT_LOG_FILE = self._alt
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fuelle(self, anzahl, ab=0):
        """`anzahl` echte Log-Zeilen direkt in die aktuelle Datei schreiben."""
        with open(gs.EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            for i in range(anzahl):
                f.write(json.dumps({"ts": float(ab + i), "kind": "tally",
                                    "id": f"cam{ab + i}", "state": "pgm"}) + "\n")

    def _fuelle_bis_ueber_die_grenze(self, ab=0):
        """Schreiben, bis die Datei die ECHTE Grenze ueberschreitet.

        Gezaehlt statt geraten: eine feste Zeilenzahl waere beim ersten
        laengeren Feld wieder zu klein, und der Test wuerde still aufhoeren,
        die Grenze zu erreichen — genau die Form, gegen die er antritt.
        """
        geschrieben = 0
        while (not gs.EVENT_LOG_FILE.exists()
               or gs.EVENT_LOG_FILE.stat().st_size <= gs.EVENT_LOG_MAX):
            self._fuelle(2000, ab=ab + geschrieben)
            geschrieben += 2000
        return geschrieben

    def test_unter_der_grenze_dreht_nichts(self):
        gs.log_event("tally", id="cam1", state="pgm")
        self.assertTrue(gs.EVENT_LOG_FILE.exists())
        self.assertFalse(gs.rotated_log_file().exists())
        self.assertEqual(len(gs.read_event_log(200)), 1)

    def test_ueber_der_grenze_dreht_es_wirklich(self):
        # Die ECHTE Grenze, nicht eine kleinere: ueber 1 MB reale Zeilen.
        self._fuelle_bis_ueber_die_grenze()
        self.assertGreater(gs.EVENT_LOG_FILE.stat().st_size, gs.EVENT_LOG_MAX,
                           "das Fixture erreicht die Grenze nicht — dann prueft "
                           "dieser Test nichts")
        gs.log_event("tally", id="danach", state="pvw")
        self.assertTrue(gs.rotated_log_file().exists(), "es wurde gedreht")
        with open(gs.EVENT_LOG_FILE, encoding="utf-8") as f:
            self.assertEqual(len(f.readlines()), 1,
                             "die neue Datei hat genau die eine neue Zeile")

    def test_nach_dem_drehen_ist_das_fenster_nicht_leer(self):
        # DAS ist der Defekt. Vorher: 1 Zeile statt 200.
        n = self._fuelle_bis_ueber_die_grenze()
        gs.log_event("tally", id="danach", state="pvw")
        eintraege = gs.read_event_log(200)
        self.assertEqual(len(eintraege), 200,
                         "nach dem Drehen muss das Fenster weiterhin voll sein")
        self.assertEqual(eintraege[-1]["id"], "danach", "das Neueste steht hinten")
        self.assertEqual(eintraege[0]["id"], f"cam{n - 199}",
                         "und davor luecklos das, was in der gedrehten Datei steht")

    def test_die_reihenfolge_stimmt_ueber_die_grenze_hinweg(self):
        self._fuelle_bis_ueber_die_grenze()
        gs.log_event("tally", id="danach", state="pvw")
        ts = [e.get("ts", 0) for e in gs.read_event_log(50)]
        self.assertEqual(ts, sorted(ts), "aelter zuerst, ueber beide Dateien hinweg")

    def test_mehr_verlangt_als_da_ist(self):
        self._fuelle(3)
        self.assertEqual(len(gs.read_event_log(200)), 3,
                         "kein Auffuellen mit Leerem, wenn es nichts nachzulegen gibt")

    def test_nur_die_gedrehte_datei_ist_noch_da(self):
        # Kommt vor: jemand loescht `events.log` von Hand oder der Dienst
        # startet mit leerem /run. Das Vorherige ist trotzdem lesbar.
        self._fuelle(5)
        gs.EVENT_LOG_FILE.rename(gs.rotated_log_file())
        self.assertEqual(len(gs.read_event_log(200)), 5)

    def test_ohne_jede_datei_bleibt_es_leer(self):
        self.assertEqual(gs.read_event_log(200), [])

    def test_beim_zweiten_drehen_wird_die_alte_ersetzt(self):
        # Sonst waechst `events.log.1` mit — die Rotation soll begrenzen.
        n = self._fuelle_bis_ueber_die_grenze()
        gs.log_event("tally", id="erste", state="pvw")
        erste_groesse = gs.rotated_log_file().stat().st_size
        self._fuelle_bis_ueber_die_grenze(ab=n + 100000)
        gs.log_event("tally", id="zweite", state="pvw")
        self.assertLess(abs(gs.rotated_log_file().stat().st_size - erste_groesse),
                        erste_groesse, "die gedrehte Datei wurde ersetzt, nicht angehaengt")
        self.assertEqual(gs.read_event_log(1)[0]["id"], "zweite")


if __name__ == "__main__":
    unittest.main()
