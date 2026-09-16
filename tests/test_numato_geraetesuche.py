"""
Findet der Numato-Watcher sein Modul auch auf Windows und macOS?

─── DIE MELDUNG DAHINTER (Nutzer, 2026-09-15) ──────────────────────────────

„tally pi muss auch auf windows und mac laufen und dort als gpio interface
einen numato gpio usb benutzen koennen."

─── WAS VORHER GEMESSEN WURDE ──────────────────────────────────────────────

Die Geraetesuche war LINUX-ONLY, an zwei Stellen zugleich:

    PREFERRED_DEVICES = ["/dev/numato0", "/dev/numato1"]   # udev-Symlink
    candidates = sorted(glob.glob("/dev/ttyACM*"))          # CDC-ACM

`/dev/numato0` legt eine udev-Regel an — udev gibt es nur unter Linux.
`/dev/ttyACM*` ist der Linux-Name fuer ein CDC-ACM-Geraet; dieselbe Hardware
heisst unter Windows `COM3` und auf einem Mac `/dev/cu.usbmodem14201`.

Auf beiden fand `find_device()` also nichts, und zwar LAUTLOS: sie gab
`None` zurueck, und der Zustand sagte „kein Numato gefunden" — was stimmte
und den Grund verschwieg. Wer das Modul an einen Mac steckte, bekam
dieselbe Meldung wie jemand ohne Modul.

Das ist nicht irgendein Weg, sondern DER EINZIGE: `gpio_watcher.py` braucht
`gpiod` und `/dev/gpiochip0`, beides gibt es auf einem Schreibtischrechner
nicht.

─── WAS DIESER LAUF PRUEFT ─────────────────────────────────────────────────

Die Aufzaehlung wird gegen eine gefaelschte `list_ports.comports()`
gefahren — echte Hardware gibt es in CI nicht, und darum geht es auch
nicht: gefragt ist, ob die REIHENFOLGE stimmt und ob ein Windows- bzw.
macOS-Name ueberhaupt in der Liste landet. Dass ein Modul dann antwortet,
entscheidet `probe_numato` am echten Port.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _port(device, vid=None):
    return SimpleNamespace(device=device, vid=vid)


class NumatoGeraetesuche(unittest.TestCase):
    def setUp(self):
        # KEIN `skipTest`, wenn pyserial fehlt — und das ist der Punkt.
        # Frueher beendete `numato_watcher` sich beim IMPORT, wenn pyserial
        # nicht da war; die Tests hier uebersprangen sich deshalb und waren
        # gruen, ohne etwas gemessen zu haben. Seit der Abbruch in `main()`
        # steht, laesst sich die Geraetesuche pruefen, wo immer Python laeuft
        # — sie ist reine Aufzaehlungs-Logik und braucht keine Bibliothek,
        # nur die gefaelschte `list_ports` unten.
        import numato_watcher
        self.nw = numato_watcher

    # ── Windows ───────────────────────────────────────────────────────────
    def test_windows_com_port_steht_in_der_liste(self):
        """`COM3` ist ein Kandidat — vorher fiel er durch jedes Muster."""
        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", return_value=False):
            lp.comports.return_value = [_port("COM3"), _port("COM7")]
            self.assertEqual(self.nw.kandidaten(), ["COM3", "COM7"])

    def test_windows_numato_vid_kommt_zuerst(self):
        """Die Hersteller-Kennung sortiert, sie schliesst nicht aus."""
        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", return_value=False):
            lp.comports.return_value = [
                _port("COM3"),
                _port("COM9", vid=self.nw.NUMATO_VID),
                _port("COM7"),
            ]
            self.assertEqual(self.nw.kandidaten(), ["COM9", "COM3", "COM7"])

    # ── macOS ─────────────────────────────────────────────────────────────
    def test_macos_usbmodem_steht_in_der_liste(self):
        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", return_value=False):
            lp.comports.return_value = [_port("/dev/cu.usbmodem14201")]
            self.assertEqual(self.nw.kandidaten(), ["/dev/cu.usbmodem14201"])

    # ── Linux: der udev-Symlink bleibt vorn ───────────────────────────────
    def test_udev_symlink_schlaegt_alles(self):
        """Auf einem eingerichteten Pi ist er die verlaessliche Zuordnung."""
        def existiert(p):
            return p == "/dev/numato0"

        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", side_effect=existiert):
            lp.comports.return_value = [_port("/dev/ttyACM0", vid=self.nw.NUMATO_VID)]
            self.assertEqual(
                self.nw.kandidaten(), ["/dev/numato0", "/dev/ttyACM0"]
            )

    def test_symlink_wird_nicht_doppelt_gelistet(self):
        """Zaehlt `list_ports` ihn mit auf, steht er trotzdem nur einmal da."""
        def existiert(p):
            return p == "/dev/numato0"

        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", side_effect=existiert):
            lp.comports.return_value = [_port("/dev/numato0")]
            self.assertEqual(self.nw.kandidaten(), ["/dev/numato0"])

    # ── Der Symlink wird NICHT geprobt, alles andere schon ────────────────
    def test_symlink_ohne_probe__fremder_port_mit_probe(self):
        def existiert(p):
            return p == "/dev/numato0"

        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", side_effect=existiert), \
             mock.patch.object(self.nw, "probe_numato") as probe:
            lp.comports.return_value = [_port("COM3")]
            probe.return_value = False
            self.assertEqual(self.nw.find_device(), "/dev/numato0")
            probe.assert_not_called()

    def test_ein_port_der_antwortet_wird_genommen(self):
        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", return_value=False), \
             mock.patch.object(self.nw, "probe_numato") as probe:
            lp.comports.return_value = [_port("COM3"), _port("COM7")]
            probe.side_effect = lambda p: p == "COM7"
            self.assertEqual(self.nw.find_device(), "COM7")

    def test_kein_geraet_ist_None_und_kein_Absturz(self):
        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", return_value=False), \
             mock.patch.object(self.nw, "probe_numato", return_value=False):
            lp.comports.return_value = []
            self.assertIsNone(self.nw.find_device())

    def test_aufzaehlung_darf_werfen_ohne_den_lauf_zu_beenden(self):
        """Auf manchen Systemen wirft `comports()`. Das ist kein Grund
        aufzuhoeren — der udev-Symlink kann trotzdem da sein."""
        def existiert(p):
            return p == "/dev/numato0"

        with mock.patch.object(self.nw, "list_ports") as lp, \
             mock.patch.object(self.nw.os.path, "exists", side_effect=existiert):
            lp.comports.side_effect = OSError("kein Zugriff")
            self.assertEqual(self.nw.kandidaten(), ["/dev/numato0"])


class RunLocalKenntDenNumato(unittest.TestCase):
    """Der Watcher muss auch GESTARTET werden koennen.

    Bis 2026-09-15 stand `numato_watcher.py` in `run-local.py` nirgends: die
    Geraetesuche haette funktionieren koennen, und es haette trotzdem
    niemand etwas davon gehabt.
    """

    def test_schalter_und_start_stehen_drin(self):
        quelle = (ROOT / "run-local.py").read_text(encoding="utf-8")
        self.assertIn('"--numato"', quelle)
        self.assertIn('starte("numato_watcher.py")', quelle)

    def test_der_windows_starter_reicht_die_schalter_durch(self):
        bat = ROOT / "run_windows.bat"
        self.assertTrue(bat.exists(), "run_windows.bat fehlt")
        text = bat.read_text(encoding="utf-8", errors="replace")
        # Ohne Dienst-Modus haengt der Aufrufer (Suite) an einem `pause`.
        self.assertIn("--server", text)
        self.assertIn("run-local.py", text)
        # `py` zuerst: `python` kann auf den Store-Alias zeigen.
        self.assertIn("where py", text)


if __name__ == "__main__":
    unittest.main()
