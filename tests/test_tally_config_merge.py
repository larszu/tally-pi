"""Tests fuer `merge_tally_config` in `guide_server.py`.

WAS SCHIEFLIEF (gemessen 2026-09-04). `POST /tally-config` schrieb das
gepostete Objekt vollstaendig. `load_tally_config` fuellt fehlende Schluessel
aus DEFAULT_TALLY_CONFIG nach, und dort ist `atem_ip` leer.

Der Planer erzeugt eine Datei mit genau `{id, name, input}` je Geraet -- mit
Absicht: `tallyMap.ts` laesst `out_gpio`, `out_trigger` und `me` ausdruecklich
weg, weil das Verdrahtungs-Entscheidungen an der Hardware sind. Woertlich dort:
"eine erfundene Pin-Nummer waere schlimmer als ein fehlendes Feld".

Genau diese Zurueckhaltung wurde bestraft: wer die Planer-Datei postete,
loeschte `atem_ip` und JEDE GPIO-Zuordnung. `tally_state_for_device` liefert
danach fuer alles "offline", und keine Lampe schaltet mehr.

Die Regel, die hier geprueft wird: ein Feld, das der Poster NICHT als
Schluessel mitschickt, bleibt stehen. Ein Feld, das er mitschickt -- auch leer
oder null --, gewinnt. Absent heisst "weiss ich nicht", nicht "loesch das".

Lauf: `python3 -m unittest discover -s tests -v`  (keine Abhaengigkeiten).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guide_server import merge_tally_config  # noqa: E402


def pi_config():
    """Der Stand auf dem Pi: ATEM-Adresse und verdrahtete Lampen."""
    return {
        "atem_ip": "192.168.10.240",
        "devices": [
            {"id": "cam1", "name": "Kamera 1", "input": 1, "me": 1,
             "out_gpio": 17, "out_trigger": "high", "in_gpio": 27},
            {"id": "cam2", "name": "Kamera 2", "input": 2, "out_gpio": 22},
        ],
    }


def planer_datei():
    """Was der Planer erzeugt -- genau die drei Felder, die er besitzt."""
    return {
        "devices": [
            {"id": "cam1", "name": "Kamera 1 (Havarie)", "input": 3},
            {"id": "cam2", "name": "Kamera 2", "input": 2},
        ],
    }


class TestMergeTallyConfig(unittest.TestCase):
    def test_atem_ip_ueberlebt_einen_post_ohne_atem_ip(self):
        out = merge_tally_config(planer_datei(), pi_config())
        self.assertEqual(out["atem_ip"], "192.168.10.240")

    def test_gpio_zuordnung_ueberlebt(self):
        out = merge_tally_config(planer_datei(), pi_config())
        by_id = {d["id"]: d for d in out["devices"]}
        self.assertEqual(by_id["cam1"]["out_gpio"], 17)
        self.assertEqual(by_id["cam1"]["out_trigger"], "high")
        self.assertEqual(by_id["cam1"]["in_gpio"], 27)
        self.assertEqual(by_id["cam2"]["out_gpio"], 22)

    def test_der_poster_besitzt_name_und_input(self):
        # Der Planer hat cam1 auf Eingang 3 umgehaengt und umbenannt. Das ist
        # seine Entscheidung und muss durchkommen -- sonst waere der Merge
        # ein Schreibschutz statt einer Zusammenfuehrung.
        out = merge_tally_config(planer_datei(), pi_config())
        by_id = {d["id"]: d for d in out["devices"]}
        self.assertEqual(by_id["cam1"]["input"], 3)
        self.assertEqual(by_id["cam1"]["name"], "Kamera 1 (Havarie)")

    def test_mitgeschickter_leerer_wert_gewinnt(self):
        # Absent heisst "weiss ich nicht". Wer wirklich loeschen will, schickt
        # den Schluessel mit -- sonst gaebe es keinen Weg zurueck.
        neu = {"atem_ip": "", "devices": [{"id": "cam1", "name": "K1", "input": 1, "out_gpio": None}]}
        out = merge_tally_config(neu, pi_config())
        self.assertEqual(out["atem_ip"], "")
        self.assertIsNone(out["devices"][0]["out_gpio"])

    def test_neues_geraet_bekommt_nichts_erfunden(self):
        neu = {"devices": [{"id": "cam9", "name": "Kamera 9", "input": 9}]}
        out = merge_tally_config(neu, pi_config())
        self.assertEqual(len(out["devices"]), 1)
        self.assertNotIn("out_gpio", out["devices"][0])

    def test_der_poster_besitzt_die_geraeteliste(self):
        # cam2 fehlt im Post -- es verschwindet. Der Poster definiert die
        # Rollen; alles andere waere eine Liste, die nur noch waechst.
        neu = {"devices": [{"id": "cam1", "name": "Kamera 1", "input": 1}]}
        out = merge_tally_config(neu, pi_config())
        self.assertEqual([d["id"] for d in out["devices"]], ["cam1"])

    def test_ohne_alten_stand_bleibt_alles_wie_gepostet(self):
        neu = planer_datei()
        self.assertEqual(merge_tally_config(neu, {})["devices"], neu["devices"])
        self.assertEqual(merge_tally_config(neu, None)["devices"], neu["devices"])

    def test_geraete_ohne_id_fallen_nicht_auf_die_nase(self):
        neu = {"devices": [{"name": "ohne id", "input": 1}, "kaputt"]}
        out = merge_tally_config(neu, pi_config())
        self.assertEqual(len(out["devices"]), 1)

    def test_der_alte_stand_wird_nicht_veraendert(self):
        alt = pi_config()
        merge_tally_config(planer_datei(), alt)
        self.assertEqual(alt["devices"][0]["name"], "Kamera 1")
        self.assertEqual(alt["devices"][0]["input"], 1)


if __name__ == "__main__":
    unittest.main()
