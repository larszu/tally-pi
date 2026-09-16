#!/usr/bin/env python3
"""
Der Befehlskanal zum ATEM-Watcher — auf jedem Betriebssystem einer.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-12) ────────────────────────────────

„baue die app so das sie auch auf mac und windows lokal server starten kann"

─── WAS HIER DAS PROBLEM WAR ───────────────────────────────────────────────

`gpio_watcher` und `guide_server` schicken dem `atem_watcher` Befehle
(`set_aux`, `set_program`, `set_preview`) — eine JSON-Zeile durch ein
Unix-Socket unter `/run/pi-guide/atem-cmd.sock`. Das ist auf dem Pi genau
richtig: kein Port im Netz, Rechte ueber die Dateirechte, und wer den
Befehlsweg von Hand pruefen will, tut es mit `nc -U`.

Unter Windows gibt es `socket.AF_UNIX` in Python nicht. Der Ausdruck
`socket.AF_UNIX` wirft dort `AttributeError` — und zwar an drei Stellen mit
drei verschiedenen Folgen: der Watcher meldete „bind failed" und der
Befehlsweg war still weg, waehrend die Oberflaeche beim Ausloesen einen
Stapelabzug zurueckgab. Ein Kanal, der auf einem Betriebssystem still
fehlt, ist schlimmer als einer, der fehlt und es sagt.

─── WIE ER JETZT LIEGT ─────────────────────────────────────────────────────

    POSIX (Linux, macOS)   Unix-Socket   <STATE_DIR>/atem-cmd.sock
    Windows                Loopback-TCP  127.0.0.1:<vom System vergeben>
                                         Port steht in <STATE_DIR>/atem-cmd.port

Das Drahtformat ist auf beiden Wegen dasselbe: EINE JSON-Zeile hin, EINE
JSON-Zeile zurueck. Wer auf dem Pi bisher `nc -U /run/pi-guide/atem-cmd.sock`
benutzt hat, benutzt es weiter — auf dem Pi aendert sich nichts.

WARUM DER PORT VOM SYSTEM KOMMT UND NICHT FEST IST. Ein fester Port waere
belegt, sobald ein zweiter lokaler Start laeuft, und zwei Watcher an einem
Port sind schlimmer als ein Fehler: Befehle gingen an den falschen. Das
System vergibt einen freien, der Watcher schreibt ihn hin, die Sender lesen
ihn. Ist die Datei alt (Watcher tot), schlaegt das Verbinden fehl und die
Meldung sagt genau das.

WER DARF SENDEN. Auf dem Pi jeder lokale Nutzer (das Socket steht auf 0666,
mit Absicht — siehe `atem_watcher.py`). Der Loopback-Port ist an
`127.0.0.1` gebunden und damit ebenfalls lokal und nur lokal: aus dem Netz
ist er nicht erreichbar. Das ist dieselbe Zusage, nicht eine schwaechere.

`PI_GUIDE_CMD_TCP=1` erzwingt den TCP-Weg auch auf POSIX. Das ist kein
Schalter fuer Benutzer, sondern fuer `tests/test_plattformen.py`: der
Windows-Zweig wird damit auf der Linux-CI wirklich durchlaufen, statt nur
behauptet zu werden.
"""
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

#: Windows hat kein AF_UNIX in Python — dort ist der TCP-Weg der einzige.
UNIX_MOEGLICH = hasattr(socket, "AF_UNIX")


def tcp_modus() -> bool:
    """True, wenn der Kanal ueber einen Loopback-Port laeuft."""
    if os.environ.get("PI_GUIDE_CMD_TCP") == "1":
        return True
    return not UNIX_MOEGLICH


def beschreibung() -> str:
    """Der Kanal in einer Zeile — fuer Protokoll und Fehlermeldung."""
    if tcp_modus():
        port = _gemerkter_port()
        wo = f"127.0.0.1:{port}" if port else f"{paths.ATEM_CMD_PORT}"
        return f"tcp {wo}"
    return f"unix {paths.ATEM_CMD_SOCK}"


# ── Die Seite, die zuhoert (atem_watcher) ──────────────────────────────────

