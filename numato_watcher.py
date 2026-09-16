#!/usr/bin/env python3
"""
Der Numato-Watcher — GPIO ueber ein USB-Board, auf Linux, macOS und Windows.

─── WOZU ───────────────────────────────────────────────────────────────────

Auf dem Pi sind die Tally-Ein- und -Ausgaenge der 40-polige Stecker
(`gpio_watcher.py` liest Taster, `guide_server.py` treibt Lampen ueber
libgpiod). Ein Mac oder Windows-Rechner hat diesen Stecker nicht — aber einen
USB-Anschluss und daran ein Numato-USB-GPIO-Board. Dieser Dienst macht daraus
DIESELBEN Funktionen:

  * Taster lesen  → Flanke → ATEM-Befehl (ueber den ATEM-Befehlskanal) oder
                    Companion-Druck (HTTP). Die Rechnung ist `tally_actions`,
                    Wort fuer Wort dieselbe wie beim Pi-Watcher.
  * Tally-Lampen  → der Guide-Server schickt „Kanal X an/aus" ueber den
                    Numato-Befehlskanal (`cmd_channel.NUMATO`), dieser Dienst
                    setzt den Ausgang.

─── EIN BOARD, EIN BESITZER ────────────────────────────────────────────────

Ein serieller Anschluss laesst sich nur von EINEM Prozess oeffnen. Am Pi
teilen sich Ein- und Ausgaenge die Leitungen ueber den Kernel (libgpiod gibt
jede Leitung getrennt heraus); an einem USB-Board geht das nicht — nur einer
haelt den Port. Deshalb besitzt DIESER Dienst das Board und tut beides:
er pollt die Eingaenge UND setzt die Ausgaenge, beides durch dieselbe
serielle Leitung, serialisiert ueber ein Schloss. Der Guide-Server treibt die
Lampen nicht selbst, sondern schickt sie hierher — genau wie er ATEM-Befehle
an den ATEM-Watcher schickt.

─── WAS FEHLT, FEHLT SICHTBAR ──────────────────────────────────────────────

Ohne pyserial oder ohne angestecktes Board taeuscht dieser Dienst nichts vor.
Er STIRBT ABER NICHT — sonst risse er unter `run-local.py` den ganzen lokalen
Start mit sich (der beendet sich, wenn ein Kind mit Fehler endet). Er laeuft
weiter, meldet in `numato.json` `connected: false` samt Grund, und die
Oberflaeche zeigt das an. Sobald ein Board auftaucht, greift er es beim
naechsten Durchlauf.

Die Kanalnummern kommen aus denselben Feldern wie am Pi: `out_gpio` eines
Geraets ist am Numato die AUSGANGS-Kanalnummer, `in_gpio` die EINGANGS-
Kanalnummer (0..31). Dieselbe Zahl, die am Pi eine BCM-Leitung meint.
"""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import cmd_channel  # noqa: E402
import tally_actions  # noqa: E402
import numato_io  # noqa: E402
from gpio_watcher import BurstTracker  # noqa: E402  (reine Threading-Logik, kein gpiod)

BINDINGS = paths.BINDINGS_FILE
TALLY_CONFIG = paths.TALLY_FILE
STATE_FILE = paths.NUMATO_STATE
EVENT_LOG_FILE = paths.EVENTS_LOG
COMPANION = "http://localhost:8000"
POLL_INTERVAL = 0.05   # 50 ms
ADC_POLL_EVERY = 4     # jeder N-te Poll liest ADC (~5 Hz bei 50 ms / 4)


def log(msg):
    print(msg, flush=True)


def log_event(kind, **fields):
    """Eine JSON-Zeile ans gemeinsame Ereignislog (dieselbe Datei wie sonst)."""
    rec = {"ts": time.time(), "kind": kind, "src": "numato-watcher"}
    rec.update(fields)
    try:
        EVENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def http_post(url, data=b""):
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def atem_cmd(cmd):
    """ATEM-Befehl an den ATEM-Watcher — derselbe Kanal wie beim Pi-Watcher."""
    cmd_channel.sende(cmd)


def _load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log(f"{path.name} parse error: {e}")
        return None


