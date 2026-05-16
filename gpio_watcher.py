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
EVENT_LOG_FILE = Path("/opt/pi-guide/events.log")
ATEM_CMD_SOCKET = Path("/run/pi-guide/atem-cmd.sock")
CHIP = "/dev/gpiochip0"
COMPANION = "http://localhost:8000"
CONSUMER = "pi-gpio-watcher"


def log(msg):
    print(msg, flush=True)


def log_event(kind, **fields):
    """Append a JSON line to the shared event log (same file as guide_server.py).
    POSIX guarantees atomic append for small lines under PIPE_BUF on files
    opened with O_APPEND, so a cross-process lock isn't needed."""
    rec = {"ts": time.time(), "kind": kind, "src": "gpio-watcher"}
    rec.update(fields)
    try:
        EVENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    if action_type in ("atem_aux", "atem_pgm", "atem_pvw"):
        src = d.get("in_atem_source")
        if not isinstance(src, int):
            src = d.get("input")  # fall back to device's own ATEM input
        rel = d.get("in_atem_source_release")
        rel = rel if isinstance(rel, int) else None
        action = {"kind": action_type,
                  "source": src,
                  "source_release": rel,
                  "me": int(d.get("me") or 1)}
        if action_type == "atem_aux":
            action["aux"] = d.get("in_atem_aux")
    elif action_type == "companion":
        mode = d.get("in_companion_mode", "tap")
        kind = "down_up" if mode == "hold" else "press"
        action = {"kind": kind,
                  "page":   d.get("in_companion_page", 1),
                  "row":    d.get("in_companion_row", 0),
                  "column": d.get("in_companion_col", 0)}
    else:
        return None
    # Modes that need both edges (so we can fire press AND release):
    #   - companion down_up
    #   - atem_aux/pgm/pvw with a release-source defined
    #   - any binding with hold_release_ms > 0 (burst tracker needs every edge)
    edge = d.get("in_edge", "falling")
    kind = action.get("kind")
    is_atem = kind in ("atem_aux", "atem_pgm", "atem_pvw")
    hold_ms = int(d.get("in_hold_release_ms") or 0)
    needs_both = (kind == "down_up"
                  or (is_atem and action.get("source_release") is not None)
                  or hold_ms > 0)
    if needs_both:
        edge = "both"
    return {
        "bcm": bcm,
        "trigger_edge": edge,
        "enabled": True,
        "bias": d.get("in_bias", "pull-up"),
        "debounce_ms": int(d.get("in_debounce_ms", 20)),
        "hold_release_ms": hold_ms,
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


def run_action(binding, event_type, _edge_count=None):
    trig = binding.get("trigger_edge", "falling")
    bcm = binding.get("bcm")
    label = binding.get("_label") or f"GPIO{bcm}"

    # Local helper: every input-event log gets the same baseline fields,
    # plus edge_count when the burst tracker reports it.
    def _ilog(**extra):
        rec = {"bcm": bcm, "edge": event_type, "label": label}
        if _edge_count is not None:
            rec["edge_count"] = _edge_count
        rec.update(extra)
        log_event("input", **rec)

    if trig != "both" and trig != event_type:
        # Edge fired but the binding doesn't act on it — log so the user
        # can see the pin actually triggered.
        _ilog(action="ignored", reason=f"trigger={trig}")
        return
    a = binding.get("action") or {}
    kind = a.get("kind")
    try:
        if kind == "press":
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/press"
            http_post(url)
            log(f"{label} {event_type} -> press {a['page']}/{a['row']}/{a['column']}")
            _ilog(action="companion_press",
                  page=a['page'], row=a['row'], column=a['column'], ok=True)
        elif kind == "down_up":
            sub = "down" if event_type == "falling" else "up"
            url = f"{COMPANION}/api/location/{a['page']}/{a['row']}/{a['column']}/{sub}"
            http_post(url)
            log(f"{label} {event_type} -> {sub} {a['page']}/{a['row']}/{a['column']}")
            _ilog(action="companion_" + sub,
                  page=a['page'], row=a['row'], column=a['column'], ok=True)
        elif kind == "variable":
            name = urllib.parse.quote(a["variable"])
            val = str(a.get("value", "1"))
            url = f"{COMPANION}/api/custom-variable/{name}/value"
            http_post(url, val.encode())
            log(f"{label} {event_type} -> variable {a['variable']}={val}")
            _ilog(action="companion_variable",
                  variable=a['variable'], value=val, ok=True)
        elif kind in ("atem_aux", "atem_pgm", "atem_pvw"):
            src_press   = a.get("source")
            src_release = a.get("source_release")
            configured = binding.get("trigger_edge", "falling")
            if configured == "rising":
                is_press = (event_type == "rising")
            else:
                is_press = (event_type == "falling")
            target = src_press if is_press else src_release
            phase = "press" if is_press else "release"
            if target is None:
                if not is_press:
                    return
                target = src_press
            if not isinstance(target, int):
                log(f"{label}: {kind} missing source ({a})")
                _ilog(action=kind, phase=phase, ok=False, error="missing source")
                return
            if kind == "atem_aux":
                aux = int(a.get("aux") or 0)
                if not aux:
                    _ilog(action=kind, phase=phase, ok=False,
                          error="missing aux number")
                    return
                atem_cmd({"cmd": "set_aux", "aux": aux, "source": target})
                log(f"{label} {event_type}({phase}) -> ATEM Aux{aux} <- src {target}")
                _ilog(action=kind, phase=phase, aux=aux, source=target, ok=True)
            else:
                me = int(a.get("me") or 1)
                sub = "set_program" if kind == "atem_pgm" else "set_preview"
                atem_cmd({"cmd": sub, "me": me, "source": target})
                bus = "PGM" if kind == "atem_pgm" else "PVW"
                log(f"{label} {event_type}({phase}) -> ATEM {bus} ME{me} <- src {target}")
                _ilog(action=kind, phase=phase, me=me, source=target, ok=True)
        else:
            log(f"{label}: unknown action kind {kind!r}")
            _ilog(action="unknown", kind=str(kind), ok=False)
    except Exception as e:
        log(f"action error on {label}: {e}")
        _ilog(action=str(kind), ok=False, error=str(e))


def bias_of(s):
    return {"pull-up": Bias.PULL_UP, "pull-down": Bias.PULL_DOWN, "none": Bias.DISABLED}.get(
        s or "pull-up", Bias.PULL_UP
    )


import threading as _threading


class BurstTracker:
    """Collapse a burst of edges (from a poorly debouncing button or noisy
    line) into one logical press at the start and one logical release at
    the end.

    The first edge after a quiet period fires `run_press`. Each subsequent
    edge resets a timer; when the timer expires without a new edge, the
    button is considered released and `run_release` fires.

    `is_at_idle()` (optional) is sampled when the release timer expires.
    If it returns False (the line is currently in the pressed/active
    state — e.g. the user's finger is still on the button but contact
    has briefly silenced edges), the release is POSTPONED — we
    re-arm the timer and wait for either a new edge or for the line to
    return to idle. This makes the tracker robust against intermittent
    contacts that go quiet for hundreds of ms while still being held.

    Edge count per burst is reported to the release callback so the user
    can see how noisy the contact is.
    """

    __slots__ = ("label", "release_s", "_run_press", "_run_release",
                 "_is_at_idle", "_lock", "_pressed", "_timer", "_edge_count")

    def __init__(self, label, release_ms, run_press, run_release,
                 is_at_idle=None):
        self.label = label
        self.release_s = max(release_ms, 1) / 1000.0
        # run_press()       — called on first edge
        # run_release(count) — called when the burst settles, with the
        #                      number of edges seen in this burst
        self._run_press = run_press
        self._run_release = run_release
        self._is_at_idle = is_at_idle
        self._lock = _threading.Lock()
        self._pressed = False
        self._timer = None
        self._edge_count = 0

    def on_edge(self, _event_type):
        fire_press = False
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._pressed:
                self._pressed = True
                self._edge_count = 0
                fire_press = True
            self._edge_count += 1
            t = _threading.Timer(self.release_s, self._on_quiet)
            t.daemon = True
            self._timer = t
        if fire_press:
            try:
                self._run_press()
            except Exception as e:
                log(f"burst press error on {self.label}: {e}")
        t.start()

    def _on_quiet(self):
        # If the line is still in the "pressed" state right now, the user
        # is probably still holding the button (intermittent contact,
        # silent gap in the burst). Re-arm and wait.
        try:
            still_pressed = (self._is_at_idle is not None
                             and not self._is_at_idle())
        except Exception:
            still_pressed = False
        with self._lock:
            if still_pressed and self._pressed:
                t = _threading.Timer(self.release_s, self._on_quiet)
                t.daemon = True
                self._timer = t
                t.start()
                return
            count = self._edge_count
            self._pressed = False
            self._timer = None
            self._edge_count = 0
        try:
            self._run_release(count)
        except Exception as e:
            log(f"burst release error on {self.label}: {e}")

    def cancel(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pressed = False
            self._edge_count = 0


def _edges_for_burst(binding):
    """Return (press_edge, release_edge) — synthesised edge labels that
    feed run_action's is_press logic correctly for this binding."""
    trig = binding.get("trigger_edge", "falling")
    if trig == "rising":
        return "rising", "falling"
    return "falling", "rising"


def _build_trackers(by_offset, req=None):
    """Build a {bcm: BurstTracker} dict for bindings that opted into burst
    tracking (hold_release_ms > 0). `req` is the libgpiod line-request
    that owns these pins — passed in so each tracker can sample the
    current pad level when deciding whether the burst is really over.
    Other bindings keep the old direct edge → run_action wiring."""
    trackers = {}
    for bcm, b in by_offset.items():
        hold_ms = int(b.get("hold_release_ms", 0) or 0)
        if hold_ms <= 0:
            continue
        press_e, release_e = _edges_for_burst(b)
        # Default args so each closure has its own binding/edge.
        def make_press(binding=b, e=press_e):
            return lambda: run_action(binding, e)
        def make_release(binding=b, e=release_e):
            def cb(count):
                # Annotate the action call with the burst edge count so
                # the live log shows how dirty the gesture was.
                run_action(binding, e, _edge_count=count)
            return cb
        # is_at_idle: pull-up wiring assumes idle = HIGH (Value.ACTIVE),
        # active = LOW (Value.INACTIVE). pull-down inverts.
        bias = b.get("bias", "pull-up")
        def make_idle_check(b_bcm=bcm, bias_=bias):
            if req is None:
                return None
            try:
                from gpiod.line import Value as _V
            except Exception:
                return None
            idle_val = _V.ACTIVE if bias_ != "pull-down" else _V.INACTIVE
            def check():
                try:
                    return req.get_value(b_bcm) == idle_val
                except Exception:
                    return True  # fail-open → release fires
            return check
        trackers[bcm] = BurstTracker(
            label=b.get("_label") or f"GPIO{bcm}",
            release_ms=hold_ms,
            run_press=make_press(),
            run_release=make_release(),
            is_at_idle=make_idle_check(),
        )
    return trackers


def _dispatch(b, etype, trackers):
    """Route an edge event either through the burst tracker (if configured)
    or directly to run_action."""
    t = trackers.get(int(b["bcm"]))
    if t is not None:
        t.on_edge(etype)
    else:
        run_action(b, etype)


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
        # In the per-pin fallback, each pin has its own request object —
        # wrap them so the tracker can sample via a unified req.get_value(bcm).
        class _MultiReq:
            def get_value(self, bcm):
                r = active.get(int(bcm))
                if r is None:
                    raise RuntimeError(f"no request for bcm {bcm}")
                return r.get_value(bcm)
        trackers = _build_trackers(by_offset, req=_MultiReq())
        if trackers:
            log(f"burst trackers active for GPIOs: {sorted(trackers)}")
        try:
            while True:
                for bcm, r in list(active.items()):
                    if r.wait_edge_events(timeout=timedelta(milliseconds=50)):
                        for ev in r.read_edge_events():
                            etype = "rising" if ev.event_type == ev.Type.RISING_EDGE else "falling"
                            _dispatch(by_offset[bcm], etype, trackers)
                if file_mtime() != last_mtime:
                    return
        finally:
            for t in trackers.values():
                t.cancel()
            for r in active.values():
                try:
                    r.release()
                except Exception:
                    pass
        return

    trackers = _build_trackers(by_offset, req=req)
    if trackers:
        log(f"burst trackers active for GPIOs: {sorted(trackers)}")

    try:
        while True:
            if req.wait_edge_events(timeout=timedelta(milliseconds=500)):
                for ev in req.read_edge_events():
                    bcm = ev.line_offset
                    etype = "rising" if ev.event_type == ev.Type.RISING_EDGE else "falling"
                    b = by_offset.get(bcm)
                    if b:
                        _dispatch(b, etype, trackers)
            if file_mtime() != last_mtime:
                return
    finally:
        for t in trackers.values():
            t.cancel()
        try:
            req.release()
        except Exception:
            pass


def main():
    log(f"pi-gpio-watcher starting (chip={CHIP})")
    log_event("watcher", action="start", chip=CHIP)
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
