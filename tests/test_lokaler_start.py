"""
Startet der Tally-Pi auf einem Rechner OHNE Pi-Hardware?

─── DIE MELDUNG DAHINTER (Nutzer, 2026-09-09) ──────────────────────────────

„Tally pi muss man auch lokal starten koennen. Nicht nur an raspberry."

─── WARUM DIESER TEST DEN SERVER WIRKLICH HOCHFAEHRT ───────────────────────

Weil die Frage genau das ist. Ein Test, der nur `paths.py` importiert und
zwei Umgebungsvariablen vergleicht, belegt, dass eine Zuordnung stimmt — er
belegt nicht, dass das Programm laeuft. Genau daran ist der Zustand vorher
vorbeigegangen: `python -m compileall` war gruen, waehrend ein harter
`import gpiod` jeden Start auf einem gewoehnlichen Rechner unmoeglich machte.

Geprueft wird deshalb der ganze Weg: `run-local.py` starten, warten, bis die
Oberflaeche antwortet, und den Tally-Zustand einer Kamera abfragen. Kommt
dort `pgm` heraus, hat der Beispiel-Zustand den Server erreicht und der
Server hat gerechnet.

Der Test ueberspringt sich NICHT, wenn etwas fehlt. Fehlende Hardware ist
hier der Normalfall und kein Grund, die Frage nicht zu stellen — das ist
die ganze Zusicherung.
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
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402


def freier_port() -> int:
    """Einen Port vom Betriebssystem geben lassen, statt einen zu raten."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class PfadeSindUmlenkbar(unittest.TestCase):
    """Ohne das gaebe es keinen lokalen Start — `/opt` und `/run` gehoeren root."""

    def test_vorgabe_ist_der_pi(self):
        # Die Vorgabe MUSS bleiben, was auf dem Pi installiert ist: die
        # systemd-Units setzen nichts, und ein verschobener Vorgabewert
        # wuerde jede laufende Installation ins Leere zeigen lassen.
        umgebung = {k: v for k, v in os.environ.items()
                    if k not in ("PI_GUIDE_CONF", "PI_GUIDE_STATE")}
        raus = subprocess.run(
            [sys.executable, "-c",
             "import paths;print(paths.CONF_DIR);print(paths.STATE_DIR)"],
            cwd=str(ROOT), env=umgebung, capture_output=True, text=True, check=True)
        self.assertEqual(raus.stdout.split(), ["/opt/pi-guide", "/run/pi-guide"])

    def test_umgebung_lenkt_um(self):
        umgebung = dict(os.environ)
        umgebung["PI_GUIDE_CONF"] = "/tmp/pruef-conf"
        umgebung["PI_GUIDE_STATE"] = "/tmp/pruef-state"
        raus = subprocess.run(
            [sys.executable, "-c",
             "import paths;print(paths.TALLY_FILE);print(paths.ATEM_STATE)"],
            cwd=str(ROOT), env=umgebung, capture_output=True, text=True, check=True)
        self.assertEqual(
            raus.stdout.split(),
            ["/tmp/pruef-conf/tally.json", "/tmp/pruef-state/atem.json"])

    def test_kein_absoluter_pi_pfad_ist_uebriggeblieben(self):
        # Die Gegenprobe zur Umlenkung: ein einziger stehengebliebener
        # `Path("/opt/pi-guide/…")` reichte, damit ein Programm doch wieder
        # nach root fragt — und zwar erst zur Laufzeit, an einer Stelle, die
        # der lokale Start vielleicht nie erreicht.
        uebrig = []
        for datei in sorted(ROOT.glob("*.py")):
            if datei.name == "paths.py":
                continue  # dort stehen die Vorgaben, das ist ihr Ort
            text = datei.read_text(encoding="utf-8")
            for zeile_nr, zeile in enumerate(text.split("\n"), 1):
                if zeile.lstrip().startswith("#"):
                    continue
                if '"/opt/pi-guide' in zeile or '"/run/pi-guide' in zeile:
                    uebrig.append(f"{datei.name}:{zeile_nr}")
        self.assertEqual(uebrig, [], f"absolute Pi-Pfade ausserhalb paths.py: {uebrig}")