def load_config():
    """(Eingangs-Bindungen, Ausgangs-Kanaele) aus tally.json + bindings.json.

    Eingaenge: `tally.json`-Geraete mit `in_gpio` (via `tally_actions`, Quelle
    "numato" -> Schluessel "channel") und Alt-Bindungen aus `bindings.json`
    mit `source == "numato"` (Companion). Ausgaenge: `out_gpio` der Geraete.
    """
    input_bindings = []
    seen = set()

    tally = _load_json(TALLY_CONFIG) or {}
    for dev in (tally.get("devices") or []):
        if not isinstance(dev, dict):
            continue
        b = tally_actions.device_to_binding(dev, source="numato")
        if b is None:
            continue
        input_bindings.append(b)
        seen.add(b["channel"])

    legacy = _load_json(BINDINGS) or []
    for b in legacy if isinstance(legacy, list) else []:
        if not isinstance(b, dict) or b.get("source") != "numato":
            continue
        ch = b.get("channel")
        if not isinstance(ch, int) or ch in seen:
            continue
        b = dict(b)
        b.setdefault("trigger_edge", "falling")
        b.setdefault("enabled", True)
        b.setdefault("hold_release_ms", 0)
        b.setdefault("burst_min_edges", 1)
        b.setdefault("_label", f"numato CH{ch}")
        input_bindings.append(b)
        seen.add(ch)

    output_channels = sorted({
        d.get("out_gpio") for d in (tally.get("devices") or [])
        if isinstance(d, dict) and isinstance(d.get("out_gpio"), int)
    })
    return input_bindings, output_channels


def config_mtime():
    total = 0.0
    for p in (BINDINGS, TALLY_CONFIG):
        try:
            total += p.stat().st_mtime
        except FileNotFoundError:
            pass
    return total


