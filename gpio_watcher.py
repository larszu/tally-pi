#!/usr/bin/env python3
"""Watches GPIO lines per bindings.json + tally.json and fires actions.

Two input sources, merged at load time:
  - bindings.json (legacy / external editors): full schema, action.kind in
    {press, down_up, variable}.
  - tally.json devices with `in_gpio` set: converted into binding records
    here so we don't duplicate watcher logic. Supports action.kind=atem_aux
    which sends a CAuS command via the atem_watcher's unix socket.
"""
import json
import socket as _socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

import gpiod
from gpiod.line import Bias, Direction, Edge

BINDINGS = Path("/opt/pi-guide/bindings.json")
TALLY_CONFIG = Path("/opt/pi-guide/tally.json")
ATEM_CMD_SOCKET = Path("/run/pi-guide/atem-cmd.sock")
CHIP = "/dev/gpiochip0"
COMPANION = "http://localhost:8000"
CONSUMER = "pi-gpio-watcher"


def log(msg):
    print(msg, flush=True)


def _load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log(f"{path.name} parse error: {e}")
        return None


def _device_to_binding(d):
    """Translate a tally-device with `in_gpio` into a binding-record."""
    bcm = d.get("in_gpio")
    if not isinstance(bcm, int):
        return None
    action_type = d.get("in_action_type", "none")
    if action_type == "none":
        return None
    if action_type == "atem_aux":
        src = d.get("in_atem_source")
        if not isinstance(src, int):
            src = d.get("input")  # fall back to device's own ATEM input
        action = {"kind": "atem_aux",
                  "aux": d.get("in_atem_aux"),
                  "source": src}
    elif action_type == "companion":
        action = {"kind": "press",
                  "page":   d.get("in_companion_page", 1),
                  "row":    d.get("in_companion_row", 0),
                  "column": d.get("in_companion_col", 0)}
    else:
        return None
    return {
        "bcm": bcm,
        "trigger_edge": d.get("in_edge", "falling"),
        "enabled": True,
        "bias": d.get("in_bias", "pull-up"),
        "debounce_ms": int(d.get("in_debounce_ms", 20)),
        "action": action,
        "_label": d.get("name") or d.get("id") or f"in_gpio_{bcm}",
    }


def load_bindings():
    """Return merged binding list. Tally-device entries win over bindings.json
    if a pin is configured in both."""
    seen_bcms = set()
    merged = []

    tally = _load_json(TALLY_CONFIG) or {}
    for dev in (tally.get("devices") or []):
        if not isinstance(dev, dict):
            continue
        b = _device_to_binding(dev)
        if b is None:
            continue
        merged.append(b)
        seen_bcms.add(b["bcm"])

    legacy = _load_json(BINDINGS) or []
    for b in legacy if isinstance(legacy, list) else []:
        if not isinstance(b, dict):
            continue
        try:
            bcm = int(b.get("bcm"))
        except Exception:
            continue
        if bcm in seen_bcms:
            log(f"GPIO{bcm}: bindings.json entry shadowed by tally.json")
            continue
        merged.append(b)
        seen_bcms.add(bcm)
    return merged


def file_mtime():
    """Combined mtime of both config files (so we reload on any change)."""
    total = 0.0
    for p in (BINDINGS, TALLY_CONFIG):
        try:
            total += p.stat().st_mtime
        except FileNotFoundError:
            pass
    return total


def http_post(url, data=b""):
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def atem_cmd(cmd: dict) -> None:
    """Send one JSON command line to the atem_watcher's unix socket."""
    if not ATEM_CMD_SOCKET.exists():
        raise RuntimeError(f"{ATEM_CMD_SOCKET} not present (atem watcher down?)")
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(str(ATEM_CMD_SOCKET))
        s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        resp = s.recv(4096).decode("utf-8", errors="replace").strip()
        if resp:
            try:
                j = json.loads(resp.split("\n", 1)[0])
                if not j.get("ok"):
                    raise RuntimeError(j.get("error", "atem cmd rejected"))
            except json.JSONDecodeError:
                pass
    finally:
        try: s.close()
        except Exception: pass


