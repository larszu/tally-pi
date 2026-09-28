"""Was ein Numato-Kanal elektrisch kann, steht nicht im Protokoll.

Das 32-Kanal-Board treibt auf IO0..7 und IO16..20 nur 2 mA — kein Relaismodul,
keine LED. Eingaenge haben keinen internen Pull-up. Beides sieht die Software
nicht; sie kann es nur wissen und sagen. Diese Datei prueft, dass sie es sagt:
als Saetze in `numato.json`, nicht als Sperre.

Lauf: `python3 -m unittest discover -s tests`.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import NumatoFake  # noqa: E402
import numato_io  # noqa: E402
import numato_watcher as nw  # noqa: E402


class Kanalgruppen(unittest.TestCase):
    def test_tabelle_deckt_32_kanaele(self):
        self.assertEqual(len(numato_io.TREIBERSTROM_MA_32), 32)
        self.assertEqual(numato_io.treiberstrom_ma(3, 32), 2)
        self.assertEqual(numato_io.treiberstrom_ma(12, 32), 25)
        self.assertEqual(numato_io.treiberstrom_ma(18, 32), 2)
        self.assertEqual(numato_io.treiberstrom_ma(27, 32), 8)

    def test_andere_breite_behauptet_nichts(self):
        self.assertIsNone(numato_io.treiberstrom_ma(3, 16))
        self.assertEqual(numato_io.kanal_hinweise([3], [], 16), [])

    def test_ausgang_auf_2ma_kanal_wird_genannt(self):
        h = numato_io.kanal_hinweise([3, 12], [], 32)
        self.assertEqual(len(h), 1)
        self.assertIn("CH 3", h[0])
        self.assertIn("2 mA", h[0])
        self.assertNotIn("CH 12", h[0])

    def test_eingang_bekommt_pullup_hinweis(self):
        h = numato_io.kanal_hinweise([], [5], 32)
        self.assertEqual(len(h), 1)
        self.assertIn("CH 5", h[0])
        self.assertIn("Pull-up", h[0])

    def test_ohne_belegung_kein_hinweis(self):
        self.assertEqual(numato_io.kanal_hinweise([], [], 32), [])


class ImWatcher(unittest.TestCase):
    def test_attach_legt_hinweise_ab(self):
        fake = NumatoFake(breite=32)
        board = numato_io.NumatoBoard(fake)
        mgr = nw.NumatoManager()
        mgr.attach(board, "fake", output_channels=[3, 12], input_channels=[5])
        self.assertEqual(len(mgr.hinweise), 2)
        self.assertTrue(any("CH 3" in h for h in mgr.hinweise))
        self.assertTrue(any("Pull-up" in h for h in mgr.hinweise))

    def test_saubere_belegung_ohne_hinweis(self):
        fake = NumatoFake(breite=32)
        mgr = nw.NumatoManager()
        mgr.attach(numato_io.NumatoBoard(fake), "fake",
                   output_channels=[12, 13], input_channels=[])
        self.assertEqual(mgr.hinweise, [])


if __name__ == "__main__":
    unittest.main()
