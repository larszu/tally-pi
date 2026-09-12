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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import temp_verzeichnis as TemporaryDirectory  # noqa: E402

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
        # Die Vorgabe MUSS auf Linux bleiben, was auf dem Pi installiert ist:
        # die systemd-Units setzen nichts, und ein verschobener Vorgabewert
        # wuerde jede laufende Installation ins Leere zeigen lassen.
        #
        # Auf macOS und Windows gibt es diese Pfade nicht (siehe
        # `tests/test_plattformen.py`) — dort ist die Vorgabe ein Ort im
        # Profil des angemeldeten Nutzers, und geprueft wird, dass sie mit
        # `paths.default_dirs` fuer DIESE Plattform uebereinstimmt.
        umgebung = {k: v for k, v in os.environ.items()
                    if k not in ("PI_GUIDE_CONF", "PI_GUIDE_STATE")}
        raus = subprocess.run(
            [sys.executable, "-c",
             "import paths;print(paths.CONF_DIR);print(paths.STATE_DIR)"],
            cwd=str(ROOT), env=umgebung, capture_output=True, text=True, check=True)
        gelesen = raus.stdout.split("\n")[:2]
        gelesen = [z.strip() for z in gelesen]
        if sys.platform.startswith("linux"):
            self.assertEqual(gelesen, ["/opt/pi-guide", "/run/pi-guide"])
        else:
            self.assertEqual([Path(z) for z in gelesen],
                             [Path(d) for d in paths.default_dirs()])

    def test_umgebung_lenkt_um(self):
        # Zwei Pfade, die es auf jeder der drei Plattformen gibt — ein
        # fest eingetragenes `/tmp/...` waere unter Windows `C:\tmp\...`
        # und der Vergleich haette dort nur den Test geprueft.
        with TemporaryDirectory() as tmp:
            conf = Path(tmp) / "pruef-conf"
            state = Path(tmp) / "pruef-state"
            umgebung = dict(os.environ)
            umgebung["PI_GUIDE_CONF"] = str(conf)
            umgebung["PI_GUIDE_STATE"] = str(state)
            raus = subprocess.run(
                [sys.executable, "-c",
                 "import paths;print(paths.TALLY_FILE);print(paths.ATEM_STATE)"],
                cwd=str(ROOT), env=umgebung, capture_output=True, text=True,
                check=True)
            gelesen = [Path(z.strip()) for z in raus.stdout.split("\n")[:2]]
            self.assertEqual(gelesen, [conf / "tally.json", state / "atem.json"])

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


class DieInstallerNehmenAllesMit(unittest.TestCase):
    """Ein Programm ohne sein Modul ist auf dem Pi ein toter Dienst.

    DER FALL, DER DAS AUSGELOEST HAT (2026-09-12): Der Befehlskanal zog aus
    drei Programmen in `cmd_channel.py` um. `bootstrap.sh` und
    `update-on-pi.sh` kopieren eine Liste von Dateien nach `/opt/pi-guide` —
    eine Liste, die das neue Modul nicht kannte. Ein `update-on-pi.sh` haette
    dann eine neue `atem_watcher.py` neben ein fehlendes `cmd_channel.py`
    gelegt, und der Dienst waere beim Start mit `ModuleNotFoundError`
    gestorben. Auf einem Geraet, das im Rack steht und keinen Bildschirm hat.

    `update-on-pi.sh` kopierte ausserdem `paths.py` gar nicht — solange sich
    die Datei nie aenderte, fiel das nicht auf.

    Geprueft wird deshalb nicht eine Namensliste, sondern die Beziehung: was
    ein installiertes Programm importiert, muss im selben Zielverzeichnis
    landen.
    """

    LOKALE_MODULE = {d.stem for d in ROOT.glob("*.py")}

    def _installiert(self, skript):
        """{Zielverzeichnis: {Dateiname, …}} aus den `install`-Zeilen."""
        ziele = {}
        for zeile in (ROOT / skript).read_text(encoding="utf-8").split("\n"):
            zeile = zeile.strip()
            if not zeile.startswith("install ") or zeile.startswith("install -d"):
                continue
            teile = zeile.split()
            if len(teile) < 2:
                continue
            quelle, ziel = teile[-2], teile[-1]
            if quelle.endswith(".py"):
                ziele.setdefault(ziel.rstrip("/"), set()).add(quelle)
        return ziele

    def _importe(self, datei):
        """Die lokalen Module, die diese Datei importiert (rekursiv)."""
        import ast
        gefunden, offen = set(), [datei]
        while offen:
            aktuell = offen.pop()
            baum = ast.parse((ROOT / aktuell).read_text(encoding="utf-8"))
            for knoten in ast.walk(baum):
                namen = []
                if isinstance(knoten, ast.Import):
                    namen = [a.name.split(".")[0] for a in knoten.names]
                elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                    namen = [knoten.module.split(".")[0]]
                for n in namen:
                    if n in self.LOKALE_MODULE and f"{n}.py" not in gefunden:
                        gefunden.add(f"{n}.py")
                        offen.append(f"{n}.py")
        return gefunden

    def _pruefe(self, skript):
        for ziel, dateien in self._installiert(skript).items():
            for datei in sorted(dateien):
                for gebraucht in sorted(self._importe(datei)):
                    self.assertIn(
                        gebraucht, dateien,
                        f"{skript}: {datei} landet in {ziel}/ und importiert "
                        f"{gebraucht} — das wird dort nicht installiert")

    def test_bootstrap(self):
        self._pruefe("bootstrap.sh")

    def test_update_auf_dem_pi(self):
        self._pruefe("update-on-pi.sh")


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