def run_action(binding, event_type):
    trig = binding.get("trigger_edge", "falling")
    if trig != "both" and trig != event_type:
        return
    a = binding.get("action") or {}
    kind = a.get("kind")
    label = binding.get("_label") or f"GPIO{binding['bcm']}"
    try:
        if kind == "press":
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/press"
            http_post(url)
            log(f"{label} {event_type} -> press {a['page']}/{a['row']}/{a['column']}")
        elif kind == "down_up":
            sub = "down" if event_type == "falling" else "up"
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/{sub}"
            http_post(url)
            log(f"{label} {event_type} -> {sub} {a['page']}/{a['row']}/{a['column']}")
        elif kind == "variable":
            name = urllib.parse.quote(a["variable"])
            val = str(a.get("value", "1"))
            url = f"{COMPANION}/api/custom-variable/{name}/value"
            http_post(url, val.encode())
            log(f"{label} {event_type} -> variable {a['variable']}={val}")
        elif kind == "atem_aux":
            aux = int(a.get("aux") or 0)
            src = a.get("source")
            if not aux or not isinstance(src, int):
                log(f"{label}: atem_aux missing aux/source ({a})")
                return
            atem_cmd({"cmd": "set_aux", "aux": aux, "source": src})
            log(f"{label} {event_type} -> ATEM Aux{aux} <- src {src}")
        else:
            log(f"{label}: unknown action kind {kind!r}")
    except Exception as e:
        log(f"action error on {label}: {e}")


def bias_of(s):
    return {"pull-up": Bias.PULL_UP, "pull-down": Bias.PULL_DOWN, "none": Bias.DISABLED}.get(
        s or "pull-up", Bias.PULL_UP
    )


def watch_loop():
    last_mtime = file_mtime()
    bindings = load_bindings()
    enabled = [b for b in bindings if b.get("enabled", True)]
    if not enabled:
        log("no enabled bindings, sleeping")
        while True:
            time.sleep(2)
            if file_mtime() != last_mtime:
                return

    config = {}
    by_offset = {}
    for b in enabled:
        try:
            bcm = int(b["bcm"])
        except Exception:
            continue
        config[bcm] = gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.BOTH,
            bias=bias_of(b.get("bias")),
            debounce_period=timedelta(milliseconds=int(b.get("debounce_ms", 20))),
        )
        by_offset[bcm] = b

    if not config:
        time.sleep(2)
        return

    log(f"claiming GPIOs: {sorted(config)}")
    try:
        req = gpiod.request_lines(CHIP, consumer=CONSUMER, config=config)
    except OSError as e:
        log(f"cannot claim lines ({e}); retrying per-pin skipping conflicts")
        # Fall back: try each pin individually
        active = {}
        for bcm, settings in config.items():
            try:
                active[bcm] = gpiod.request_lines(
                    CHIP, consumer=CONSUMER, config={bcm: settings}
                )
            except OSError as e2:
                log(f"  skipping GPIO{bcm}: {e2}")
        if not active:
            log("no GPIOs claimable, sleeping")
            time.sleep(5)
            return
        try:
            while True:
                for bcm, r in list(active.items()):
                    if r.wait_edge_events(timeout=timedelta(milliseconds=50)):
                        for ev in r.read_edge_events():
                            etype = "rising" if ev.event_type == ev.Type.RISING_EDGE else "falling"
                            run_action(by_offset[bcm], etype)
                if file_mtime() != last_mtime:
                    return
        finally:
            for r in active.values():
                try:
                    r.release()
                except Exception:
                    pass
        return

    try:
        while True:
            if req.wait_edge_events(timeout=timedelta(milliseconds=500)):
                for ev in req.read_edge_events():
                    bcm = ev.line_offset
                    etype = "rising" if ev.event_type == ev.Type.RISING_EDGE else "falling"
                    b = by_offset.get(bcm)
                    if b:
                        run_action(b, etype)
            if file_mtime() != last_mtime:
                return
    finally:
        try:
            req.release()
        except Exception:
            pass


def main():
    log(f"pi-gpio-watcher starting (chip={CHIP})")
    while True:
        try:
            watch_loop()
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            log(f"loop error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
