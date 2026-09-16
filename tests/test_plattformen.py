"""
Laeuft der lokale Start auch auf einem Mac und unter Windows?

─── DIE MELDUNG DAHINTER (Nutzer, 2026-09-12) ──────────────────────────────

„baue die app so das sie auch auf mac und windows lokal server starten kann"

─── WAS DIESE DATEI PRUEFT UND WAS SIE NICHT KANN ──────────────────────────

Sie laeuft auf der Plattform, auf der sie gestartet wird — und der
verify-Workflow startet sie seit dem 2026-09-12 auf ubuntu, macos UND
windows. Das ist der eigentliche Nachweis: dieselben Fragen, dreimal
gestellt. Ein Test, der die Windows-Zusage nur auf Linux behauptet, haette
denselben Wert wie `compileall` — und genau daran ist der lokale Start
schon einmal vorbeigegangen.

Zwei Dinge gehen trotzdem nicht auf der Fremdplattform, und die stehen
deshalb als reine Funktionen da, die man von ueberall pruefen kann:

  * `paths.default_dirs(plattform, …)` — wohin die Vorgaben zeigen. Die
    Windows-Antwort ist auf Linux pruefbar, weil die Funktion keinen
    Dateizugriff macht.
  * `cmd_channel` mit `PI_GUIDE_CMD_TCP=1` — der Windows-Weg des
    Befehlskanals (Loopback-Port statt Unix-Socket) laeuft damit auch auf
    Linux und macOS wirklich durch, statt nur uebersetzt zu werden.
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import temp_verzeichnis as TemporaryDirectory  # noqa: E402

import cmd_channel  # noqa: E402
import paths  # noqa: E402

WINDOWS = sys.platform.startswith("win")


def freier_port() -> int:
    """Einen Port vom Betriebssystem geben lassen, statt einen zu raten."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_frei(port: int, timeout: float = 15.0) -> bool:
    """Wartet, bis der Port wieder zu haben ist."""
    ende = time.time() + timeout
    while time.time() < ende:
        s = socket.socket()
        if not WINDOWS:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.2)
        finally:
            s.close()
    return False


def warte_auf_antwort(basis: str, pfad: str = "/atem", versuche: int = 150):
    for _ in range(versuche):
        try:
            with urllib.request.urlopen(basis + pfad, timeout=1) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return None


class DieVorgabepfadePassenZurPlattform(unittest.TestCase):
    """`/opt` und `/run` gibt es nur auf dem Pi — und nur dort sind sie richtig."""

    def test_linux_bleibt_der_pi(self):
        # UNVERAENDERLICH. Jede installierte systemd-Unit setzt nichts und
        # verlaesst sich auf genau diese beiden Pfade; ein verschobener
        # Vorgabewert zeigte jede laufende Installation ins Leere.
        self.assertEqual(paths.default_dirs("linux", {}, "/home/pi"),
                         ("/opt/pi-guide", "/run/pi-guide"))

    def test_mac_liegt_im_home(self):
        conf, state = paths.default_dirs("darwin", {}, "/Users/ada")
        self.assertEqual(
            conf, "/Users/ada/Library/Application Support/tally-pi/conf")
        self.assertEqual(
            state, "/Users/ada/Library/Application Support/tally-pi/state")

    def test_windows_liegt_im_localappdata(self):
        conf, state = paths.default_dirs(
            "win32", {"LOCALAPPDATA": r"C:\Users\ada\AppData\Local"},
            r"C:\Users\ada")
        self.assertEqual(PureWindowsPath(conf),
                         PureWindowsPath(r"C:\Users\ada\AppData\Local\tally-pi\conf"))
        self.assertEqual(PureWindowsPath(state),
                         PureWindowsPath(r"C:\Users\ada\AppData\Local\tally-pi\state"))
        # Und ohne LOCALAPPDATA (abgespeckte Dienstkonten): trotzdem ein Pfad
        # im Profil und keine Ausnahme.
        conf2, _ = paths.default_dirs("win32", {}, r"C:\Users\ada")
        self.assertEqual(PureWindowsPath(conf2), PureWindowsPath(conf))

    def test_keine_vorgabe_zeigt_auf_opt_oder_run_ausserhalb_linux(self):
        # Der Kern der Meldung: auf einem Mac gehoert `/opt` root, unter
        # Windows landete `/run/pi-guide` still auf `C:\run\pi-guide`. Beides
        # sind Orte, an denen der angemeldete Nutzer nichts schreiben kann —
        # der lokale Start waere mit einem Rechtefehler gestorben.
        for plattform in ("darwin", "win32", "freebsd14"):
            for d in paths.default_dirs(plattform, {}, "/home/ada"):
                self.assertNotIn("/opt/pi-guide", d.replace("\\", "/"),
                                 f"{plattform}: zeigt auf /opt")
                self.assertNotIn("/run/pi-guide", d.replace("\\", "/"),
                                 f"{plattform}: zeigt auf /run")

    def test_diese_maschine_kann_ihre_vorgaben_anlegen(self):
        # Die Gegenprobe auf der Plattform, auf der der Test gerade laeuft:
        # ausser auf Linux (dort gehoeren die Pfade root und dem Installer)
        # muss der angemeldete Nutzer die Vorgabe wirklich anlegen koennen.
        # Sonst ist die Zusage „laeuft lokal" eine Behauptung ueber ein
        # Verzeichnis, das niemand beschreiben darf.
        if sys.platform.startswith("linux"):
            self.skipTest("auf Linux sind die Vorgaben die Pi-Pfade — "
                          "die legt bootstrap.sh als root an")
        for d in paths.default_dirs():
            pfad = Path(d)
            try:
                pfad.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self.fail(f"Vorgabeverzeichnis {pfad} nicht anlegbar: {e}")
            probe = pfad / ".schreibprobe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()


