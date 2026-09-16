#!/usr/bin/env python3
"""Numato 32-CH USB GPIO watcher with hot-plug support.

Findet das Geraet auf Linux, macOS UND Windows, pollt alle 32 digitalen
Kanaele + ADCs, feuert Companion-Aktionen auf Flanken und schreibt den
Zustand nach `paths.NUMATO_STATE`.

─── WAS GEMELDET WURDE (Nutzer, 2026-09-15) ────────────────────────────────

„tally pi muss auch auf windows und mac laufen und dort als gpio interface
einen numato gpio usb benutzen koennen."

─── WARUM DAS VORHER NICHT GING ────────────────────────────────────────────

Die Geraetesuche war LINUX-ONLY, und zwar an zwei Stellen zugleich:

    #: Der udev-Symlink dieses Repos (`99-numato.rules`). Nur auf Linux, und
#: dort die verlaessliche Zuordnung — deshalb wird er zuerst versucht.
PREFERRED_DEVICES = ["/dev/numato0", "/dev/numato1"]
#: Numato Lab. Sortiert die Reihenfolge, schliesst nichts aus — siehe Kopf.
NUMATO_VID = 0x2A19   # udev-Symlink
    candidates = sorted(glob.glob("/dev/ttyACM*"))          # CDC-ACM

`/dev/numato0` legt eine udev-Regel an (`99-numato.rules` in diesem Repo) —
udev gibt es nur unter Linux. `/dev/ttyACM*` ist der Linux-Name fuer ein
CDC-ACM-Geraet; dieselbe Hardware heisst

    Windows   COM3, COM7, …
    macOS     /dev/cu.usbmodem14201, …

Auf beiden fand `find_device()` also NICHTS, und zwar lautlos: die Funktion
gab `None` zurueck, die Schleife wartete auf ein Geraet, und der Zustand
sagte „kein Numato gefunden" — was stimmte und den Grund verschwieg. Wer
das Modul an einen Mac steckte, bekam dieselbe Meldung wie jemand ohne
Modul.

DAS IST DER EINZIGE GPIO-WEG AUSSERHALB DES PI. `gpio_watcher.py` braucht
`gpiod` und `/dev/gpiochip0`; beides gibt es auf einem Schreibtischrechner
nicht. Ein USB-Modul ist dort nicht die zweite Wahl, sondern die einzige.

─── WIE JETZT GESUCHT WIRD ─────────────────────────────────────────────────

Ueber `serial.tools.list_ports` — das ist pyserials eigene Aufzaehlung und
kennt alle drei Systeme. Der udev-Symlink bleibt vorne dran, weil er auf
einem eingerichteten Pi die verlaessliche Zuordnung ist; danach wird jeder
aufgezaehlte Port GEPROBT (`ver\r`), nicht geraten.

Die Hersteller-Kennung (VID 0x2A19, Numato Lab) sortiert nur die
Reihenfolge — sie schliesst nichts aus. Numato baut mehrere Serien, und ein
Modul mit anderer Kennung, das auf `ver` antwortet, ist ein Modul, das auf
`ver` antwortet. Umgekehrt waere eine reine VID-Pruefung genau die Sorte
Filter, die bei neuer Hardware still nichts mehr findet.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Die Pfade liegen in `paths.py` — dieselbe Datei liegt neben diesem Programm,
# auf dem Pi wie im Arbeitsverzeichnis. Der Pfad-Eintrag davor ist noetig, weil
# systemd die Programme mit einem anderen Arbeitsverzeichnis startet als dem
# Verzeichnis, in dem sie liegen.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

# ── pyserial: da oder nicht da, und das wird GESAGT ────────────────────────
#
# Bis 2026-09-12 stand hier ein `sys.exit(1)` BEIM IMPORT, mit dem Rat
# `apt install python3-serial`. Auf dem Pi ist der Rat richtig; auf einem Mac
# und unter Windows gibt es kein apt, und der Abbruch beim Import machte das
# Programm dort auch fuer einen Blick von aussen unerreichbar — selbst ein
# Test konnte es nicht laden, um zu sehen, ob es faellt.
#
# Es wird nichts vorgetaeuscht: ohne pyserial gibt es keine Numato-Platine,
# und das Programm sagt es und laeuft nicht weiter. Es sagt es nur dort, wo
# jemand es liest (beim Start), mit einem Rat, der zur Plattform passt.
#
# `serial.tools.list_ports` KOMMT SEIT 2026-09-15 MIT (Nutzer: „tally pi muss
# auch auf windows und mac laufen und dort als gpio interface einen numato
# gpio usb benutzen koennen"). Es ist pyserials eigene Aufzaehlung und kennt
# alle drei Systeme — ohne sie war die Geraetesuche hier Linux-only.
try:
    import serial
    from serial.tools import list_ports
    SERIAL_VERFUEGBAR = True
    SERIAL_GRUND = ""
except ImportError as _e:  # pragma: no cover — haengt an der Installation
    serial = None
    list_ports = None
    SERIAL_VERFUEGBAR = False
    SERIAL_GRUND = f"pyserial nicht verfuegbar: {_e}"

# Die Pfade kommen aus `paths.py` — eine Stelle statt neunzehn. Ohne
# gesetzte Umgebungsvariablen sind es genau die alten, siehe dort.
BINDINGS = paths.BINDINGS_FILE
STATE_FILE = paths.NUMATO_STATE
COMPANION = "http://localhost:8000"
POLL_INTERVAL = 0.05  # 50 ms
ADC_POLL_EVERY = 4    # every Nth digital poll (so ADC runs ~5 Hz at 50ms loop / 4)
#: Der udev-Symlink dieses Repos (`99-numato.rules`). Nur auf Linux, und
#: dort die verlaessliche Zuordnung — deshalb wird er zuerst versucht.
PREFERRED_DEVICES = ["/dev/numato0", "/dev/numato1"]
#: Numato Lab. Sortiert die Reihenfolge, schliesst nichts aus — siehe Kopf.
NUMATO_VID = 0x2A19
BAUD = 19200


def log(msg):
    print(msg, flush=True)


def write_state(state):
    try:
        paths.atomic_write_json(STATE_FILE, state)
    except Exception as e:
        log(f"state write error: {e}")


def load_bindings():
    if not BINDINGS.exists():
        return []
    try:
        data = json.loads(BINDINGS.read_text())
        return [b for b in data if isinstance(b, dict) and b.get("source") == "numato"]
    except Exception as e:
        log(f"bindings parse error: {e}")
        return []


def bindings_mtime():
    try:
        return BINDINGS.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def kandidaten():
    """Alle Ports, die ein Numato sein KOENNTEN — beste Vermutung zuerst.

    Die Reihenfolge ist die ganze Klugheit dieser Funktion, denn geprobt wird
    ohnehin jeder Eintrag:

      1. der udev-Symlink (Linux, eingerichteter Pi) — eindeutig,
      2. Ports mit der Hersteller-Kennung von Numato Lab,
      3. alles Uebrige, was `list_ports` aufzaehlt.

    Punkt 3 ist kein Fuellmaterial: `list_ports` meldet auf manchen Systemen
    keine VID (etwa hinter einem USB-Hub mit eigenem Treiber), und ein Modul,
    das auf `ver` antwortet, ist ein Modul. Was hier NICHT passiert, ist
    Raten nach Namensmuster — `probe_numato` entscheidet.
    """
    reihe = []
    for p in PREFERRED_DEVICES:
        if os.path.exists(p):
            reihe.append(p)

    passend, uebrig = [], []
    try:
        for port in list_ports.comports():  # noqa: F821 — in `main` geprueft
            if port.device in reihe:
                continue
            (passend if port.vid == NUMATO_VID else uebrig).append(port.device)
    except Exception as e:  # pragma: no cover — systemabhaengig
        log(f"port enumeration failed: {e}")

    return reihe + sorted(passend) + sorted(uebrig)


def find_device():
    """Return path to the first plausible Numato device, or None.

    Der Symlink wird nicht geprobt: er existiert nur, wenn die udev-Regel
    dieses Repos ihn angelegt hat, und die trifft auf die Kennung. Alles
    andere wird geprobt — auch unter Windows, wo `COM7` genauso gut ein
    Bluetooth-Adapter sein kann.
    """
    for p in kandidaten():
        if p in PREFERRED_DEVICES:
            return p
        if probe_numato(p):
            return p
    return None


def probe_numato(path):
    """Open and send `ver\r`; accept anything that responds."""
    try:
        with serial.Serial(path, BAUD, timeout=0.3) as s:
            s.reset_input_buffer()
            s.write(b"ver\r")
            time.sleep(0.15)
            data = s.read(128)
            return len(data) > 0
    except Exception:
        return False


def cmd(s, line, wait=0.05):
    """Send one command, return the response payload (stripped of echo)."""
    s.reset_input_buffer()
    s.write((line + "\r").encode())
    time.sleep(wait)
    raw = s.read(512).decode(errors="replace")
    # strip echo and the trailing '>' prompt
    out = raw.replace(line, "").replace(">", "").strip("\r\n >")
    return out


def read_all_digital(s):
    """Return tuple of 32 ints (0/1) using `gpio readall`. 8-digit hex little-end."""
    out = cmd(s, "gpio readall")
    # Numato returns 8 hex chars (32 bits), MSB = GPIO31, LSB = GPIO0 per docs.
    m = re.search(r"[0-9a-fA-F]{8}", out)
    if not m:
        return None
    val = int(m.group(0), 16)
    return tuple((val >> i) & 1 for i in range(32))


def read_adc(s, ch):
    out = cmd(s, f"adc read {ch}")
    m = re.search(r"\d+", out)
    return int(m.group(0)) if m else None


def http_post(url, data=b""):
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def run_action(binding, event_type):
    trig = binding.get("trigger_edge", "falling")
    if trig != "both" and trig != event_type:
        return
    a = binding.get("action") or {}
    kind = a.get("kind")
    try:
        if kind == "press":
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/press"
            http_post(url)
            log(f"numato CH{binding['channel']} {event_type} -> press")
        elif kind == "down_up":
            sub = "down" if event_type == "falling" else "up"
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/{sub}"
            http_post(url)
            log(f"numato CH{binding['channel']} {event_type} -> {sub}")
        elif kind == "variable":
            name = urllib.parse.quote(a["variable"])
            val = str(a.get("value", "1"))
            url = f"{COMPANION}/api/custom-variable/{name}/value"
            http_post(url, val.encode())
            log(f"numato CH{binding['channel']} {event_type} -> var {a['variable']}={val}")
    except Exception as e:
        log(f"action error on numato CH{binding.get('channel')}: {e}")


def session(device):
    log(f"opening {device}")
    s = serial.Serial(device, BAUD, timeout=0.3)
    try:
        # handshake
        cmd(s, "ver", wait=0.2)
        last_state = read_all_digital(s)
        if last_state is None:
            log(f"{device}: no valid response, not a Numato?")
            return
        adc = [None] * 32
        tick = 0
        last_mtime = bindings_mtime()
        bindings = load_bindings()

        while True:
            cur = read_all_digital(s)
            if cur is None:
                log("readall failed, reconnecting")
                return
            for bcm_ch in range(32):
                if cur[bcm_ch] != last_state[bcm_ch]:
                    etype = "rising" if cur[bcm_ch] == 1 else "falling"
                    for b in bindings:
                        if b.get("channel") == bcm_ch and b.get("enabled", True):
                            run_action(b, etype)
            last_state = cur

            if tick % ADC_POLL_EVERY == 0:
                # poll a subset per loop to avoid saturating serial
                base = (tick // ADC_POLL_EVERY) % 8
                for k in range(4):
                    ch = base * 4 + k
                    if ch < 32:
                        v = read_adc(s, ch)
                        if v is not None:
                            adc[ch] = v

            write_state({
                "connected": True,
                "device": device,
                "digital": list(cur),
                "adc": adc,
                "ts": time.time(),
            })

            if bindings_mtime() != last_mtime:
                last_mtime = bindings_mtime()
                bindings = load_bindings()
                log(f"bindings reloaded ({len(bindings)} active)")

            tick += 1
            time.sleep(POLL_INTERVAL)
    finally:
        try:
            s.close()
        except Exception:
            pass


def rat_zur_plattform() -> str:
    if sys.platform.startswith("linux"):
        return "apt install python3-serial"
    return "pip install pyserial"


def main():
    if not SERIAL_VERFUEGBAR:
        log(SERIAL_GRUND)
        log(f"Ohne pyserial wird keine Numato-Platine gesucht. Nachruesten: "
            f"{rat_zur_plattform()}")
        write_state({"connected": False, "device": None,
                     "error": SERIAL_GRUND, "ts": time.time()})
        return 1
    # HIER STAND EIN RIEGEL FUER ALLES AUSSER LINUX, und er war ehrlich: „die
    # Geraetesuche gibt es bisher nur auf Linux … das ist nachruestbar".
    # Nachgeruestet am 2026-09-15 — `kandidaten()` zaehlt jetzt ueber
    # `serial.tools.list_ports` auf, und das kennt `COM3` wie
    # `/dev/cu.usbmodem*` wie `/dev/ttyACM*`. Der Riegel ist damit kein
    # Schutz mehr, sondern haette genau das verhindert, wofuer er
    # angekuendigt war.
        return 1
    log("numato-watcher starting (hot-plug enabled)")
    while True:
        try:
            dev = find_device()
            if not dev:
                write_state({"connected": False, "device": None, "ts": time.time()})
                time.sleep(2)
                continue
            if not probe_numato(dev):
                write_state({"connected": False, "device": dev, "error": "probe failed"})
                time.sleep(2)
                continue
            session(dev)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            log(f"session error: {e}")
            write_state({"connected": False, "device": None, "error": str(e)})
            time.sleep(2)


if __name__ == "__main__":
    # Der Rueckgabewert von `main` ist der Exit-Code: fehlt pyserial, endet
    # das Programm mit 1 und `run-local.py` meldet es. Ein `main()` ohne
    # `sys.exit` haette den Abbruch verschluckt und 0 zurueckgegeben.
    sys.exit(main() or 0)
