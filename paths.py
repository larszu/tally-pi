#!/usr/bin/env python3
"""
Wo die Dateien liegen — EINE Stelle statt neunzehn.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-09) ────────────────────────────────

„Tally pi muss man auch lokal starten koennen. Nicht nur an raspberry."

─── WARUM DAS VORHER NICHT GING ────────────────────────────────────────────

Neunzehn absolute Pfade, verteilt auf fuenf Dateien: `/opt/pi-guide/…` fuer
die Konfiguration, `/run/pi-guide/…` fuer den Laufzeit-Zustand. Beide
gehoeren auf einem gewoehnlichen Rechner root, `/run` ist ausserdem ein
tmpfs, das es unter Windows und macOS gar nicht gibt. Wer die Anwendung am
Schreibtisch ansehen wollte, musste sie als root gegen Systemverzeichnisse
starten — und riskierte dabei, eine echte Installation zu ueberschreiben.
Genau davor warnt `docs/screenshots.py` bis heute: „never run it on a Pi
that is actually in service."

Das ist der eigentliche Punkt, und er ist nicht Bequemlichkeit: eine
Anwendung, die sich nur auf der Zielhardware starten laesst, laesst sich
auch nur dort ausprobieren. Jede Aenderung braucht dann einen Pi, und was
keinen Pi hat, wird nicht ausprobiert.

─── WIE ES JETZT LIEGT ─────────────────────────────────────────────────────

Zwei Umgebungsvariablen, zwei Vorgaben. Ohne sie ist alles wie bisher — die
systemd-Units, `bootstrap.sh` und die laufenden Pis merken nichts davon.

    PI_GUIDE_CONF    Konfiguration     Vorgabe /opt/pi-guide
    PI_GUIDE_STATE   Laufzeit-Zustand  Vorgabe /run/pi-guide

`run-local.py` setzt beide auf ein Verzeichnis im Home und startet damit
dieselben Programme, die auf dem Pi laufen. Kein zweiter Codepfad, kein
„Entwicklungsmodus" mit eigenem Verhalten: es sind dieselben Dateien an
einem anderen Ort.
"""
import os
from pathlib import Path

#: Wo Konfiguration und Protokoll liegen (vom Installer beschrieben).
CONF_DIR = Path(os.environ.get("PI_GUIDE_CONF", "/opt/pi-guide"))
#: Wo der fluechtige Laufzeit-Zustand liegt (von den Watchern beschrieben).
STATE_DIR = Path(os.environ.get("PI_GUIDE_STATE", "/run/pi-guide"))

# ── Konfiguration ──────────────────────────────────────────────────────────
TALLY_FILE = CONF_DIR / "tally.json"
BINDINGS_FILE = CONF_DIR / "bindings.json"
EVENTS_LOG = CONF_DIR / "events.log"
SETUP_HTML = CONF_DIR / "setup-guide.html"

# ── Laufzeit ───────────────────────────────────────────────────────────────
ATEM_STATE = STATE_DIR / "atem.json"
ATEM_CMD_SOCK = STATE_DIR / "atem-cmd.sock"
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
