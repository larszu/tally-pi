#!/usr/bin/env python3
"""Numato 32-CH USB GPIO watcher with hot-plug support.

Detects /dev/numato0 (udev symlink) or auto-scans /dev/ttyACM* for a Numato
device (responds to `ver\r`). Polls all 32 digital channels + ADCs, fires
Companion actions on edges, and writes live state to /run/pi-guide/numato.json.
"""
import glob
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
try:
    import serial
    SERIAL_VERFUEGBAR = True
    SERIAL_GRUND = ""
except ImportError as _e:
    serial = None
    SERIAL_VERFUEGBAR = False
    SERIAL_GRUND = f"pyserial nicht verfuegbar: {_e}"

# Die Pfade kommen aus `paths.py` — eine Stelle statt neunzehn. Ohne
# gesetzte Umgebungsvariablen sind es genau die alten, siehe dort.
BINDINGS = paths.BINDINGS_FILE
STATE_FILE = paths.NUMATO_STATE
COMPANION = "http://localhost:8000"
POLL_INTERVAL = 0.05  # 50 ms
ADC_POLL_EVERY = 4    # every Nth digital poll (so ADC runs ~5 Hz at 50ms loop / 4)
PREFERRED_DEVICES = ["/dev/numato0", "/dev/numato1"]
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


def find_device():
    """Return path to the first plausible Numato device, or None."""
    for p in PREFERRED_DEVICES:
        if os.path.exists(p):
            return p
    candidates = sorted(glob.glob("/dev/ttyACM*"))
    for c in candidates:
        if probe_numato(c):
            return c
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
    if not sys.platform.startswith("linux"):
        # Die Geraetesuche kennt `/dev/numato0` und `/dev/ttyACM*`. Unter
        # Windows heissen serielle Anschluesse `COM3`, auf dem Mac
        # `/dev/cu.usbmodem*`. Das ist nachruestbar, aber nicht ohne die
        # Platine zu pruefen — und etwas zu suchen, wo man nicht suchen kann,
        # waere eine leere Anzeige ohne Grund. Also steht der Grund da.
        log(f"numato-watcher: die Geraetesuche gibt es bisher nur auf Linux "
            f"({sys.platform} nennt serielle Anschluesse anders). Die "
            f"Numato-Platine haengt am Pi; dort laeuft dieser Dienst.")
        write_state({"connected": False, "device": None,
                     "error": f"Geraetesuche auf {sys.platform} nicht "
                              f"unterstuetzt", "ts": time.time()})
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
    sys.exit(main() or 0)
