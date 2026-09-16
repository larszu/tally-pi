"""
Spricht `numato_io.NumatoBoard` das Numato-Protokoll richtig?

Kein echtes Board noetig: `NumatoFake` (in `hilfe.py`) beantwortet dieselben
Zeilen wie die Platine. Geprueft wird das, woran ein Tippfehler das Board
stumm liesse — die Kanal-Zeichen oberhalb von 9, die Hex-Breite, und dass ein
gesetzter Ausgang beim Zuruecklesen auch gesetzt ist.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import NumatoFake  # noqa: E402
import numato_io  # noqa: E402


class KanalZeichen(unittest.TestCase):
    def test_ziffern_und_buchstaben(self):
        f = numato_io._kanal_zeichen
        self.assertEqual(f(0), "0")
        self.assertEqual(f(9), "9")
        self.assertEqual(f(10), "A")
        self.assertEqual(f(31), "V")

    def test_ausserhalb_wirft(self):
        with self.assertRaises(ValueError):
            numato_io._kanal_zeichen(32)


class BoardSprichtProtokoll(unittest.TestCase):
    def setUp(self):
        self.fake = NumatoFake(breite=32)
        self.board = numato_io.NumatoBoard(self.fake)

    def test_ver_ist_lebenszeichen(self):
        self.assertIn("00000008", self.board.ver())

    def test_readall_setzt_breite(self):
        self.fake.set_input(3, True)
        wert = self.board.read_all()
        self.assertEqual(self.board.breite, 32)
        self.assertTrue((wert >> 3) & 1)

    def test_readall_kuerzeres_board(self):
        # Ein 8-Kanal-Board antwortet mit 2 Hex-Zeichen — die Breite darf
        # nicht auf 32 festgenagelt sein.
        fake8 = NumatoFake(breite=8)
        board8 = numato_io.NumatoBoard(fake8)
        fake8.set_input(1, True)
        board8.read_all()
        self.assertEqual(board8.breite, 8)

    def test_set_und_clear_einzeln(self):
        self.board.read_all()  # Breite lernen
        self.board.set_iomask((1 << 32) - 1)
        self.board.set_iodir(0)  # alles Ausgang
        self.board.set_channel(17)
        self.assertTrue(self.fake.get_output(17))
        self.board.clear_channel(17)
        self.assertFalse(self.fake.get_output(17))

    def test_set_channel_ueber_neun(self):
        # Kanal 20 muss als 'K' gehen, nicht als "20".
        self.board.read_all()
        self.board.set_iomask((1 << 32) - 1)
        self.board.set_iodir(0)
        self.board.set_channel(20)
        self.assertTrue(self.fake.get_output(20))

    def test_writeall_respektiert_iodir(self):
        self.board.read_all()
        self.board.set_iomask((1 << 32) - 1)
        # Kanal 5 Ausgang, Rest Eingang.
        self.board.set_iodir(((1 << 32) - 1) & ~(1 << 5))
        self.board.write_all((1 << 5) | (1 << 6))
        self.assertTrue(self.fake.get_output(5))
        # Kanal 6 ist Eingang -> writeall darf ihn nicht als Ausgang fuehren.
        self.assertNotIn(6, self.fake.outputs)


if __name__ == "__main__":
    unittest.main()