class NumatoManager:
    """Haelt das Board und ist der einzige, der es anspricht.

    Ein Schloss serialisiert alles auf der seriellen Leitung: das Pollen der
    Eingaenge (Hauptfaden) und das Setzen der Ausgaenge (Befehlsfaden). Der
    Befehlsfaden lebt ueber Wiederverbindungen hinweg; nur `self.board`
    wechselt.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.board = None
        self.device = None
        self.session = 0            # zaehlt bei jedem erfolgreichen Anstecken hoch
        self.output_channels = []
        self.output_values = {}     # channel -> bool (zuletzt geschrieben)
        self.last_error = None

    # ── Board an-/abmelden ───────────────────────────────────────────────────
    def attach(self, board, device, output_channels, input_channels):
        with self.lock:
            self.board = board
            self.device = device
            self.session += 1
            self.output_channels = list(output_channels)
            self.output_values = {}
            self.last_error = None
            self._configure_locked(output_channels, input_channels)

    def detach(self, error=None):
        with self.lock:
            self.board = None
            self.device = None
            self.output_values = {}
            if error:
                self.last_error = error

    def _configure_locked(self, output_channels, input_channels):
        """iomask/iodir setzen und die Ausgaenge auf AUS. Schloss haelt Aufrufer."""
        if self.board is None:
            return
        try:
            # Breite lernen (setzt board.breite) und dann Richtung festlegen:
            # 0 = Ausgang, 1 = Eingang. iomask ffff… = alle Bits anfassen.
            self.board.read_all()
            breite = self.board.breite or 32
            alle = (1 << breite) - 1
            iodir = alle
            for ch in output_channels:
                if 0 <= ch < breite:
                    iodir &= ~(1 << ch)
            self.board.set_iomask(alle)
            self.board.set_iodir(iodir)
            # Ausgaenge in einen bekannten Zustand: alle aus.
            for ch in output_channels:
                self.board.clear_channel(ch)
                self.output_values[ch] = False
            log(f"numato configured: outputs={list(output_channels)} "
                f"inputs={list(input_channels)} width={breite}")
        except Exception as e:
            self.last_error = f"configure failed: {e}"
            log(f"numato {self.last_error}")

    def reconfigure_outputs(self, output_channels, input_channels):
        """Nach einer Konfig-Aenderung Richtung neu setzen (Board bleibt offen)."""
        with self.lock:
            self.output_channels = list(output_channels)
            self._configure_locked(output_channels, input_channels)

    # ── Ausgaenge (vom Guide-Server ueber den Befehlskanal) ───────────────────
    def set_output(self, channel, on):
        """Einen Ausgangskanal setzen. Schreibt seriell NUR bei echter
        Aenderung — so darf der Guide-Server bedenkenlos oft senden, und nach
        einem Neustart dieses Dienstes (leerer Cache) schreibt der erste
        Befehl wieder wirklich."""
        with self.lock:
            if self.board is None:
                return False, "kein Numato-Board verbunden"
            if not isinstance(channel, int):
                return False, "channel muss eine Zahl sein"
            if self.output_values.get(channel) == bool(on):
                return True, "unveraendert"
            try:
                if on:
                    self.board.set_channel(channel)
                else:
                    self.board.clear_channel(channel)
                self.output_values[channel] = bool(on)
                return True, "ok"
            except Exception as e:
                self.last_error = f"set_output failed: {e}"
                return False, str(e)

    def handle_cmd(self, cmd):
        """Einen Befehl vom Numato-Kanal beantworten (dict -> dict)."""
        art = cmd.get("cmd")
        if art in ("set", "clear"):
            on = bool(cmd.get("on")) if art == "set" else False
            if art == "set" and "on" not in cmd:
                on = True
            ok, msg = self.set_output(cmd.get("channel"), on)
            # Bei Misserfolg gehoert der Grund unter `error` — so schlaegt er
            # ueber `cmd_channel.sende` beim Guide-Server als echter Grund
            # durch, statt als das nichtssagende „cmd rejected".
            antwort = {"ok": ok, "session": self.session}
            antwort["message" if ok else "error"] = msg
            return antwort
        if art == "ping":
            with self.lock:
                return {"ok": self.board is not None, "session": self.session,
                        "device": self.device}
        return {"ok": False, "error": f"unbekannter Befehl {art!r}"}

    # ── Eingaenge (Hauptfaden pollt) ─────────────────────────────────────────
    def read_all(self):
        with self.lock:
            if self.board is None:
                return None
            try:
                return self.board.read_all()
            except Exception as e:
                self.last_error = f"readall failed: {e}"
                return None


def _edges_for_burst(binding):
    trig = binding.get("trigger_edge", "falling")
    if trig == "rising":
        return "rising", "falling"
    return "falling", "rising"


def _run_action(binding, etype, edge_count=None):
    tally_actions.run_action(
        binding, etype,
        log=log, log_event=log_event,
        atem_cmd=atem_cmd, http_post=http_post, companion=COMPANION,
        edge_count=edge_count,
        log_fields={"channel": binding.get("channel")},
    )


def _build_trackers(bindings, letzter_stand):
    """{channel: BurstTracker} fuer Bindungen mit hold_release_ms > 0.

    `letzter_stand` ist eine Funktion channel -> aktueller Pegel (0/1) aus dem
    letzten Poll — der Tracker fragt sie, ob die Leitung noch gedrueckt ist.
    """
    trackers = {}
    for b in bindings:
        ch = b.get("channel")
        hold_ms = int(b.get("hold_release_ms", 0) or 0)
        if not isinstance(ch, int) or hold_ms <= 0:
            continue
        press_e, release_e = _edges_for_burst(b)

        def make_press(binding=b, e=press_e):
            return lambda: _run_action(binding, e)

        def make_release(binding=b, e=release_e):
            def cb(count):
                _run_action(binding, e, edge_count=count)
            return cb

        # Ruhepegel: pull-up -> Ruhe = HIGH (1), gedrueckt = LOW (0).
        bias = b.get("bias", "pull-up")

        def make_idle(c=ch, bias_=bias):
            ruhe = 1 if bias_ != "pull-down" else 0
            def check():
                try:
                    return letzter_stand(c) == ruhe
                except Exception:
                    return True  # fail-open -> Freigabe feuert
            return check

        trackers[ch] = BurstTracker(
            label=b.get("_label") or f"numato CH{ch}",
            release_ms=hold_ms,
            run_press=make_press(),
            run_release=make_release(),
            is_at_idle=make_idle(),
            min_edges=int(b.get("burst_min_edges") or 1),
        )
    return trackers


def _dispatch(binding, etype, trackers):
    t = trackers.get(int(binding["channel"]))
    if t is not None:
        t.on_edge(etype)
    else:
        _run_action(binding, etype)


def _command_server(manager):
    """Ein Faden, der Ausgabe-Befehle vom Guide-Server annimmt — dauerhaft.

    Er ueberlebt Wiederverbindungen des Boards; `manager` haelt das jeweils
    aktuelle Board. Faellt das Zuhoeren aus, meldet er es und versucht es
    spaeter erneut.
    """
    while True:
        try:
            srv = cmd_channel.NUMATO.listen()
        except Exception as e:
            log(f"numato cmd listener bind failed "
                f"({cmd_channel.NUMATO.beschreibung()}): {e}")
            time.sleep(2)
            continue
        log(f"numato cmd listener on {cmd_channel.NUMATO.beschreibung()}")
        try:
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    break
                with conn:
                    try:
                        conn.settimeout(2.0)
                        roh = conn.recv(4096).decode("utf-8", errors="replace")
                        zeile = roh.split("\n", 1)[0].strip()
                        cmd = json.loads(zeile) if zeile else {}
                        antwort = manager.handle_cmd(cmd) if isinstance(cmd, dict) \
                            else {"ok": False, "error": "kein Objekt"}
                    except Exception as e:
                        antwort = {"ok": False, "error": str(e)}
                    try:
                        conn.sendall((json.dumps(antwort) + "\n").encode("utf-8"))
                    except OSError:
                        pass
        finally:
            try:
                srv.close()
            except OSError:
                pass


def _write_disconnected(reason):
    try:
        paths.ensure_dirs()
        paths.atomic_write_json(STATE_FILE, {
            "connected": False, "device": None,
            "error": reason, "ts": time.time(),
        })
    except OSError:
        pass


def run():
    """Board suchen, halten, pollen — und nie sterben."""
    manager = NumatoManager()
    t = threading.Thread(target=_command_server, args=(manager,),
                         name="numato-cmd", daemon=True)
    t.start()

    if not numato_io.PYSERIAL_DA:
        # Kein pyserial: es gibt kein Board zu suchen. Sagen und
        # weiterlaufen — der Befehlsfaden nimmt trotzdem an (und antwortet
        # ehrlich „kein Board"), damit der Guide-Server einen Grund bekommt.
        log(numato_io.PYSERIAL_GRUND)
        log("Ohne pyserial kein Numato. Nachruesten: pip install pyserial "
            "(auf dem Pi: apt install python3-serial).")
        while True:
            _write_disconnected(numato_io.PYSERIAL_GRUND)
            time.sleep(2)

    letzter_stand = {"bits": 0}

    def pegel(channel):
        return (letzter_stand["bits"] >> channel) & 1

    while True:
        device = numato_io.finde_geraet()
        board = None
        if device is not None:
            try:
                board = numato_io.oeffne(device)
            except Exception as e:
                log(f"numato open {device} failed: {e}")
                board = None
        if board is None:
            _write_disconnected("kein Numato-Board gefunden")
            time.sleep(2)
            continue

        log(f"numato board on {device}")
        bindings, outputs = load_config()
        enabled = [b for b in bindings if b.get("enabled", True)]
        inputs = [b["channel"] for b in enabled if isinstance(b.get("channel"), int)]
        manager.attach(board, device, outputs, inputs)
        trackers = _build_trackers(enabled, pegel)
        if trackers:
            log(f"numato burst trackers on channels {sorted(trackers)}")
        by_channel = {b["channel"]: b for b in enabled
                      if isinstance(b.get("channel"), int)}

        last_bits = manager.read_all()
        if last_bits is None:
            manager.detach("readall failed at start")
            time.sleep(2)
            continue
        letzter_stand["bits"] = last_bits
        last_mtime = config_mtime()
        adc = [None] * 32
        tick = 0
        try:
            while True:
                cur = manager.read_all()
                if cur is None:
                    log("numato readall failed — reconnecting")
                    break
                letzter_stand["bits"] = cur
                changed = cur ^ last_bits
                if changed:
                    for ch, b in by_channel.items():
                        if (changed >> ch) & 1:
                            etype = "rising" if (cur >> ch) & 1 else "falling"
                            _dispatch(b, etype, trackers)
                last_bits = cur

                if tick % ADC_POLL_EVERY == 0:
                    base = (tick // ADC_POLL_EVERY) % 8
                    for k in range(4):
                        ch = base * 4 + k
                        if ch < 32:
                            with manager.lock:
                                if manager.board is None:
                                    break
                                try:
                                    v = manager.board.read_adc(ch)
                                except Exception:
                                    v = None
                            if v is not None:
                                adc[ch] = v

                with manager.lock:
                    outvals = dict(manager.output_values)
                    sess = manager.session
                    err = manager.last_error
                paths.atomic_write_json(STATE_FILE, {
                    "connected": True,
                    "device": device,
                    "session": sess,
                    "digital": [(cur >> i) & 1 for i in range(32)],
                    "outputs": {str(k): v for k, v in outvals.items()},
                    "adc": adc,
                    "error": err,
                    "ts": time.time(),
                })

                if config_mtime() != last_mtime:
                    last_mtime = config_mtime()
                    bindings, outputs = load_config()
                    enabled = [b for b in bindings if b.get("enabled", True)]
                    inputs = [b["channel"] for b in enabled
                              if isinstance(b.get("channel"), int)]
                    for tr in trackers.values():
                        tr.cancel()
                    trackers = _build_trackers(enabled, pegel)
                    by_channel = {b["channel"]: b for b in enabled
                                  if isinstance(b.get("channel"), int)}
                    manager.reconfigure_outputs(outputs, inputs)
                    log("numato config reloaded")

                tick += 1
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            for tr in trackers.values():
                tr.cancel()
            sys.exit(0)
        finally:
            for tr in trackers.values():
                tr.cancel()
        manager.detach("board lost")
        _write_disconnected("Verbindung zum Numato verloren")
        time.sleep(2)


def main():
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
