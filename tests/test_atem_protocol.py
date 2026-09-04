"""Tests fuer das ATEM-UDP-Protokoll in `atem_watcher.py`.

WARUM AUSGERECHNET HIER. Das sind reine Byte-Bauer fuer ein Binaerprotokoll:
ein falsches Offset macht lautlos die falsche Kamera zum Programm, und der
Socket zeigt weiter "verbunden". Genau das ist hier schon einmal passiert --
der Docstring von `_atem_header` haelt es fest:

    "A previous version put local_pkt_id at offset 6..7. That kept the
     session alive (the switcher tolerates the unknown field) but broke
     reliable delivery of command packets -- the switcher couldn't track
     our sequence and so CAuS/CPgI/CPvI silently did nothing despite the
     socket showing 'connected'. Use this helper everywhere so the layout
     stays consistent."

Diese Regel stand bis hierher nur im Kommentar. Die CI dieses Repos fuhr
`python -m compileall` und `bash -n` -- Syntax, sonst nichts. Ein
zurueckgedrehtes Offset waere gruen durchgelaufen.

Lauf: `python3 -m unittest discover -s tests -v`  (keine Abhaengigkeiten).
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import atem_watcher as aw  # noqa: E402


def header_fields(pkt: bytes):
    """Die sechs 16-Bit-Felder des ATEM-Headers, wie sie auf dem Draht stehen."""
    return struct.unpack_from(">HHHHHH", pkt, 0)


class AtemHeaderLayout(unittest.TestCase):
    """Die Feldbelegung, die der Docstring als nicht verhandelbar beschreibt."""

    def test_local_pkt_id_steht_auf_offset_10_nicht_6(self):
        # Der dokumentierte Rueckfall: local_pkt_id auf 6..7 statt 10..11.
        pkt = aw._atem_header(0x080C, session_id=0x1234, local_pkt_id=0x00AB)
        f = header_fields(pkt)
        self.assertEqual(f[5], 0x00AB, "local_pkt_id gehoert auf Offset 10..11")
        self.assertEqual(f[3], 0x0000, "Offset 6..7 ist reserviert und MUSS 0 sein")

    def test_session_und_ack_stehen_wo_sie_hingehoeren(self):
        pkt = aw._atem_header(0x080C, session_id=0xBEEF, local_pkt_id=1, ack_pkt_id=0x0042)
        f = header_fields(pkt)
        self.assertEqual(f[1], 0xBEEF, "session_id auf Offset 2..3")
        self.assertEqual(f[2], 0x0042, "ack_pkt_id auf Offset 4..5")

    def test_reservierte_felder_bleiben_null(self):
        pkt = aw._atem_header(0x0818, session_id=0xFFFF, local_pkt_id=0xFFFF, ack_pkt_id=0xFFFF)
        f = header_fields(pkt)
        self.assertEqual((f[3], f[4]), (0, 0), "Offsets 6..9 sind reserviert")

    def test_header_ist_zwoelf_byte(self):
        self.assertEqual(len(aw._atem_header(0x080C, 1, 1)), 12)


class PaketLaengen(unittest.TestCase):
    """Die unteren 11 Bit des ersten Feldes sind die Paketlaenge -- sie muss
    zur tatsaechlichen Byte-Zahl passen, sonst verwirft der Mischer das Paket."""

    def pruefe(self, pkt: bytes, name: str):
        codiert = header_fields(pkt)[0] & 0x07FF
        self.assertEqual(codiert, len(pkt), f"{name}: Laengenfeld {codiert} != {len(pkt)} Bytes")

    def test_hello(self):
        self.pruefe(aw.make_hello(), "make_hello")

    def test_ack(self):
        self.pruefe(aw.make_ack(0x1234, 7), "make_ack")

    def test_ping(self):
        self.pruefe(aw.make_ping(0x1234, 7), "make_ping")

    def test_caus(self):
        self.pruefe(aw.make_caus(0x1234, 7, 0, 5), "make_caus")

    def test_cpgi(self):
        self.pruefe(aw.make_cpgi(0x1234, 7, 0, 5), "make_cpgi")

    def test_cpvi(self):
        self.pruefe(aw.make_cpvi(0x1234, 7, 0, 5), "make_cpvi")


class KommandoPakete(unittest.TestCase):
    """Die drei Kommandos, mit denen der Pi den Mischer tatsaechlich schaltet."""

    def test_alle_drei_nutzen_den_gemeinsamen_header(self):
        # Der Docstring verlangt "use this helper everywhere". Pruefbar ist es
        # ueber das Ergebnis: die ersten 12 Bytes muessen Byte fuer Byte dem
        # entsprechen, was der Helfer liefert.
        erwartet = aw._atem_header(0x0818, 0x1234, 9)
        for name, pkt in (
            ("CAuS", aw.make_caus(0x1234, 9, 0, 5)),
            ("CPgI", aw.make_cpgi(0x1234, 9, 0, 5)),
            ("CPvI", aw.make_cpvi(0x1234, 9, 0, 5)),
        ):
            self.assertEqual(pkt[:12], erwartet, f"{name} baut seinen Header selbst")

    def test_kommandonamen_stehen_auf_offset_16(self):
        self.assertEqual(aw.make_caus(1, 1, 0, 5)[16:20], b"CAuS")
        self.assertEqual(aw.make_cpgi(1, 1, 0, 5)[16:20], b"CPgI")
        self.assertEqual(aw.make_cpvi(1, 1, 0, 5)[16:20], b"CPvI")

    def test_quelle_kommt_als_big_endian_am_ende(self):
        # Input 4711 passt nicht in ein Byte -- eine Endianness-Verwechslung
        # oder eine 8-Bit-Kuerzung faellt hier sofort auf.
        for bauer in (aw.make_cpgi, aw.make_cpvi):
            pkt = bauer(1, 1, 0, 4711)
            self.assertEqual(struct.unpack_from(">H", pkt, 22)[0], 4711, bauer.__name__)

    def test_me_index_wird_uebernommen(self):
        # CPgI/CPvI: ME-Index (0-basiert) steht auf Offset 20.
        self.assertEqual(aw.make_cpgi(1, 1, 0, 5)[20], 0)
        self.assertEqual(aw.make_cpgi(1, 1, 1, 5)[20], 1)

    def test_caus_setzt_die_maske_und_den_aux_index(self):
        pkt = aw.make_caus(1, 1, 3, 5)
        self.assertEqual(pkt[20], 0x01, "CAuS-Maske 0x01 = 'Quelle setzen'")
        self.assertEqual(pkt[21], 3, "Aux-Index (0-basiert)")

    def test_hello_traegt_die_syn_flagge(self):
        self.assertEqual(header_fields(aw.make_hello())[0] >> 11, 0x02, "SYN")

    def test_ack_traegt_die_ack_flagge(self):
        self.assertEqual(header_fields(aw.make_ack(1, 1))[0] >> 11, 0x10, "ACK")

    def test_ping_und_kommandos_sind_reliable(self):
        self.assertEqual(header_fields(aw.make_ping(1, 1))[0] >> 11, 0x01)
        self.assertEqual(header_fields(aw.make_caus(1, 1, 0, 1))[0] >> 11, 0x01)


class KommandoParser(unittest.TestCase):
    """`parse_commands` ist der Empfangsweg -- hierueber kommt der PGM/PVW-Zustand."""

    def test_liest_zurueck_was_die_bauer_schreiben(self):
        pkt = aw.make_cpgi(0x1234, 1, 0, 5)
        self.assertEqual([n for n, _ in aw.parse_commands(pkt)], ["CPgI"])

    def test_findet_mehrere_bloecke_hintereinander(self):
        kopf = aw._atem_header(0x0818, 1, 1)
        block = lambda name: struct.pack(">HH4sBBH", 12, 0, name, 0, 0, 7)  # noqa: E731
        pkt = kopf + block(b"PrgI") + block(b"PrvI")
        self.assertEqual([n for n, _ in aw.parse_commands(pkt)], ["PrgI", "PrvI"])

    def test_bricht_bei_abgeschnittenem_paket_ab_statt_zu_werfen(self):
        # Ein halbes Paket vom Netz darf den Watcher nicht umbringen.
        pkt = aw.make_cpgi(1, 1, 0, 5)[:18]
        self.assertEqual(list(aw.parse_commands(pkt)), [])

    def test_bricht_bei_unsinniger_laenge_ab(self):
        # cmd_len < 8 waere eine Endlosschleife, wenn es nicht abgefangen wird.
        pkt = aw._atem_header(0x0818, 1, 1) + struct.pack(">HH4s", 0, 0, b"XXXX")
        self.assertEqual(list(aw.parse_commands(pkt)), [])

    def test_leeres_paket_liefert_nichts(self):
        self.assertEqual(list(aw.parse_commands(b"")), [])
        self.assertEqual(list(aw.parse_commands(aw.make_ping(1, 1))), [])


if __name__ == "__main__":
    unittest.main()
