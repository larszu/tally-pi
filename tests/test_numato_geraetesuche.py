"""
Findet der Numato-Watcher sein Modul auch auf Windows und macOS?

─── DIE MELDUNG DAHINTER (Nutzer, 2026-09-15) ──────────────────────────────

„tally pi muss auch auf windows und mac laufen und dort als gpio interface
einen numato gpio usb benutzen koennen."

─── WAS VORHER GEMESSEN WURDE ──────────────────────────────────────────────

Die Geraetesuche war LINUX-ONLY (`/dev/numato0` per udev, `/dev/ttyACM*` per
glob). Auf Windows (`COM3`) und macOS (`/dev/cu.usbmodem…`) fand sie nichts,
und zwar lautlos. Jetzt sucht `numato_io` ueber `serial.tools.list_ports`,
das alle drei Systeme kennt.

DIESE TESTS ZOGEN MIT DER LOGIK UM: die Geraetesuche liegt seit der
Zusammenfuehrung in `numato_io` (`finde_geraete`/`finde_geraet`), nicht mehr
im Watcher. Geprueft wird gegen eine gefaelschte `list_ports.comports()` —
echte Hardware gibt es in CI nicht; gefragt ist, ob die REIHENFOLGE stimmt
und ob ein Windows-/macOS-Name ueberhaupt in der Liste landet. Ob ein Modul
dann antwortet, entscheidet `probe` am echten Port.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numato_io  # noqa: E402


def _port(device, vid=None):
    return SimpleNamespace(device=device, vid=vid)


class NumatoGeraetesuche(unittest.TestCase):
    def setUp(self):
        # KEIN `skipTest`, wenn pyserial fehlt: die Aufzaehlungs-Logik in
        # `numato_io` braucht die Bibliothek nicht, nur die gefaelschte
        # `list_ports` unten. `numato_io` bricht beim Import nicht ab.
        self.io = numato_io

    def _mocks(self, ports, exists=None):
        """list_ports.comports -> ports; os.path.exists -> exists; glob leer.

        `glob` wird geleert, damit auf einem Linux-Laeufer kein echtes
        `/dev/ttyACM*` in die Liste sickert.
        """
        existiert = (lambda p: False) if exists is None else exists
        lp = mock.MagicMock()
        lp.comports.return_value = ports
        return (mock.patch.object(self.io, "list_ports", lp),
                mock.patch.object(self.io.os.path, "exists", side_effect=existiert),
                mock.patch.object(self.io.glob, "glob", return_value=[]))

    def _finde(self, ports, exists=None):
        a, b, c = self._mocks(ports, exists)
        with a, b, c:
            return self.io.finde_geraete()

    # ── Windows ───────────────────────────────────────────────────────────
    def test_windows_com_port_steht_in_der_liste(self):
        self.assertEqual(self._finde([_port("COM3"), _port("COM7")]),
                         ["COM3", "COM7"])

    def test_windows_numato_vid_kommt_zuerst(self):
        ports = [_port("COM3"), _port("COM9", vid=self.io.NUMATO_VID), _port("COM7")]
        self.assertEqual(self._finde(ports), ["COM9", "COM3", "COM7"])

    # ── macOS ─────────────────────────────────────────────────────────────
    def test_macos_usbmodem_steht_in_der_liste(self):
        self.assertEqual(self._finde([_port("/dev/cu.usbmodem14201")]),
                         ["/dev/cu.usbmodem14201"])

    # ── Linux: der udev-Symlink bleibt vorn ────────────────────────────────
    def test_udev_symlink_schlaegt_alles(self):
        exists = lambda p: p == "/dev/numato0"
        self.assertEqual(
            self._finde([_port("/dev/ttyACM0", vid=self.io.NUMATO_VID)], exists),
            ["/dev/numato0", "/dev/ttyACM0"])

    def test_symlink_wird_nicht_doppelt_gelistet(self):
        exists = lambda p: p == "/dev/numato0"
        self.assertEqual(self._finde([_port("/dev/numato0")], exists),
                         ["/dev/numato0"])

    def test_aufzaehlung_darf_werfen_ohne_den_lauf_zu_beenden(self):
        exists = lambda p: p == "/dev/numato0"
        lp = mock.MagicMock()
        lp.comports.side_effect = OSError("kein Zugriff")
        with mock.patch.object(self.io, "list_ports", lp), \
             mock.patch.object(self.io.os.path, "exists", side_effect=exists), \
             mock.patch.object(self.io.glob, "glob", return_value=[]):
            self.assertEqual(self.io.finde_geraete(), ["/dev/numato0"])

    # ── finde_geraet(): Symlink ohne Probe, alles andere mit ────────────────
    def test_symlink_ohne_probe__fremder_port_mit_probe(self):
        exists = lambda p: p == "/dev/numato0"
        a, b, c = self._mocks([_port("COM3")], exists)
        with a, b, c, mock.patch.object(self.io, "probe") as probe:
            probe.return_value = False
            self.assertEqual(self.io.finde_geraet(), "/dev/numato0")
            probe.assert_not_called()

    def test_ein_port_der_antwortet_wird_genommen(self):
        a, b, c = self._mocks([_port("COM3"), _port("COM7")])
        with a, b, c, mock.patch.object(self.io, "probe",
                                        side_effect=lambda p: p == "COM7"):
            self.assertEqual(self.io.finde_geraet(), "COM7")

    def test_kein_geraet_ist_None_und_kein_Absturz(self):
        a, b, c = self._mocks([])
        with a, b, c, mock.patch.object(self.io, "probe", return_value=False):
            self.assertIsNone(self.io.finde_geraet())


class RunLocalKenntDenNumato(unittest.TestCase):
    """Der Watcher muss auch GESTARTET werden — und er wird es, automatisch.

    Nach der Zusammenfuehrung startet `run-local.py` den Numato-Watcher von
    selbst (ausser `--no-gpio`), nicht mehr nur auf `--numato`. Der Schalter
    bleibt anerkannt, damit vertraute Aufrufe nicht abbrechen.
    """

    def test_start_steht_drin(self):
        quelle = (ROOT / "run-local.py").read_text(encoding="utf-8")
        self.assertIn('starte("numato_watcher.py")', quelle)
        self.assertIn('"--numato"', quelle)

    def test_die_starter_reichen_die_schalter_durch(self):
        for name in ("start-local.bat", "start-local.command"):
            starter = ROOT / name
            self.assertTrue(starter.exists(), f"{name} fehlt")
            text = starter.read_text(encoding="utf-8", errors="replace")
            self.assertTrue("%*" in text or '"$@"' in text,
                            f"{name} reicht die Schalter nicht durch")
            self.assertIn("run-local.py", text)
        self.assertIn("py -3", (ROOT / "start-local.bat").read_text(
            encoding="utf-8", errors="replace"))

    def test_ohne_pyserial_wird_der_grund_ehrlich_gemeldet(self):
        # Frueher pruefte `run-local.py` pyserial vor dem Start und warnte.
        # Jetzt startet der Watcher automatisch und meldet den Grund SELBST —
        # ohne pyserial stirbt er nicht, sondern schreibt ihn nach
        # `numato.json` (und die Oberflaeche zeigt ihn). Geprueft wird also
        # der ehrliche Weg an der Stelle, an der er jetzt liegt.
        quelle = (ROOT / "numato_watcher.py").read_text(encoding="utf-8")
        self.assertIn("PYSERIAL_DA", quelle)
        self.assertIn("pip install pyserial", quelle)


if __name__ == "__main__":
    unittest.main()
