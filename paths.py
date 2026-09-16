#!/usr/bin/env python3
"""
Wo die Dateien liegen — EINE Stelle statt neunzehn.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-09) ────────────────────────────────

„Tally pi muss man auch lokal starten koennen. Nicht nur an raspberry."

─── WAS NACHGEREICHT WURDE (Nutzer, 2026-09-12) ────────────────────────────

„baue die app so das sie auch auf mac und windows lokal server starten kann"

─── WARUM DAS VORHER NICHT GING ────────────────────────────────────────────

Neunzehn absolute Pfade, verteilt auf fuenf Dateien: `/opt/pi-guide/…` fuer
die Konfiguration, `/run/pi-guide/…` fuer den Laufzeit-Zustand. Beide
gehoeren auf einem gewoehnlichen Rechner root, `/run` ist ausserdem ein
tmpfs, das es unter Windows und macOS gar nicht gibt. Wer die Anwendung am
Schreibtisch ansehen wollte, musste sie als root gegen Systemverzeichnisse
starten — und riskierte dabei, eine echte Installation zu ueberschreiben.
Genau davor warnte `docs/screenshots.py` lange: „never run it on a Pi that
is actually in service." Seit dem 2026-09-12 muss es das nicht mehr — es
benutzt dieselben zwei Variablen wie jeder lokale Start.

Das ist der eigentliche Punkt, und er ist nicht Bequemlichkeit: eine
Anwendung, die sich nur auf der Zielhardware starten laesst, laesst sich
auch nur dort ausprobieren. Jede Aenderung braucht dann einen Pi, und was
keinen Pi hat, wird nicht ausprobiert.

─── WIE ES JETZT LIEGT ─────────────────────────────────────────────────────

Zwei Umgebungsvariablen, und Vorgaben, die zur Plattform passen:

    PI_GUIDE_CONF    Konfiguration     Vorgabe siehe Tabelle
    PI_GUIDE_STATE   Laufzeit-Zustand  Vorgabe siehe Tabelle

    Plattform   Konfiguration                              Zustand
    ─────────   ─────────────────────────────────────────  ───────────────────
    Linux/Pi    /opt/pi-guide                              /run/pi-guide
    macOS       ~/Library/Application Support/tally-pi/…   …/state
    Windows     %LOCALAPPDATA%\\tally-pi\\conf              …\\state
    sonst       $XDG_*_HOME/tally-pi (BSD u.a.)

Auf Linux bleibt ALLES wie es war — die systemd-Units, `bootstrap.sh` und
die laufenden Pis merken von dieser Datei nichts. Die anderen Zweige
greifen nur dort, wo die Pi-Pfade ohnehin nicht existieren: `/opt` und
`/run` gehoeren auf einem Mac root, und unter Windows landete `/run/…`
still auf `C:\\run\\…` — ein Verzeichnis, das niemand erwartet und das der
angemeldete Nutzer oft nicht anlegen darf.

EIN UNTERSCHIED BLEIBT, UND ER WIRD HIER GESAGT: `/run` auf dem Pi ist ein
tmpfs, der Zustand ist dort nach dem Booten weg. Auf macOS und Windows gibt
es kein tmpfs an fester Stelle; der Zustand liegt auf der Platte und
ueberlebt einen Neustart. Fuer den Blick auf die Oberflaeche am
Schreibtisch ist das folgenlos — nur `cue.json` ist davon betroffen, und
wer den Unterschied pruefen will, findet ihn in
`tests/test_plattformen.py` benannt.

`run-local.py` setzt beide Variablen ohnehin auf ein Verzeichnis im
Arbeitsordner und startet damit dieselben Programme, die auf dem Pi laufen.
Kein zweiter Codepfad, kein „Entwicklungsmodus" mit eigenem Verhalten: es
sind dieselben Dateien an einem anderen Ort.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping, Optional, Tuple

#: Pi-Vorgaben. Sie stehen als Konstanten da, weil sie in `bootstrap.sh`,
#: den systemd-Units und jeder laufenden Installation wortgleich vorkommen.
PI_CONF = "/opt/pi-guide"
PI_STATE = "/run/pi-guide"


def default_dirs(plattform: str = sys.platform,
                 umgebung: Optional[Mapping[str, str]] = None,
                 home: Optional[str] = None) -> Tuple[str, str]:
    """Die Vorgabepfade fuer eine Plattform — als reine Funktion.

    Reine Funktion und mit Parametern, damit ein Test auf Linux auch die
    Windows- und macOS-Vorgabe pruefen kann. Ein Zweig, der nur auf der
    Plattform pruefbar ist, auf der er laeuft, wird genau dann falsch,
    wenn niemand hinsieht.
    """
    umgebung = os.environ if umgebung is None else umgebung
    heim_text = home if home is not None else os.path.expanduser("~")
    heim = PurePosixPath(heim_text)

    if plattform.startswith("linux"):
        # UNVERAENDERT. Jede installierte Pi-Einheit zeigt hierhin.
        return PI_CONF, PI_STATE

    if plattform == "darwin":
        basis = heim / "Library" / "Application Support" / "tally-pi"
        return str(basis / "conf"), str(basis / "state")

    if plattform in ("win32", "cygwin", "msys"):
        # `PureWindowsPath` und nicht `Path`: die Funktion soll auf JEDER
        # Plattform dieselbe Windows-Antwort geben, damit ein Test auf Linux
        # den Windows-Zweig wirklich pruefen kann. `Path` waere dort eine
        # PosixPath und lieferte `C:\\Users\\ada/tally-pi` — halb richtig ist
        # bei Pfaden dasselbe wie falsch.
        lokal = umgebung.get("LOCALAPPDATA") or str(
            PureWindowsPath(heim_text) / "AppData" / "Local")
        basis = PureWindowsPath(lokal) / "tally-pi"
        return str(basis / "conf"), str(basis / "state")

    # BSD und alles Uebrige: die XDG-Konvention, die dort ebenfalls gilt.
    daten = umgebung.get("XDG_DATA_HOME") or str(heim / ".local" / "share")
    laufzeit = umgebung.get("XDG_RUNTIME_DIR")
    basis = PurePosixPath(daten) / "tally-pi"
    zustand = (PurePosixPath(laufzeit) / "tally-pi") if laufzeit else (basis / "state")
    return str(basis / "conf"), str(zustand)


_CONF_VORGABE, _STATE_VORGABE = default_dirs()

#: Wo Konfiguration und Protokoll liegen (vom Installer beschrieben).
CONF_DIR = Path(os.environ.get("PI_GUIDE_CONF", _CONF_VORGABE))
#: Wo der fluechtige Laufzeit-Zustand liegt (von den Watchern beschrieben).
STATE_DIR = Path(os.environ.get("PI_GUIDE_STATE", _STATE_VORGABE))

# ── Konfiguration ──────────────────────────────────────────────────────────
TALLY_FILE = CONF_DIR / "tally.json"
BINDINGS_FILE = CONF_DIR / "bindings.json"
EVENTS_LOG = CONF_DIR / "events.log"
SETUP_HTML = CONF_DIR / "setup-guide.html"

# ── Laufzeit ───────────────────────────────────────────────────────────────
ATEM_STATE = STATE_DIR / "atem.json"
ATEM_CMD_SOCK = STATE_DIR / "atem-cmd.sock"
#: Nur unter Windows benutzt — dort gibt es kein AF_UNIX, der Befehlskanal
#: liegt auf einem Loopback-Port und dessen Nummer steht in dieser Datei.
#: Siehe `cmd_channel.py`.
ATEM_CMD_PORT = STATE_DIR / "atem-cmd.port"
NUMATO_STATE = STATE_DIR / "numato.json"
INPUT_STATE = STATE_DIR / "input-state.json"
CUE_FILE = STATE_DIR / "cue.json"


def ensure_dirs() -> None:
    """
    Die beiden Verzeichnisse anlegen, wenn sie fehlen.

    Auf dem Pi legt sie `bootstrap.sh` an und dieser Aufruf tut nichts.
    Lokal gibt es sie beim ersten Start noch nicht — und ein Programm, das
    daran scheitert, statt das Verzeichnis anzulegen, waere nur laestig.

    Schlaegt es fehl (keine Rechte auf `/run` als gewoehnlicher Nutzer),
    bleibt es still: der Aufrufer merkt es beim ersten Schreibversuch, und
    dort steht die bessere Meldung.
    """
    for d in (CONF_DIR, STATE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


# ── Schreiben und Lesen, ueberall gleich ───────────────────────────────────
#
# WARUM DAS HIER STEHT UND NICHT VIERMAL IM PROGRAMM.
#
# Vier Programme schrieben denselben Dreisatz (temporaere Datei, schreiben,
# `os.replace`) in vier Abschriften. Unter Windows hat dieser Dreisatz eine
# Eigenheit, die er unter Linux nicht hat: `os.replace` scheitert mit
# „Zugriff verweigert", solange ein ANDERER Prozess die Zieldatei geoeffnet
# hat. Genau das passiert hier staendig — `atem_watcher` schreibt
# `atem.json` zehnmal in der Sekunde, waehrend `guide_server` sie fuer jede
# Anfrage der Oberflaeche liest.
#
# Auf dem Pi faellt das nie auf. Unter Windows waere der lokale Start nach
# ein paar Minuten mit einem Stapelabzug im ATEM-Watcher gestorben, und die
# Meldung haette nach einem Rechtefehler ausgesehen statt nach dem, was es
# ist: zwei Prozesse an einer Datei. Deshalb einige wenige Wiederholungen.
# Sie sind kurz genug, dass ein echter Rechtefehler trotzdem schnell
# durchschlaegt.
_WIEDERHOLUNGEN = 8
_WARTEN_S = 0.02


def atomic_write_json(path: Path, payload, *, indent=None, mode=0o664) -> None:
    """JSON vollstaendig oder gar nicht — der Leser sieht nie ein Fragment."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
        letzter = None
        for versuch in range(_WIEDERHOLUNGEN):
            try:
                os.replace(tmp, path)
                letzter = None
                break
            except OSError as e:          # Windows: Leser haelt die Datei
                letzter = e
                time.sleep(_WARTEN_S * (versuch + 1))
        if letzter is not None:
            raise letzter
        try:
            os.chmod(path, mode)
        except OSError:
            # Windows kennt keine Gruppenrechte, und auf dem Pi gehoert die
            # Datei manchmal einem anderen Dienst. Beides ist kein Grund,
            # den Schreibvorgang fuer gescheitert zu erklaeren.
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    """JSON lesen und bei Unfug die Vorgabe liefern — ohne Ausnahme."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return default