def listen(backlog: int = 8) -> socket.socket:
    """Den Kanal aufmachen und das horchende Socket zurueckgeben.

    Wirft, wenn es nicht geht — der Aufrufer meldet das. Ein Kanal, der
    lautlos nicht zustande kommt, laesst jeden spaeteren Tastendruck ins
    Leere laufen, ohne dass irgendwo steht, warum.
    """
    if tcp_modus():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # KEIN SO_REUSEADDR: unter Windows erlaubt es zwei Prozessen
        # denselben Port, und dann bekommt einer von beiden die Befehle —
        # welcher, ist nicht vorhersagbar. Lieber ein Fehler beim Binden.
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(backlog)
        paths.atomic_write_json(paths.ATEM_CMD_PORT,
                                {"port": port, "pid": os.getpid()})
        return srv

    pfad = paths.ATEM_CMD_SOCK
    pfad.parent.mkdir(parents=True, exist_ok=True)
    try:
        pfad.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    try:
        # 0o666: jeder lokale Nutzer darf Befehle schicken. Das ist ein
        # Socket im Geraet (nicht im Netz erreichbar), und der regulaere
        # Pi-Nutzer soll `set_aux` ohne sudo pruefen koennen.
        os.chmod(str(pfad), 0o666)
    except OSError:
        pass
    srv.listen(backlog)
    return srv


def aufraeumen() -> None:
    """Die Spur des Kanals entfernen — beim geordneten Beenden."""
    for p in (paths.ATEM_CMD_PORT if tcp_modus() else paths.ATEM_CMD_SOCK,):
        try:
            Path(p).unlink()
        except OSError:
            pass


# ── Die Seite, die sendet (gpio_watcher, guide_server) ─────────────────────

def _gemerkter_port():
    d = paths.read_json(paths.ATEM_CMD_PORT)
    if isinstance(d, dict) and isinstance(d.get("port"), int):
        return d["port"]
    return None


def verbinde(timeout: float = 2.0) -> socket.socket:
    """Ein verbundenes Socket zum Watcher — oder ein RuntimeError mit Grund."""
    if tcp_modus():
        port = _gemerkter_port()
        if not port:
            raise RuntimeError(
                f"{paths.ATEM_CMD_PORT} nicht vorhanden oder leer "
                f"(atem_watcher laeuft nicht?)")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
        except OSError as e:
            s.close()
            # Die haeufigste Lage unter Windows: die Port-Datei ist von einem
            # frueheren Lauf uebrig. Das gehoert in die Meldung, sonst sucht
            # jemand den Fehler im Netzwerk.
            raise RuntimeError(
                f"127.0.0.1:{port} antwortet nicht ({e}) — atem_watcher "
                f"laeuft nicht, oder {paths.ATEM_CMD_PORT} ist von einem "
                f"frueheren Lauf uebrig")
        return s

    pfad = paths.ATEM_CMD_SOCK
    if not pfad.exists():
        raise RuntimeError(f"{pfad} nicht vorhanden (atem_watcher laeuft nicht?)")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(pfad))
    except OSError as e:
        s.close()
        raise RuntimeError(f"{pfad} antwortet nicht ({e})")
    return s


def sende(cmd: dict, timeout: float = 2.0) -> dict:
    """Einen Befehl schicken und die Antwort auswerten.

    Kommt eine Antwort mit `ok: false`, wird sie zu einem RuntimeError mit
    dem Grund des Watchers — der Aufrufer protokolliert ihn und die
    Oberflaeche zeigt ihn an. Stillschweigen waere hier das Schlimmste: der
    Knopf saehe gedrueckt aus und der Mischer haette nichts bekommen.
    """
    s = verbinde(timeout)
    try:
        s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        antwort = s.recv(4096).decode("utf-8", errors="replace").strip()
    finally:
        try:
            s.close()
        except OSError:
            pass
    if not antwort:
        return {}
    try:
        j = json.loads(antwort.split("\n", 1)[0])
    except json.JSONDecodeError:
        return {}
    if isinstance(j, dict) and not j.get("ok"):
        raise RuntimeError(j.get("error", "atem cmd rejected"))
    return j if isinstance(j, dict) else {}
