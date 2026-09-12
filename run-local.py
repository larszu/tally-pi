#!/usr/bin/env python3
"""
Tally-Pi auf einem gewoehnlichen Rechner starten — Linux, macOS, Windows.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-09 und 2026-09-12) ─────────────────

„Tally pi muss man auch lokal starten koennen. Nicht nur an raspberry."
„baue die app so das sie auch auf mac und windows lokal server starten kann"

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

─── WAS AN MAC UND WINDOWS ANDERS IST (und was nicht) ──────────────────────

Nichts am Ablauf. Drei Dinge am Unterbau, alle in eigenen Dateien:

  * die Vorgabepfade    → `paths.py` (`/opt` und `/run` gibt es dort nicht)
  * der Befehlskanal    → `cmd_channel.py` (Windows kennt kein AF_UNIX)
  * das Beenden         → weiter unten: unter Windows bekommen die
                          Kindprozesse Strg-C nicht vom Fenster, sondern
                          werden von hier beendet.

Was NICHT geht, geht auf keiner der drei Plattformen: GPIO-Eingaenge,
Tally-Ausgaenge und das OLED brauchen den Pi. Das sagt die Oberflaeche
selbst, an jeder betroffenen Stelle.

AUFRUF

    python3 run-local.py                 # Oberflaeche, keine Hardware
    python3 run-local.py --demo          # dazu ein stehender Beispiel-Zustand
    python3 run-local.py --demo --open   # und gleich den Browser aufmachen
    python3 run-local.py --atem 10.0.0.5 # gegen einen echten Mischer
    python3 run-local.py --port 8081     # anderer Port
    python3 run-local.py --host 127.0.0.1  # nur lokal, nicht im Netz

Unter Windows heisst das Programm meist `python` statt `python3`; auf dem
Mac und unter Windows gibt es zum Doppelklicken ausserdem
`start-local.command` bzw. `start-local.bat`.

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
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS = sys.platform.startswith("win")
MAC = sys.platform == "darwin"

#: Aelter geht nicht: die Programme hier benutzen `list[...]`-Annotationen
#: und `gpiod` 2.x. 3.9 ist das, was auf einem macOS ohne Zusatzinstallation
#: liegt, und deutlich aelter als jedes aktuelle Windows-Python.
MIN_PYTHON = (3, 9)

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


def port_belegt(host: str, port: int) -> str:
    """Leerer Text, wenn der Port frei ist — sonst der Grund im Klartext.

    WARUM VORHER UND NICHT ERST IM SERVER. Der `guide_server` laeuft als
    Kindprozess; sein Startfehler steht mitten in der Ausgabe von drei
    Prozessen, und dieses Programm haette danach zwei laufende Watcher und
    keine Oberflaeche. Einmal fragen, bevor irgendetwas startet, kostet
    eine Millisekunde und spart die Verwechslung.

    DIE PROBE MUSS GENAUSO BINDEN WIE DER SERVER, sonst luegt sie in beide
    Richtungen. Unter POSIX setzt `guide_server` SO_REUSEADDR und darf
    deshalb auf einen Port, der nach dem letzten Strg-C noch eine Minute in
    TIME_WAIT haengt — eine Probe ohne die Option haette den zweiten Start
    innerhalb einer Minute mit „Port belegt" abgewiesen, obwohl er
    funktioniert haette. Unter Windows setzt der Server sie bewusst NICHT
    (dort erlaubt sie zwei Servern denselben Port), also hier auch nicht.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not WINDOWS:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return ""
    except OSError as e:
        return str(e)
    finally:
        s.close()


