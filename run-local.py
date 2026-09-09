#!/usr/bin/env python3
"""
Tally-Pi auf einem gewoehnlichen Rechner starten.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-09) ────────────────────────────────

„Tally pi muss man auch lokal starten koennen. Nicht nur an raspberry."

─── WAS DIESES PROGRAMM TUT — UND WAS AUSDRUECKLICH NICHT ──────────────────

Es startet DIESELBEN Programme, die auf dem Pi laufen. Es gibt keinen
zweiten Codepfad und keinen „Entwicklungsmodus" mit eigenem Verhalten:
`guide_server.py`, `atem_watcher.py` und `gpio_watcher.py` sind dieselben
Dateien, sie liegen nur an einem anderen Ort und schreiben in ein
Verzeichnis, das dem angemeldeten Nutzer gehoert.

Das ist der Grund, warum die Pfade seit 2026-09-09 aus `paths.py` kommen:
ein lokaler Start, der eine eigene Kopie der Logik braucht, prueft die
Kopie und nicht das Programm.

WAS FEHLT, FEHLT SICHTBAR. Ohne 40-poligen Stecker gibt es keine
GPIO-Eingaenge, ohne I2C kein OLED, ohne Mischer im Netz keine ATEM-Daten.
Nichts davon wird nachgebildet:

  * `gpio_watcher` schreibt `gpio_available: false` samt Grund und laeuft
    weiter. Ein Mock, der Tastendruecke erfindet, waere hier gefaehrlich —
    an diesen Eingaengen haengt eine Kamera-Umschaltung.
  * `atem_watcher` laeuft normal und meldet „nicht verbunden", solange
    keine Gegenstelle antwortet. Mit `--atem <ip>` spricht er einen echten
    Mischer im Netz an; das ist der ehrliche Weg, die Anzeige zu pruefen.
  * `--demo` legt EINEN Zustand ab (drei Kameras, PGM 1 / PVW 2) und
    startet den ATEM-Watcher gar nicht erst. Der Zustand steht still und
    ist als Beispiel erkennbar — er behauptet keine Verbindung.

AUFRUF

    python3 run-local.py                 # Oberflaeche, keine Hardware
    python3 run-local.py --demo          # dazu ein stehender Beispiel-Zustand
    python3 run-local.py --atem 10.0.0.5 # gegen einen echten Mischer
    python3 run-local.py --port 8081     # anderer Port
    python3 run-local.py --host 127.0.0.1  # nur lokal, nicht im Netz

Der Zustand liegt unter `.local-run/` im Arbeitsverzeichnis (ueber
`--dir` verschiebbar). Er wird beim Start NICHT geloescht — wer eine
Konfiguration von Hand angelegt hat, findet sie wieder.
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEMO_TALLY = {
    "atem_ip": "",
    "devices": [
        {"id": "cam1", "name": "Kamera 1", "input": 1, "me": 1, "aux": [],
         "out_gpio": 17, "out_trigger": "pgm", "out_active_high": False},
        {"id": "cam2", "name": "Kamera 2", "input": 2, "me": 1, "aux": [],
         "out_gpio": 22, "out_trigger": "pgm", "out_active_high": False},
        {"id": "cam3", "name": "Kamera 3 (Handheld)", "input": 3, "me": 1,
         "aux": [1], "out_gpio": 23, "out_trigger": "pgm_pvw",
         "out_active_high": True},
    ],
}

# Ein STEHENDER Zustand, und er sagt selbst, dass er einer ist. `connected`
# steht auf true, weil die Oberflaeche sonst den Verbindungsfehler zeigt und
# man die Tally-Farben gar nicht saehe; `demo` daneben ist die Wahrheit
# darueber, woher die Zahlen kommen.
DEMO_ATEM = {
    "connected": True,
    "demo": True,
    "pgm": {"0": 1},
    "pvw": {"0": 2},
    "aux": {"1": 5},
    "inputs": {"1": "Kamera 1", "2": "Kamera 2", "3": "Kamera 3", "5": "Kamera 1"},
}


def lan_adresse() -> str:
    """Die Adresse, unter der andere Geraete im selben Netz drankommen."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Kein Paket geht raus — `connect` auf UDP waehlt nur die Route und
        # verraet damit die Absender-Adresse. Ohne Netz faellt es auf
        # localhost zurueck, was fuer die Ausgabe genau richtig ist.
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dir", default=".local-run",
                    help="Verzeichnis fuer Konfiguration und Zustand")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0",
                    help="Bind-Adresse. Vorgabe: alle Schnittstellen, damit "
                         "ein Handy im selben Netz drankommt.")
    ap.add_argument("--demo", action="store_true",
                    help="stehenden Beispiel-Zustand ablegen statt einen "
                         "Mischer zu suchen")
    ap.add_argument("--atem", default="",
                    help="IP eines echten ATEM im Netz")
    ap.add_argument("--no-gpio", action="store_true",
                    help="den GPIO-Watcher gar nicht erst starten")
    a = ap.parse_args()

    basis = Path(a.dir).resolve()
    conf, state = basis / "conf", basis / "state"
    conf.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    umgebung = dict(os.environ)
    umgebung["PI_GUIDE_CONF"] = str(conf)
    umgebung["PI_GUIDE_STATE"] = str(state)
    umgebung["GUIDE_HOST"] = a.host
    umgebung["GUIDE_PORT"] = str(a.port)
    umgebung["PYTHONUNBUFFERED"] = "1"

    tally = conf / "tally.json"
    if not tally.exists():
        cfg = dict(DEMO_TALLY)
        cfg["atem_ip"] = a.atem
        tally.write_text(json.dumps(cfg, indent=2))
        print(f"[local] Beispiel-Konfiguration angelegt: {tally}")
    elif a.atem:
        # Eine vorhandene Konfiguration wird NICHT ueberschrieben — nur die
        # eine Angabe, die auf der Kommandozeile stand.
        cfg = json.loads(tally.read_text())
        cfg["atem_ip"] = a.atem
        tally.write_text(json.dumps(cfg, indent=2))
        print(f"[local] atem_ip auf {a.atem} gesetzt")

    if not (conf / "bindings.json").exists():
        (conf / "bindings.json").write_text(json.dumps({"bindings": []}, indent=2))

    for name in ("setup-guide.html",):
        ziel = conf / name
        if not ziel.exists() and (ROOT / name).exists():
            ziel.write_bytes((ROOT / name).read_bytes())

    if a.demo:
        (state / "atem.json").write_text(json.dumps(DEMO_ATEM))
        print("[local] Beispiel-Zustand abgelegt (drei Kameras, PGM 1 / PVW 2)")

    prozesse: list[subprocess.Popen] = []

    def starte(datei: str, *args: str) -> None:
        p = subprocess.Popen([sys.executable, str(ROOT / datei), *args],
                             env=umgebung, cwd=str(ROOT))
        prozesse.append(p)
        print(f"[local] gestartet: {datei} (pid {p.pid})")

    # Der ATEM-Watcher wuerde den stehenden Beispiel-Zustand sofort mit
    # „nicht verbunden" ueberschreiben — deshalb laeuft er im Demo-Betrieb
    # nicht. Das ist keine Vertuschung: ohne ihn steht auch keine
    # Verbindungsmeldung da, die es zu glauben gaebe.
    if not a.demo:
        starte("atem_watcher.py")
    if not a.no_gpio:
        starte("gpio_watcher.py")

    guide_env = dict(umgebung)
    guide = subprocess.Popen([sys.executable, str(ROOT / "guide_server.py")],
                             env=guide_env, cwd=str(ROOT))
    prozesse.append(guide)

    lan = lan_adresse()
    print()
    print("  Tally-Pi laeuft lokal.")
    print(f"    hier:            http://127.0.0.1:{a.port}/")
    if a.host == "0.0.0.0":
        print(f"    im selben Netz:  http://{lan}:{a.port}/")
    print(f"    Konfiguration:   {conf}")
    print(f"    Zustand:         {state}")
    print()
    print("  Zum Beenden Strg-C.")
    print()

    def beenden(*_):
        for p in prozesse:
            try:
                p.terminate()
            except OSError:
                pass
        for p in prozesse:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, beenden)
    signal.signal(signal.SIGTERM, beenden)
    while True:
        for p in prozesse:
            if p.poll() is not None:
                print(f"[local] Prozess {p.args[1]} beendet (Code {p.returncode})")
                beenden()
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
