"""
Der Weg einer Tally-Lampe: Guide-Server -> Numato-Befehlskanal -> Watcher ->
Board. Ueber ein ECHTES Socket, nur das Board ist nachgebaut.

Das ist die Naht zwischen zwei Prozessen, die sich sonst nur auf der
Zielmaschine zeigt: schickt der Guide-Server „Lampe an" als das richtige
`gpio set/clear` los, und kommt es am Kanal an? Der Befehlskanal wird auf ein
temporaeres Socket umgebogen, damit der Test nicht an `/run/pi-guide` will.
"""
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import NumatoFake, temp_verzeichnis  # noqa: E402
import numato_io  # noqa: E402
import cmd_channel  # noqa: E402
import numato_watcher as nw  # noqa: E402
import guide_server as gs  # noqa: E402


class LampeGehtUeberDenKanal(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_verzeichnis()
        d = Path(self.tmp.__enter__())
        # Den Numato-Kanal auf temporaere Dateien umbiegen — Server UND Client
        # benutzen dieselbe Instanz `cmd_channel.NUMATO`.
        self._alt = cmd_channel.NUMATO
        cmd_channel.NUMATO = cmd_channel.Kanal(
            "numato_watcher", d / "numato-cmd.sock", d / "numato-cmd.port")

        self.fake = NumatoFake(breite=32)
        self.board = numato_io.NumatoBoard(self.fake)
        self.mgr = nw.NumatoManager()
        self.mgr.attach(self.board, "fake", output_channels=[17], input_channels=[])

        self.server = threading.Thread(
            target=nw._command_server, args=(self.mgr,), daemon=True)
        self.server.start()
        # Warten, bis der Kanal offen ist.
        for _ in range(100):
            try:
                cmd_channel.NUMATO.sende({"cmd": "ping"}, timeout=0.5)
                break
            except Exception:
                time.sleep(0.02)

    def tearDown(self):
        cmd_channel.NUMATO = self._alt
        try:
            self.tmp.__exit__(None, None, None)
        except Exception:
            pass

    def test_roher_befehl_setzt_kanal(self):
        antwort = cmd_channel.NUMATO.sende({"cmd": "set", "channel": 17, "on": True})
        self.assertTrue(antwort.get("ok"))
        self.assertTrue(self.fake.get_output(17))

    def test_guide_server_backend_dreht_die_polaritaet(self):
        out = gs.NumatoTallyOutputs()
        out.configure([17])
        # set(17, on=True) heisst „Pin auf LOW" -> am Numato `gpio clear`.
        out.set(17, True)
        self.assertFalse(self.fake.get_output(17))
        # set(17, on=False) heisst „Pin auf HIGH" -> `gpio set`.
        out.set(17, False)
        self.assertTrue(self.fake.get_output(17))

    def test_grund_schlaegt_durch_wenn_kein_board(self):
        self.mgr.detach()
        out = gs.NumatoTallyOutputs()
        out.configure([17])
        ok, msg = out.set(17, False)
        self.assertFalse(ok)
        self.assertIn("kein Numato", msg)


if __name__ == "__main__":
    unittest.main()
