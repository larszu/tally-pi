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

─── ZWEI KANAELE, EIN MECHANISMUS ──────────────────────────────────────────

Es gibt inzwischen einen ZWEITEN solchen Kanal: der Guide-Server schickt dem
`numato_watcher` Lampen-Befehle (`set`, `clear`), wenn die GPIO-Ausgaenge
ueber ein Numato-USB-Board laufen statt ueber den Pi-Stecker. Der Weg ist
Wort fuer Wort derselbe — nur andere Dateinamen. Deshalb steckt die ganze
Mechanik in `Kanal`, und es gibt zwei Instanzen: `ATEM` (die alten
Modul-Funktionen `listen`/`sende`/… rufen sie auf, damit sich fuer
`atem_watcher`/`gpio_watcher`/`guide_server` nichts aendert) und `NUMATO`.
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
    """True, wenn die Kanaele ueber einen Loopback-Port laufen."""
    if os.environ.get("PI_GUIDE_CMD_TCP") == "1":
        return True
    return not UNIX_MOEGLICH


class Kanal:
    """Ein JSON-Zeilen-Befehlskanal zwischen zwei lokalen Prozessen.

    `name` steht nur in Meldungen. `sock_pfad`/`port_pfad` sind die beiden
    Dateien, ueber die sich die Seiten finden (Unix-Socket bzw. die Datei mit
    der vom System vergebenen Port-Nummer). Welcher Weg gilt, entscheidet
    `tcp_modus()` — auf beiden Instanzen gleich.
    """

    def __init__(self, name, sock_pfad, port_pfad):
        self.name = name
        self.sock_pfad = sock_pfad
        self.port_pfad = port_pfad

    def beschreibung(self) -> str:
        """Der Kanal in einer Zeile — fuer Protokoll und Fehlermeldung."""
        if tcp_modus():
            port = self._gemerkter_port()
            wo = f"127.0.0.1:{port}" if port else f"{self.port_pfad}"
            return f"tcp {wo}"
        return f"unix {self.sock_pfad}"

    # ── Die Seite, die zuhoert ───────────────────────────────────────────────
    def listen(self, backlog: int = 8) -> socket.socket:
        """Den Kanal aufmachen und das horchende Socket zurueckgeben.

        Wirft, wenn es nicht geht — der Aufrufer meldet das. Ein Kanal, der
        lautlos nicht zustande kommt, laesst jeden spaeteren Befehl ins Leere
        laufen, ohne dass irgendwo steht, warum.
        """
        if tcp_modus():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # KEIN SO_REUSEADDR: unter Windows erlaubt es zwei Prozessen
            # denselben Port, und dann bekommt einer von beiden die Befehle —
            # welcher, ist nicht vorhersagbar. Lieber ein Fehler beim Binden.
            srv.bind(("127.0.0.1", 0))
            port = srv.getsockname()[1]
            srv.listen(backlog)
            paths.atomic_write_json(self.port_pfad,
                                    {"port": port, "pid": os.getpid()})
            return srv

        pfad = self.sock_pfad
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
            # Pi-Nutzer soll ohne sudo pruefen koennen.
            os.chmod(str(pfad), 0o666)
        except OSError:
            pass
        srv.listen(backlog)
        return srv

    def aufraeumen(self) -> None:
        """Die Spur des Kanals entfernen — beim geordneten Beenden."""
        ziel = self.port_pfad if tcp_modus() else self.sock_pfad
        try:
            Path(ziel).unlink()
        except OSError:
            pass

    # ── Die Seite, die sendet ────────────────────────────────────────────────
    def _gemerkter_port(self):
        d = paths.read_json(self.port_pfad)
        if isinstance(d, dict) and isinstance(d.get("port"), int):
            return d["port"]
        return None

    def verbinde(self, timeout: float = 2.0) -> socket.socket:
        """Ein verbundenes Socket zum Zuhoerer — oder RuntimeError mit Grund."""
        if tcp_modus():
            port = self._gemerkter_port()
            if not port:
                raise RuntimeError(
                    f"{self.port_pfad} nicht vorhanden oder leer "
                    f"({self.name} laeuft nicht?)")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect(("127.0.0.1", port))
            except OSError as e:
                s.close()
                # Die haeufigste Lage unter Windows: die Port-Datei ist von
                # einem frueheren Lauf uebrig. Das gehoert in die Meldung,
                # sonst sucht jemand den Fehler im Netzwerk.
                raise RuntimeError(
                    f"127.0.0.1:{port} antwortet nicht ({e}) — {self.name} "
                    f"laeuft nicht, oder {self.port_pfad} ist von einem "
                    f"frueheren Lauf uebrig")
            return s

        pfad = self.sock_pfad
        if not pfad.exists():
            raise RuntimeError(f"{pfad} nicht vorhanden ({self.name} laeuft nicht?)")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(str(pfad))
        except OSError as e:
            s.close()
            raise RuntimeError(f"{pfad} antwortet nicht ({e})")
        return s

    def sende(self, cmd: dict, timeout: float = 2.0) -> dict:
        """Einen Befehl schicken und die Antwort auswerten.

        Kommt eine Antwort mit `ok: false`, wird sie zu einem RuntimeError mit
        dem Grund des Zuhoerers — der Aufrufer protokolliert ihn und die
        Oberflaeche zeigt ihn an. Stillschweigen waere hier das Schlimmste: der
        Knopf saehe gedrueckt aus und nichts waere passiert.
        """
        s = self.verbinde(timeout)
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
            raise RuntimeError(j.get("error", "cmd rejected"))
        return j if isinstance(j, dict) else {}


#: Der ATEM-Kanal (atem_watcher hoert zu, gpio_watcher/guide_server senden).
ATEM = Kanal("atem_watcher", paths.ATEM_CMD_SOCK, paths.ATEM_CMD_PORT)

#: Der Numato-Ausgabekanal (numato_watcher hoert zu, guide_server sendet).
NUMATO = Kanal("numato_watcher", paths.NUMATO_CMD_SOCK, paths.NUMATO_CMD_PORT)


# ── Rueckwaertskompatible Modul-Funktionen: sie meinen den ATEM-Kanal ───────
# `atem_watcher`, `gpio_watcher` und `guide_server` rufen `cmd_channel.sende`,
# `.listen`, `.beschreibung`, `.aufraeumen` auf — das bleibt der ATEM-Kanal,
# damit sich an ihnen nichts aendert.
def beschreibung() -> str:
    return ATEM.beschreibung()


def listen(backlog: int = 8) -> socket.socket:
    return ATEM.listen(backlog)


def aufraeumen() -> None:
    ATEM.aufraeumen()


def verbinde(timeout: float = 2.0) -> socket.socket:
    return ATEM.verbinde(timeout)


def sende(cmd: dict, timeout: float = 2.0) -> dict:
    return ATEM.sende(cmd, timeout)