# ── Windows: die Kinder haengen am Elternprozess ───────────────────────────
#
# WARUM DAS HIER STEHT. Unter POSIX beendet dieses Programm seine drei Kinder
# im Signal-Handler, und wer es hart abschiesst, hinterlaesst Waisen, die
# `pkill` findet. Unter Windows ist die Lage unangenehmer: wird dieses
# Fenster geschlossen oder das Programm im Task-Manager beendet, laufen
# `guide_server` und die Watcher WEITER — unsichtbar, mit belegtem Port 8080.
# Der naechste Doppelklick auf `start-local.bat` sagt dann „Port ist nicht
# frei", und niemand sieht, wer ihn belegt.
#
# Ein Job-Objekt mit KILL_ON_JOB_CLOSE loest genau das: die Kinder haengen an
# einem Griff, den Windows schliesst, wenn dieser Prozess endet — egal wie er
# endet. Das ist der uebliche Weg dafuer, aber er geht ueber ctypes, und
# ctypes-Code, der auf dieser Plattform nicht laeuft, kann man hier nicht
# ausprobieren. Deshalb ist ALLES darin abgesichert: geht es nicht, sagt es
# das in einer Zeile und der Start laeuft ohne. Der geordnete Weg (Strg-C)
# raeumt ohnehin selbst auf; das Job-Objekt ist die Absicherung fuer den
# ungeordneten.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _kernel32():
    """kernel32 mit ausgeschriebenen Signaturen.

    Ohne `restype` nimmt ctypes `int` an — und ein Griff (HANDLE) ist auf
    einem 64-Bit-Windows breiter als das. Der abgeschnittene Wert sieht wie
    ein gueltiger Griff aus und zeigt auf nichts. Deshalb steht hier jede
    Signatur, statt sich auf die Vorgabe zu verlassen.
    """
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            wintypes.LPVOID, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def windows_job():
    """Ein Job-Objekt, das seine Prozesse mitnimmt — oder None."""
    if not WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),   # ULONG_PTR
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation",
                         JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        return job
    except Exception as e:
        print(f"[local] Hinweis: die Kindprozesse haengen nicht am Job-Objekt "
              f"({e}). Beim geordneten Beenden mit Strg-C aendert das nichts; "
              f"wird dieses Fenster hart geschlossen, koennen sie "
              f"weiterlaufen.")
        return None


