"""Tests fuer `bezzera-duo/tools/duo_sniff.py`, das Auswerteskript fuer
Mitschnitte der Leitung Mainboard <-> Display der Bezzera Duo.

Die Gicar-Beispiele stammen aus der Protokolldoku von antondlr/gicar-serial
(Ascaso Baby T, Gicar 3d5): `w005600010164` schaltet dort den Dampfkessel
ein, `w00560001OK9D` ist die Quittung. Ob die Duo dasselbe spricht, ist
offen. Diese Tests pruefen nur, dass das Werkzeug es erkennt, WENN sie es tut.

Lauf: `python3 -m unittest discover -s tests`.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bezzera-duo" / "tools"))

import duo_sniff as ds  # noqa: E402


def zeile(t, r, daten: bytes) -> str:
    return f"{t} {r} " + " ".join(f"{b:02x}" for b in daten)


class GicarFormat(unittest.TestCase):
    def test_bekannte_beispiele_haben_gueltige_pruefsumme(self):
        for text in ("w005600010164", "w00560001OK9D"):
            g = ds.parse_gicar(text)
            self.assertIsNotNone(g, text)
            self.assertTrue(g.pruefsumme_ok, text)
            self.assertEqual(g.offset, 0x56)
            self.assertEqual(g.laenge, 1)

    def test_pruefsumme_anhaengen(self):
        self.assertEqual(ds.gicar_checksumme("w0056000101"), "w005600010164")

    def test_falsche_pruefsumme_wird_erkannt(self):
        self.assertFalse(ds.parse_gicar("w005600010165").pruefsumme_ok)

    def test_binaerdaten_sind_kein_gicar(self):
        self.assertIsNone(ds.parse_gicar("\x80\x11\x17\x00\x28"))


class GicarSpeicherabbild(unittest.TestCase):
    def test_aenderung_zwischen_zwei_lesungen_wird_gemeldet(self):
        a = ds.gicar_checksumme("r00350002" + "A703")  # 0x03A7 = 935 -> 93.5 Grad
        b = ds.gicar_checksumme("r00350002" + "AE03")  # 0x03AE = 942 -> 94.2 Grad
        log = "\n".join(
            [zeile(100, "A", a.encode()), "# am Display 93.5 -> 94.2", zeile(900, "A", b.encode())]
        )
        out = io.StringIO()
        speicher = ds.cmd_gicar(list(ds.lese_log(log.splitlines())), out=out)
        self.assertEqual(speicher[0x35], 0xAE)
        self.assertEqual(speicher[0x36], 0x03)
        text = out.getvalue()
        self.assertIn("0x0035 (  53): a7 -> ae", text)
        self.assertIn("-- am Display 93.5 -> 94.2", text)
        self.assertNotIn("0x0036", text)  # das hohe Byte blieb 03

    def test_schreibbefehl_setzt_speicher(self):
        log = zeile(5, "B", b"w005600010164")
        out = io.StringIO()
        speicher = ds.cmd_gicar(list(ds.lese_log([log])), out=out)
        self.assertEqual(speicher[0x56], 0x01)
        self.assertIn("SCHREIBT @0x0056", out.getvalue())


class Pruefsummen(unittest.TestCase):
    def test_lelit_bianca_mod128_wird_gefunden(self):
        # Paket Display -> Steuerplatine aus magnusnordlander/lelit-bianca-protocol
        treffer = dict(ds.pruefsummen_treffer([ds.Frame(0, "A", bytes.fromhex("8011170028"))]))
        self.assertEqual(treffer["sum7 (mod 128)"], 1.0)

    def test_gicar_ascii_wird_als_ascii_hex_sum_erkannt(self):
        fs = [ds.Frame(i, "A", ds.gicar_checksumme(f"r{i:04X}0001{i:02X}").encode()) for i in range(10)]
        beste, quote = ds.pruefsummen_treffer(fs)[0]
        self.assertEqual(beste, "ascii-hex sum8 (Gicar 3d5)")
        self.assertEqual(quote, 1.0)

    def test_crc16_modbus_referenzwert(self):
        # Standard-Pruefwert fuer CRC-16/MODBUS ueber "123456789"
        self.assertEqual(ds._crc16_modbus(b"123456789"), 0x4B37)


class LogEinlesen(unittest.TestCase):
    def test_bootmeldungen_werden_uebersprungen(self):
        zeilen = ["ets Jun  8 2016 00:22:57", "", "12 A 80 11", "# Markierung", "13 b 81"]
        e = list(ds.lese_log(zeilen))
        self.assertEqual(len(e), 3)
        self.assertEqual(e[0].daten, b"\x80\x11")
        self.assertEqual(e[1], "Markierung")
        self.assertEqual(e[2].richtung, "B")


class Diff(unittest.TestCase):
    def test_nur_die_veraenderliche_position_erscheint(self):
        log = [zeile(t, "A", bytes([0x81, 0x00, v, 0x7F])) for t, v in ((0, 0x5D), (10, 0x5D), (20, 0x5C))]
        out = io.StringIO()
        verlauf = ds.cmd_diff(list(ds.lese_log(log)), "A", None, None, out=out)
        self.assertEqual(verlauf, {2: [(20, 0x5C)]})
        self.assertIn("[0]=81", out.getvalue())


class Stats(unittest.TestCase):
    def test_ascii_protokoll_wird_benannt(self):
        fs = [ds.Frame(i * 100, "A", ds.gicar_checksumme("r000500D7").encode()) for i in range(5)]
        out = io.StringIO()
        ds.cmd_stats(fs, out=out)
        self.assertIn("vermutlich ASCII-Protokoll", out.getvalue())
        self.assertIn("ascii-hex sum8", out.getvalue())


if __name__ == "__main__":
    unittest.main()