class DerBefehlskanalGehtAufBeidenWegen(unittest.TestCase):
    """Unix-Socket wo es eins gibt, Loopback-Port wo nicht — dieselbe Zusage."""

    def _rundlauf(self, umgebung_tcp: bool):
        with TemporaryDirectory() as tmp:
            eigen = dict(os.environ)
            eigen["PI_GUIDE_STATE"] = str(Path(tmp) / "state")
            eigen["PI_GUIDE_CONF"] = str(Path(tmp) / "conf")
            eigen["PI_GUIDE_CMD_TCP"] = "1" if umgebung_tcp else "0"
            programm = (
                "import json, threading, sys, cmd_channel\n"
                "srv = cmd_channel.listen()\n"
                "print(cmd_channel.beschreibung(), flush=True)\n"
                "def bediene():\n"
                "    conn, _ = srv.accept()\n"
                "    roh = conn.recv(4096).decode()\n"
                "    req = json.loads(roh.split('\\n')[0])\n"
                "    ok = req.get('cmd') == 'set_aux'\n"
                "    conn.sendall((json.dumps({'ok': ok}) + '\\n').encode())\n"
                "    conn.close()\n"
                "t = threading.Thread(target=bediene); t.start()\n"
                "print(json.dumps(cmd_channel.sende(\n"
                "    {'cmd': 'set_aux', 'aux': 1, 'source': 3})), flush=True)\n"
                "t.join()\n"
            )
            raus = subprocess.run([sys.executable, "-c", programm],
                                  cwd=str(ROOT), env=eigen,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(raus.returncode, 0,
                             f"Kanal scheiterte: {raus.stderr[-500:]}")
            zeilen = raus.stdout.split()
            self.assertIn("tcp" if umgebung_tcp else "unix", zeilen[0])
            self.assertIn('"ok": true', raus.stdout.replace("'", '"'))

    def test_der_weg_dieser_plattform(self):
        self._rundlauf(umgebung_tcp=not cmd_channel.UNIX_MOEGLICH)

    def test_der_windows_weg_laeuft_auch_hier(self):
        # Der Loopback-Port ist unter Windows der EINZIGE Weg. Wenn er nur
        # dort liefe, wuerde er nur dort auffallen, wenn er kaputt ist.
        self._rundlauf(umgebung_tcp=True)

    def test_ohne_watcher_sagt_der_kanal_warum(self):
        # Ein Kanal, der still nichts tut, laesst einen Tastendruck ins Leere
        # laufen: die Oberflaeche meldete „ausgeloest", der Mischer bekaeme
        # nichts. Der Grund muss den Weg benennen, den er vermisst.
        with TemporaryDirectory() as tmp:
            eigen = dict(os.environ)
            eigen["PI_GUIDE_STATE"] = str(Path(tmp) / "state")
            raus = subprocess.run(
                [sys.executable, "-c",
                 "import cmd_channel\n"
                 "try:\n"
                 "    cmd_channel.sende({'cmd': 'set_aux'})\n"
                 "    print('KEIN FEHLER')\n"
                 "except RuntimeError as e:\n"
                 "    print(e)\n"],
                cwd=str(ROOT), env=eigen, capture_output=True, text=True,
                timeout=30)
            self.assertNotIn("KEIN FEHLER", raus.stdout)
            self.assertIn("atem", raus.stdout.lower())
            self.assertIn("atem-cmd", raus.stdout)

    def test_kein_af_unix_ausserhalb_des_kanals(self):
        # Die Gegenprobe zur Umlenkung: ein einziges stehengebliebenes
        # `socket.AF_UNIX` reichte, damit ein Programm unter Windows mit
        # `AttributeError` abbricht — und zwar erst, wenn jemand den Knopf
        # drueckt, nicht beim Start. Geprueft wird der SYNTAXBAUM und nicht
        # der Text: das Wort darf in einer Erklaerung stehen (es steht in
        # mehreren), nur eben nicht in einem Ausdruck.
        import ast
        uebrig = []
        for datei in sorted(ROOT.glob("*.py")):
            if datei.name == "cmd_channel.py":
                continue  # dort liegt die Fallunterscheidung, das ist ihr Ort
            baum = ast.parse(datei.read_text(encoding="utf-8"), str(datei))
            for knoten in ast.walk(baum):
                trifft = (isinstance(knoten, ast.Attribute)
                          and knoten.attr == "AF_UNIX")
                trifft = trifft or (isinstance(knoten, ast.Name)
                                    and knoten.id == "AF_UNIX")
                if trifft:
                    uebrig.append(f"{datei.name}:{knoten.lineno}")
        self.assertEqual(uebrig, [], f"AF_UNIX ausserhalb cmd_channel.py: {uebrig}")


class DieNetzwerkanzeigeAntwortetAufJedemSystem(unittest.TestCase):
    """„Unter welcher Adresse erreiche ich das Ding?" — auf allen dreien.

    Auf dem Pi beantwortet das `ip -4 -j addr show`. Das Kommando ist
    Linux-eigen; auf einem Mac und unter Windows stand an der Stelle, wo eine
    Adresse stehen soll, bis 2026-09-12 `[Errno 2] ... 'ip'`. Wer die
    Tally-Seite auf dem Handy oeffnen will, liest genau dieses Feld.
    """

    # Echte `ifconfig -a`-Ausgabe eines Macs, gekuerzt auf drei
    # Schnittstellen: Loopback, aktives WLAN, abgestecktes Ethernet.
    MAC_AUSGABE = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\toptions=1203<RXCSUM,TXCSUM,TXSTATUS,SW_TIMESTAMP>
\tinet 127.0.0.1 netmask 0xff000000
\tinet6 ::1 prefixlen 128
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 3c:22:fb:11:22:33
\tinet6 fe80::1c:2d:3e:4f%en0 prefixlen 64 secured scopeid 0xc
\tinet 192.168.1.42 netmask 0xffffff00 broadcast 192.168.1.255
\tmedia: autoselect
\tstatus: active
en5: flags=8822<BROADCAST,SMART,SIMPLEX,MULTICAST> mtu 1500
\tether 82:11:22:33:44:55
\tmedia: autoselect <full-duplex>
\tstatus: inactive
"""

    def test_der_mac_parser_findet_die_adresse(self):
        import guide_server as gs
        ifaces = gs._ifaces_ifconfig(self.MAC_AUSGABE)
        self.assertEqual([i["name"] for i in ifaces], ["en0"],
                         "lo0 gehoert nicht in die Liste, en5 hat keine IPv4")
        self.assertEqual(ifaces[0]["addresses"], ["192.168.1.42"])
        self.assertEqual(ifaces[0]["state"], "UP")

    def test_diese_maschine_nennt_eine_adresse_oder_einen_grund(self):
        import guide_server as gs
        d = gs.get_ipconfig()
        self.assertTrue(d.get("hostname"))
        self.assertTrue(d.get("interfaces"), "gar keine Antwort")
        eintrag = d["interfaces"][0]
        # Entweder eine Adresse — oder ein Grund. Ein leeres Feld ohne beides
        # ist das, was hier nicht mehr vorkommen darf.
        self.assertTrue(eintrag.get("addresses") or eintrag.get("error"),
                        f"weder Adresse noch Grund: {d}")


class JedesProgrammLaedtAufDieserPlattform(unittest.TestCase):
    """Importierbar heisst nicht lauffaehig — aber nicht importierbar heisst tot."""

    def test_import(self):
        for name in ("paths", "cmd_channel", "guide_server", "atem_watcher",
                     "gpio_watcher", "numato_watcher", "pi_status"):
            with self.subTest(modul=name):
                raus = subprocess.run(
                    [sys.executable, "-c", f"import {name}"],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                    env={**os.environ, "PI_GUIDE_CONF": str(ROOT / ".pruef-conf"),
                         "PI_GUIDE_STATE": str(ROOT / ".pruef-state")})
                self.assertEqual(raus.returncode, 0,
                                 f"{name} laedt nicht: {raus.stderr[-600:]}")


class DerLokaleStartAufDieserPlattform(unittest.TestCase):
    """Die eigentliche Frage — auf macOS und Windows genauso gestellt."""

    def starte(self, tmp, port, *extra):
        return subprocess.Popen(
            [sys.executable, str(ROOT / "run-local.py"),
             "--dir", tmp, "--port", str(port), "--host", "127.0.0.1",
             "--no-gpio", *extra],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)

    def test_die_oberflaeche_kommt_hoch_und_rechnet(self):
        port = freier_port()
        with TemporaryDirectory() as tmp:
            p = self.starte(tmp, port, "--demo")
            try:
                basis = f"http://127.0.0.1:{port}"
                antwort = warte_auf_antwort(basis)
                self.assertIsNotNone(antwort, "die Oberflaeche ist nicht hochgekommen")
                self.assertTrue(antwort.get("demo"))
                for gid, soll in {"cam1": "pgm", "cam2": "pvw", "cam3": "safe"}.items():
                    with urllib.request.urlopen(
                            f"{basis}/tally/state?id={gid}", timeout=5) as r:
                        ist = json.loads(r.read())
                    self.assertEqual(ist.get("state"), soll, f"{gid}: {ist}")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()

    def test_der_belegte_port_wird_vorher_gesagt(self):
        # Ohne die Vorpruefung startet dieses Programm zwei Watcher und
        # danach einen Server, der am Port scheitert — die Meldung stuende
        # zwischen drei Ausgaben und der Rest liefe weiter.
        port = freier_port()
        sperre = socket.socket()
        sperre.bind(("127.0.0.1", port))
        sperre.listen(1)
        try:
            with TemporaryDirectory() as tmp:
                p = self.starte(tmp, port)
                raus, _ = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 2, raus)
                self.assertIn(str(port), raus)
                self.assertIn("nicht frei", raus)
        finally:
            sperre.close()

    def test_beim_beenden_wird_der_port_wieder_frei(self):
        # Das ist die Windows-Frage: dort bekommen die Kindprozesse kein
        # Strg-C vom Fenster, sie werden von `run-local.py` beendet — und
        # wenn das misslingt, laufen Server und Watcher unsichtbar weiter und
        # halten den Port. Auf jeder Plattform muss nach dem Beenden der Port
        # wieder zu haben sein.
        port = freier_port()
        with TemporaryDirectory() as tmp:
            p = self.starte(tmp, port, "--demo")
            try:
                self.assertIsNotNone(warte_auf_antwort(f"http://127.0.0.1:{port}"),
                                     "die Oberflaeche ist nicht hochgekommen")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.assertTrue(port_frei(port),
                            f"Port {port} ist nach dem Beenden noch belegt — "
                            f"es laeuft noch etwas, das niemand sieht")

    def test_der_zustand_ueberlebt_einen_neustart_des_starts(self):
        # `--dir` wird beim Start NICHT geleert: wer eine Konfiguration von
        # Hand angelegt hat, findet sie wieder. Das gilt auf allen dreien,
        # und es ist der Grund, warum eine alte Port-Datei gezielt wegmuss
        # (sonst zeigte sie auf einen Watcher von gestern).
        port = freier_port()
        with TemporaryDirectory() as tmp:
            p = self.starte(tmp, port, "--demo")
            try:
                self.assertIsNotNone(warte_auf_antwort(f"http://127.0.0.1:{port}"))
                konf = Path(tmp) / "conf" / "tally.json"
                daten = json.loads(konf.read_text(encoding="utf-8"))
                daten["devices"][0]["name"] = "Von Hand geaendert"
                konf.write_text(json.dumps(daten, indent=2), encoding="utf-8")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.assertTrue(port_frei(port))

            port2 = freier_port()
            p2 = self.starte(tmp, port2, "--demo")
            try:
                antwort = warte_auf_antwort(f"http://127.0.0.1:{port2}",
                                            "/tally-config")
                self.assertIsNotNone(antwort)
                self.assertEqual(antwort["devices"][0]["name"], "Von Hand geaendert")
            finally:
                p2.terminate()
                try:
                    p2.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p2.kill()


class SchreibenUndLesenVertragenSichMitEinemZweitenProzess(unittest.TestCase):
    """Unter Windows scheitert `os.replace`, solange ein anderer die Datei haelt."""

    def test_der_leser_sieht_nie_ein_fragment(self):
        with TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "atem.json"
            schreiber = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys, time, paths\n"
                 "ziel = sys.argv[1]\n"
                 "ende = time.time() + 4\n"
                 "n = 0\n"
                 "while time.time() < ende:\n"
                 "    n += 1\n"
                 "    paths.atomic_write_json(ziel, {'n': n, 'fuellung': 'x' * 5000})\n"
                 "print(n)\n",
                 str(ziel)],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            gelesen = 0
            ende = time.time() + 4
            while time.time() < ende:
                d = paths.read_json(ziel)
                if d is None:
                    continue
                # Kein halber Inhalt, keine abgeschnittene Fuellung: genau das
                # ist die Zusage des atomaren Schreibens.
                self.assertEqual(len(d["fuellung"]), 5000)
                gelesen += 1
            raus, fehler = schreiber.communicate(timeout=30)
            self.assertEqual(schreiber.returncode, 0,
                             f"der Schreiber ist gestorben: {fehler[-500:]}")
            self.assertGreater(int(raus.strip()), 10, "kaum geschrieben")
            self.assertGreater(gelesen, 10, "kaum gelesen")


class DieStarterZumDoppelklicken(unittest.TestCase):
    """Wer keinen Terminal-Befehl tippen will, klickt — und das muss gehen."""

    def test_mac_starter_ist_ausfuehrbar_und_ruft_run_local(self):
        starter = ROOT / "start-local.command"
        self.assertTrue(starter.exists())
        text = starter.read_text(encoding="utf-8")
        self.assertIn("run-local.py", text)
        if os.name == "posix":
            self.assertTrue(os.access(starter, os.X_OK),
                            "ohne Ausfuehrrecht oeffnet der Finder nur den Editor")
            pruefung = subprocess.run(["bash", "-n", str(starter)],
                                      capture_output=True, text=True)
            self.assertEqual(pruefung.returncode, 0, pruefung.stderr)

    def test_windows_starter_hat_crlf(self):
        # `cmd.exe` bricht eine Batch-Datei mit reinen LF-Enden mitten in
        # einem `if (...)`-Block ab, ohne zu sagen warum. Die Regel dafuer
        # steht in `.gitattributes` — hier steht die Probe darauf.
        roh = (ROOT / "start-local.bat").read_bytes()
        self.assertIn(b"run-local.py", roh)
        nur_lf = roh.count(b"\n") - roh.count(b"\r\n")
        self.assertEqual(nur_lf, 0, "die .bat hat Zeilen ohne CR")
        # Und die Regel, die das beim naechsten Auschecken haelt: ohne sie
        # macht `* text=auto eol=lf` aus der Datei beim Klonen wieder eine
        # mit LF-Enden, und niemand sieht es im Diff.
        self.assertIn("*.bat text eol=crlf",
                      (ROOT / ".gitattributes").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