def in_job_haengen(job, pid: int) -> None:
    """Einen gestarteten Prozess in den Job stecken. Ohne Job: nichts zu tun."""
    if not job:
        return
    try:
        k32 = _kernel32()
        griff = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                                False, pid)
        if not griff:
            return
        try:
            k32.AssignProcessToJobObject(job, griff)
        finally:
            k32.CloseHandle(griff)
    except Exception:
        # Schon der Aufbau des Jobs hat gemeldet, wenn etwas fehlt. Hier
        # nochmal zu meckern, waere dieselbe Nachricht dreimal.
        pass


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"Dieses Programm braucht Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} "
              f"oder neuer, gefunden wurde {sys.version.split()[0]} "
              f"({sys.executable}).", file=sys.stderr)
        if MAC:
            print("Auf dem Mac: `brew install python` oder python.org/downloads.",
                  file=sys.stderr)
        elif WINDOWS:
            print("Unter Windows: python.org/downloads oder `winget install "
                  "Python.Python.3.12`.", file=sys.stderr)
        return 2

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
    ap.add_argument("--open", action="store_true", dest="oeffnen",
                    help="die Oberflaeche im Standardbrowser aufmachen")
    a = ap.parse_args()

    probe_host = "127.0.0.1" if a.host == "0.0.0.0" else a.host
    grund = port_belegt(probe_host, a.port)
    if grund:
        print(f"Port {a.port} ist auf {probe_host} nicht frei: {grund}",
              file=sys.stderr)
        print(f"Laeuft schon ein Tally-Pi? Sonst: "
              f"`{Path(sys.executable).name} {Path(__file__).name} "
              f"--port {a.port + 1}`.", file=sys.stderr)
        return 2

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
    # Umlaute in Geraetenamen sollen auf jeder Konsole ankommen. Windows
    # startet Python sonst mit der Codepage des Fensters (cp1252), und dann
    # bricht eine `print`-Zeile mit „Kamera Süd" den Prozess ab.
    umgebung["PYTHONIOENCODING"] = "utf-8"

    tally = conf / "tally.json"
    if not tally.exists():
        cfg = dict(DEMO_TALLY)
        cfg["atem_ip"] = a.atem
        tally.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"[local] Beispiel-Konfiguration angelegt: {tally}")
    elif a.atem:
        # Eine vorhandene Konfiguration wird NICHT ueberschrieben — nur die
        # eine Angabe, die auf der Kommandozeile stand.
        cfg = json.loads(tally.read_text(encoding="utf-8"))
        cfg["atem_ip"] = a.atem
        tally.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"[local] atem_ip auf {a.atem} gesetzt")

    if not (conf / "bindings.json").exists():
        (conf / "bindings.json").write_text(json.dumps({"bindings": []}, indent=2),
                                            encoding="utf-8")

    for name in ("setup-guide.html",):
        ziel = conf / name
        if not ziel.exists() and (ROOT / name).exists():
            ziel.write_bytes((ROOT / name).read_bytes())

    if a.demo:
        (state / "atem.json").write_text(json.dumps(DEMO_ATEM), encoding="utf-8")
        print("[local] Beispiel-Zustand abgelegt (drei Kameras, PGM 1 / PVW 2)")

    # Eine Port-Datei aus einem frueheren Lauf zeigt auf einen Watcher, den
    # es nicht mehr gibt. Sie liegt im Zustandsverzeichnis, das absichtlich
    # nicht geleert wird — also genau hier weg, wo ein neuer Lauf beginnt.
    for rest in ("atem-cmd.port", "atem-cmd.sock"):
        try:
            (state / rest).unlink()
        except OSError:
            pass

    prozesse: "list[subprocess.Popen]" = []
    job = windows_job()

    # Unter Windows bekommt jeder Prozess im Konsolenfenster das Strg-C ab.
    # Die Kinder wuerden dann mitten im Schreiben abbrechen und jeweils einen
    # Stapelabzug hinterlassen — drei Stueck, ueber die eigentliche Meldung
    # gescrollt. Mit einer eigenen Prozessgruppe hoert nur dieses Programm
    # auf Strg-C und beendet die Kinder selbst, in Ruhe.
    kind_flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0

    def starte(datei: str, *args: str) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, str(ROOT / datei), *args],
                             env=umgebung, cwd=str(ROOT),
                             creationflags=kind_flags)
        prozesse.append(p)
        in_job_haengen(job, p.pid)
        print(f"[local] gestartet: {datei} (pid {p.pid})")
        return p

    # Der ATEM-Watcher wuerde den stehenden Beispiel-Zustand sofort mit
    # „nicht verbunden" ueberschreiben — deshalb laeuft er im Demo-Betrieb
    # nicht. Das ist keine Vertuschung: ohne ihn steht auch keine
    # Verbindungsmeldung da, die es zu glauben gaebe.
    if not a.demo:
        starte("atem_watcher.py")
    if not a.no_gpio:
        starte("gpio_watcher.py")
    starte("guide_server.py")

    lan = lan_adresse()
    adresse = f"http://127.0.0.1:{a.port}/"
    print()
    print("  Tally-Pi laeuft lokal.")
    print(f"    hier:            {adresse}")
    if a.host == "0.0.0.0":
        print(f"    im selben Netz:  http://{lan}:{a.port}/")
    print(f"    Konfiguration:   {conf}")
    print(f"    Zustand:         {state}")
    if a.host == "0.0.0.0" and (MAC or WINDOWS):
        # Beide Systeme fragen beim ersten Binden nach. Wer die Frage
        # wegklickt, hat danach eine Seite, die nur auf diesem Rechner
        # aufgeht — und keinen Hinweis darauf, warum.
        wo = "macOS" if MAC else "Windows"
        print(f"    Hinweis:         {wo} fragt gleich, ob Python Verbindungen "
              f"annehmen darf.")
        print(f"                     Fuers Handy im selben Netz: erlauben. "
              f"Nur dieser Rechner: --host 127.0.0.1.")
    print()
    print(f"  Zum Beenden {'Ctrl-C' if MAC else 'Strg-C'}.")
    print()

    if a.oeffnen:
        try:
            webbrowser.open(adresse)
        except Exception as e:          # headless, kein Browser, ssh-Sitzung
            print(f"[local] Browser liess sich nicht oeffnen ({e}) — "
                  f"die Adresse steht oben.")

    beendet = {"flag": False}

    def beenden(code: int = 0):
        if beendet["flag"]:
            return
        beendet["flag"] = True
        for p in prozesse:
            try:
                p.terminate()
            except OSError:
                pass
        for p in prozesse:
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except OSError:
                    pass
        sys.exit(code)

    def auf_signal(*_):
        beenden(0)

    signal.signal(signal.SIGINT, auf_signal)
    signal.signal(signal.SIGTERM, auf_signal)
    try:
        while True:
            for p in prozesse:
                if p.poll() is not None:
                    print(f"[local] Prozess {p.args[1]} beendet "
                          f"(Code {p.returncode})")
                    # Der Code des gestorbenen Kindes wird weitergereicht.
                    # `start-local.bat` haelt das Fenster nur bei einem
                    # Fehler offen — mit einer stillen 0 stuende die Meldung
                    # in einem Fenster, das sich im selben Moment schliesst.
                    beenden(1 if p.returncode else 0)
            time.sleep(0.5)
    except KeyboardInterrupt:
        # Unter Windows kommt Strg-C je nach Konsole als Ausnahme statt
        # ueber den Signal-Handler an. Beide Wege enden hier gleich.
        beenden(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
