"""
Der Numato-Watcher: aus Konfiguration werden Ein-/Ausgaenge, ein Tastendruck
wird ein ATEM-Befehl, ein Ausgabe-Befehl setzt den richtigen Kanal.

Kein echtes Board, kein echter Mischer: `NumatoFake` beantwortet das
Protokoll, und der ATEM-Befehl wird abgefangen, statt ihn zu schicken. So
prueft der Test die VERDRAHTUNG — findet der Knopf den Mischer, findet die
Lampe den Kanal — ohne Hardware.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import NumatoFake, temp_verzeichnis  # noqa: E402
import numato_io  # noqa: E402
import numato_watcher as nw  # noqa: E402


class KonfigWirdZuEinUndAusgang(unittest.TestCase):
    def test_geraete_werden_kanaele(self):
        with temp_verzeichnis() as tmp:
            tally = Path(tmp) / "tally.json"
            tally.write_text(json.dumps({"devices": [
                {"id": "cam1", "name": "Kamera 1", "input": 1,
                 "out_gpio": 17,
                 "in_gpio": 5, "in_action_type": "atem_pgm", "in_atem_source": 2},
                {"id": "cam2", "name": "Kamera 2", "input": 2,
                 "out_gpio": 22},
            ]}))
            self._mit_config(tally, None, self._pruefe)

    def _mit_config(self, tally, bindings, fn):
        alt_t, alt_b = nw.TALLY_CONFIG, nw.BINDINGS
        nw.TALLY_CONFIG = tally
        nw.BINDINGS = bindings or (tally.parent / "bindings.json")
        try:
            fn()
        finally:
            nw.TALLY_CONFIG, nw.BINDINGS = alt_t, alt_b

    def _pruefe(self):
        bindings, outputs = nw.load_config()
        self.assertEqual(outputs, [17, 22])
        # Genau ein Eingang (cam1), als Kanal 5, ATEM-PGM auf Quelle 2.
        self.assertEqual(len(bindings), 1)
        b = bindings[0]
        self.assertEqual(b["channel"], 5)
        self.assertEqual(b["action"]["kind"], "atem_pgm")
        self.assertEqual(b["action"]["source"], 2)


class AusgangSetztDenRichtigenKanal(unittest.TestCase):
    def setUp(self):
        self.fake = NumatoFake(breite=32)
        self.board = numato_io.NumatoBoard(self.fake)
        self.mgr = nw.NumatoManager()
        self.mgr.attach(self.board, "fake", output_channels=[17, 22],
                        input_channels=[5])

    def test_configure_setzt_iodir_und_aus(self):
        # Kanal 17 und 22 sind Ausgang (iodir-Bit 0), der Rest Eingang.
        self.assertEqual((self.fake.iodir >> 17) & 1, 0)
        self.assertEqual((self.fake.iodir >> 22) & 1, 0)
        self.assertEqual((self.fake.iodir >> 5) & 1, 1)
        # Ausgaenge starten aus.
        self.assertFalse(self.fake.get_output(17))

    def test_set_und_dedupe(self):
        ok, _ = self.mgr.set_output(17, True)
        self.assertTrue(ok)
        self.assertTrue(self.fake.get_output(17))
        # Zweites Mal mit gleichem Wert: nichts Neues, aber ok.
        ok, msg = self.mgr.set_output(17, True)
        self.assertTrue(ok)
        self.assertEqual(msg, "unveraendert")
        self.mgr.set_output(17, False)
        self.assertFalse(self.fake.get_output(17))

    def test_handle_cmd_von_aussen(self):
        antwort = self.mgr.handle_cmd({"cmd": "set", "channel": 22, "on": True})
        self.assertTrue(antwort["ok"])
        self.assertTrue(self.fake.get_output(22))

    def test_ohne_board_ehrlicher_grund(self):
        self.mgr.detach()
        ok, msg = self.mgr.set_output(17, True)
        self.assertFalse(ok)
        self.assertIn("kein Numato", msg)


class TastendruckFindetDenMischer(unittest.TestCase):
    def test_fallende_flanke_schaltet_pgm(self):
        gefangen = []
        alt = nw.atem_cmd
        nw.atem_cmd = lambda cmd: gefangen.append(cmd)
        try:
            b = {"channel": 5, "trigger_edge": "falling",
                 "action": {"kind": "atem_pgm", "source": 2, "me": 1},
                 "_label": "Kamera 1"}
            nw._dispatch(b, "falling", {})
        finally:
            nw.atem_cmd = alt
        self.assertEqual(gefangen, [{"cmd": "set_program", "me": 1, "source": 2}])


if __name__ == "__main__":
    unittest.main()