class HardwareDarfFehlen(unittest.TestCase):
    """Fehlende Hardware ist eine Lage, kein Absturz — und sie wird GESAGT."""

    def test_gpio_watcher_laeuft_ohne_libgpiod(self):
        with TemporaryDirectory() as tmp:
            basis = Path(tmp)
            umgebung = dict(os.environ)
            umgebung["PI_GUIDE_CONF"] = str(basis / "conf")
            umgebung["PI_GUIDE_STATE"] = str(basis / "state")
            (basis / "conf").mkdir()
            (basis / "state").mkdir()
            (basis / "conf" / "bindings.json").write_text('{"bindings": []}')
            p = subprocess.Popen([sys.executable, str(ROOT / "gpio_watcher.py")],
                                 env=umgebung, cwd=str(ROOT),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                zustand = basis / "state" / "input-state.json"
                for _ in range(60):
                    if zustand.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(zustand.exists(),
                                "gpio_watcher hat keinen Zustand geschrieben")
                daten = json.loads(zustand.read_text())
                # Der Kern: NICHT schweigen. Ein Watcher, der nichts schreibt,
                # ist von einem, der nichts sieht, nicht zu unterscheiden.
                self.assertIn("gpio_available", daten)
                self.assertFalse(daten["gpio_available"])
                self.assertTrue(daten.get("reason"),
                                "kein Grund genannt — dann weiss niemand, warum")
                self.assertIsNone(p.poll(), "gpio_watcher ist abgestuerzt")
            finally:
                p.terminate()
                p.wait(timeout=5)

    def test_pi_status_bricht_ohne_oled_nicht_ab(self):
        raus = subprocess.run([sys.executable, str(ROOT / "pi_status.py")],
                              cwd=str(ROOT), capture_output=True, text=True,
                              timeout=30)
        self.assertEqual(raus.returncode, 0,
                         f"pi_status endete mit {raus.returncode}: {raus.stderr[-400:]}")


class DerLokaleStartLaeuft(unittest.TestCase):
    """Die eigentliche Frage — und sie wird durch Hochfahren beantwortet."""

    def test_oberflaeche_antwortet_und_rechnet(self):
        port = freier_port()
        with TemporaryDirectory() as tmp:
            p = subprocess.Popen(
                [sys.executable, str(ROOT / "run-local.py"),
                 "--dir", tmp, "--demo", "--port", str(port),
                 "--host", "127.0.0.1", "--no-gpio"],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True)
            try:
                basis = f"http://127.0.0.1:{port}"
                antwort = None
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(basis + "/atem", timeout=1) as r:
                            antwort = json.loads(r.read())
                            break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.1)
                self.assertIsNotNone(
                    antwort, "die Oberflaeche ist nicht hochgekommen")
                self.assertTrue(antwort.get("demo"),
                                "der Beispiel-Zustand nennt sich nicht als solcher")

                # Und jetzt die Rechnung: PGM steht auf Eingang 1, also muss
                # Kamera 1 rot sein, Kamera 2 (PVW 2) gruen und Kamera 3 frei.
                erwartet = {"cam1": "pgm", "cam2": "pvw", "cam3": "safe"}
                for gid, soll in erwartet.items():
                    with urllib.request.urlopen(
                            f"{basis}/tally/state?id={gid}", timeout=2) as r:
                        ist = json.loads(r.read())
                    self.assertEqual(ist.get("state"), soll,
                                     f"{gid}: erwartet {soll}, bekam {ist}")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()

    def test_ohne_demo_wird_keine_verbindung_behauptet(self):
        # Der Gegenfall, und er ist der wichtigere: ohne Mischer im Netz darf
        # nichts dastehen, was nach einer Verbindung aussieht. Eine Anzeige,
        # die „verbunden" behauptet, waehrend niemand antwortet, ist genau
        # die Verwechslung, vor der `B-86` (Tally-Seite luegt nicht) steht.
        port = freier_port()
        with TemporaryDirectory() as tmp:
            p = subprocess.Popen(
                [sys.executable, str(ROOT / "run-local.py"),
                 "--dir", tmp, "--port", str(port),
                 "--host", "127.0.0.1", "--no-gpio"],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True)
            try:
                basis = f"http://127.0.0.1:{port}"
                antwort = None
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(basis + "/atem", timeout=1) as r:
                            antwort = json.loads(r.read())
                            break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.1)
                self.assertIsNotNone(antwort, "die Oberflaeche ist nicht hochgekommen")
                self.assertFalse(antwort.get("connected"),
                                 f"ohne Mischer wird eine Verbindung behauptet: {antwort}")
            finally:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    unittest.main()
