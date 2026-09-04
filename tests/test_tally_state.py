"""Tests fuer `tally_state_for_device` in `guide_server.py`.

Diese Funktion entscheidet, ob die Tally-Lampe einer Kamera ROT zeigt. Sie ist
damit die sicherheitsrelevanteste reine Funktion im Repo: wer vor einer dunklen
Lampe steht, verhaelt sich, als sei er nicht auf Sendung.

Zwei Stellen darin sind besonders leicht kaputtzumachen:

1. **Die ME-Zaehlweise dreht sich.** `atem_watcher` schreibt die Busse als
   `{"<me>": input}` mit **0-basiertem** ME, `device.me` in der Konfiguration
   ist **1-basiert**. Der Code rechnet `int(k) == (me - 1)`. Ein vergessenes
   `- 1` legt die Lampe auf den falschen ME -- und das faellt im Betrieb erst
   auf, wenn zwei Busse unterschiedliche Kameras fahren.

2. **Ein unbekannter Zustand darf nicht wie "sicher" aussehen.** Ohne
   Verbindung liefert die Funktion `offline`, nicht `safe`.

Die Funktion nimmt `atem_state` als Parameter -- sie ist also ohne Mischer,
ohne Netz und ohne Hardware pruefbar. Bis hierher tat das niemand: die CI fuhr
`python -m compileall` und `bash -n`.

Lauf: `python3 -m unittest discover -s tests -v`  (keine Abhaengigkeiten).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_server as gs  # noqa: E402


def state(connected=True, pgm=None, pvw=None, aux=None):
    return {
        "connected": connected,
        "pgm": pgm if pgm is not None else {},
        "pvw": pvw if pvw is not None else {},
        "aux": aux if aux is not None else {},
    }


CFG = {
    "devices": [
        {"id": "cam1", "input": 1, "me": 1, "aux": []},
        {"id": "cam2", "input": 2, "me": 1, "aux": [1]},
        {"id": "cam3", "input": 3, "me": 2, "aux": []},
        {"id": "ohneInput", "me": 1},
    ]
}


class OhneVerbindung(unittest.TestCase):
    def test_ohne_verbindung_offline_nicht_safe(self):
        # Der wichtigste Fall: unbekannt ist nicht dasselbe wie "nicht auf
        # Sendung". Wer hier `safe` zurueckgaebe, liesse die Lampe dunkel,
        # obwohl niemand weiss, was der Mischer gerade macht.
        s = state(connected=False, pgm={"0": 1})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "offline")

    def test_fehlendes_connected_feld_gilt_als_offline(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", {}), "offline")


class GrundzustaendeEinesGeraets(unittest.TestCase):
    def test_auf_programm(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", state(pgm={"0": 1})), "pgm")

    def test_auf_vorschau(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", state(pvw={"0": 1})), "pvw")

    def test_programm_schlaegt_vorschau(self):
        s = state(pgm={"0": 1}, pvw={"0": 1})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "pgm")

    def test_weder_noch_ist_safe(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", state(pgm={"0": 9})), "safe")

    def test_unbekanntes_geraet_ist_unknown_nicht_safe(self):
        # "safe" waere eine Aussage ueber ein Geraet, das die Konfiguration
        # gar nicht kennt.
        self.assertEqual(gs.tally_state_for_device(CFG, "gibtsNicht", state()), "unknown")

    def test_geraet_ohne_input_ist_safe(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "ohneInput", state(pgm={"0": 1})), "safe")


class MeZaehlweise(unittest.TestCase):
    """0-basiert im Watcher, 1-basiert in der Konfiguration."""

    def test_me1_liest_schluessel_null(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", state(pgm={"0": 1})), "pgm")

    def test_me2_liest_schluessel_eins(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam3", state(pgm={"1": 3})), "pgm")

    def test_der_falsche_bus_faerbt_nicht(self):
        # cam3 haengt an ME 2. Steht ihr Input auf ME 1 im Programm, bleibt
        # ihre Lampe dunkel -- genau das trennt die beiden Busse.
        self.assertEqual(gs.tally_state_for_device(CFG, "cam3", state(pgm={"0": 3})), "safe")

    def test_und_umgekehrt(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", state(pgm={"1": 1})), "safe")

    def test_ohne_me_gilt_bus_eins(self):
        cfg = {"devices": [{"id": "x", "input": 4}]}
        self.assertEqual(gs.tally_state_for_device(cfg, "x", state(pgm={"0": 4})), "pgm")


class AuxAlsProgramm(unittest.TestCase):
    """Ein Aux-Ausgang, der die Kamera fuehrt, zaehlt als 'auf Sendung'."""

    def test_beobachteter_aux_macht_pgm(self):
        s = state(pgm={"0": 9}, aux={"1": 2})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam2", s), "pgm")

    def test_nicht_beobachteter_aux_faerbt_nicht(self):
        # cam1 beobachtet keinen Aux, obwohl Aux 1 ihren Input fuehrt.
        s = state(pgm={"0": 9}, aux={"1": 1})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "safe")

    def test_aux_mit_anderer_quelle_faerbt_nicht(self):
        s = state(pgm={"0": 9}, aux={"1": 7})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam2", s), "safe")

    def test_aux_schlaegt_auch_die_vorschau(self):
        s = state(pvw={"0": 2}, aux={"1": 2})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam2", s), "pgm")


class AdHocGeraet(unittest.TestCase):
    """Eine rein numerische Id in der URL ist ein Geraet auf ME 1."""

    def test_zahl_als_id_wird_zum_input(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "5", state(pgm={"0": 5})), "pgm")

    def test_adhoc_ohne_treffer_ist_safe(self):
        self.assertEqual(gs.tally_state_for_device(CFG, "5", state(pgm={"0": 6})), "safe")

    def test_konfiguriertes_geraet_schlaegt_die_adhoc_deutung(self):
        cfg = {"devices": [{"id": "7", "input": 1, "me": 1}]}
        # Id "7" ist konfiguriert und zeigt auf Input 1 -- nicht auf Input 7.
        self.assertEqual(gs.tally_state_for_device(cfg, "7", state(pgm={"0": 1})), "pgm")
        self.assertEqual(gs.tally_state_for_device(cfg, "7", state(pgm={"0": 7})), "safe")


class AltesBusFormat(unittest.TestCase):
    """Aeltere Watcher schrieben Listen statt Dicts -- beides muss gelten."""

    def test_liste_wird_verstanden(self):
        s = state(pgm=[{"me": 1, "input": 1}])
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "pgm")

    def test_liste_trennt_die_busse_ebenfalls(self):
        # In der Listenform ist `me` 1-basiert (kein `- 1`).
        s = state(pgm=[{"me": 2, "input": 3}])
        self.assertEqual(gs.tally_state_for_device(CFG, "cam3", s), "pgm")
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "safe")

    def test_kaputte_eintraege_werfen_nicht(self):
        s = state(pgm={"nicht-numerisch": 1, "0": "auch-kein-int"})
        self.assertEqual(gs.tally_state_for_device(CFG, "cam1", s), "safe")


if __name__ == "__main__":
    unittest.main()
