#!/usr/bin/env python3
"""Serves setup-guide.html, live GPIO status, and bindings API on port 8080."""
import http.server
import json
import os
import re
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8080
DEBUG_GPIO = Path("/sys/kernel/debug/gpio")
BINDINGS_FILE = Path("/opt/pi-guide/bindings.json")
NUMATO_STATE_FILE = Path("/run/pi-guide/numato.json")
ATEM_STATE_FILE = Path("/run/pi-guide/atem.json")
TALLY_CONFIG_FILE = Path("/opt/pi-guide/tally.json")
EVENT_LOG_FILE = Path("/opt/pi-guide/events.log")
EVENT_LOG_MAX = 1_000_000  # bytes before rotation
EVENT_LOG_LOCK = threading.Lock()

# Physical header pin for each BCM GPIO (2..27)
BCM_TO_PIN = {
    2: 3, 3: 5, 4: 7, 5: 29, 6: 31, 7: 26, 8: 24, 9: 21,
    10: 19, 11: 23, 12: 32, 13: 33, 14: 8, 15: 10, 16: 36,
    17: 11, 18: 12, 19: 35, 20: 38, 21: 40, 22: 15, 23: 16,
    24: 18, 25: 22, 26: 37, 27: 13,
}

LINE_RE = re.compile(
    r"gpio-(\d+)\s+\(\s*GPIO(\d+)\s*(?:\|([^)]*?))?\s*\)"
    r"(?:\s+(in|out))?"
    r"(?:\s+(hi|lo))?"
)

# `pinctrl get` output, e.g. "17: ip    pd | lo // GPIO17 = input"
PINCTRL_RE = re.compile(
    r"^\s*(\d+):\s+(\S+)\s+(\S+)\s*\|\s*(\S+)"
)


def read_pinctrl_bias():
    """Return dict[bcm] -> 'pu' | 'pd' | 'none' by parsing `pinctrl get`."""
    try:
        out = subprocess.run(
            ["pinctrl", "get"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}
    result = {}
    for line in out.splitlines():
        m = PINCTRL_RE.match(line)
        if not m:
            continue
        bcm = int(m.group(1))
        pull = m.group(3)
        if pull == "pu":
            result[bcm] = "pu"
        elif pull == "pd":
            result[bcm] = "pd"
        else:
            result[bcm] = "none"
    return result


def parse_gpio_state():
    result = {}
    try:
        text = DEBUG_GPIO.read_text(errors="replace")
    except Exception as e:
        return {"error": str(e)}
    bias_map = read_pinctrl_bias()
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        global_line = int(m.group(1))
        bcm = int(m.group(2))
        if bcm not in BCM_TO_PIN:
            continue
        consumer = (m.group(3) or "").strip()
        direction = m.group(4) or None
        value_s = m.group(5)
        value = 1 if value_s == "hi" else (0 if value_s == "lo" else None)
        result[str(bcm)] = {
            "bcm": bcm,
            "pin": BCM_TO_PIN[bcm],
            "consumer": consumer,
            "claimed": bool(consumer),
            "direction": direction,
            "value": value,
            "bias": bias_map.get(bcm),
            "global_line": global_line,
            "sysfs": consumer == "sysfs",
        }
    return result


def load_bindings():
    if not BINDINGS_FILE.exists():
        return []
    try:
        data = json.loads(BINDINGS_FILE.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_bindings(bindings):
    BINDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # atomic write so watcher sees complete file
    fd, tmp = tempfile.mkstemp(dir=str(BINDINGS_FILE.parent), prefix=".bindings-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(bindings, f, indent=2)
        os.replace(tmp, BINDINGS_FILE)
        try:
            os.chmod(BINDINGS_FILE, 0o664)
        except Exception:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def validate_binding(b):
    """Raise ValueError if binding is malformed."""
    if not isinstance(b, dict):
        raise ValueError("binding must be object")
    source = b.get("source", "pi")
    if source not in ("pi", "numato"):
        raise ValueError("source must be pi|numato")
    if source == "pi":
        bcm = b.get("bcm")
        if not isinstance(bcm, int) or bcm not in BCM_TO_PIN:
            raise ValueError(f"bcm must be one of {sorted(BCM_TO_PIN)}")
    else:
        ch = b.get("channel")
        if not isinstance(ch, int) or ch < 0 or ch > 31:
            raise ValueError("channel must be 0..31")
    if b.get("trigger_edge") not in (None, "rising", "falling", "both"):
        raise ValueError("trigger_edge must be rising|falling|both")
    if b.get("bias") not in (None, "pull-up", "pull-down", "none"):
        raise ValueError("bias must be pull-up|pull-down|none")
    deb = b.get("debounce_ms", 20)
    if not isinstance(deb, int) or deb < 0 or deb > 5000:
        raise ValueError("debounce_ms must be 0..5000")
    action = b.get("action") or {}
    kind = action.get("kind")
    if kind not in ("press", "down_up", "variable"):
        raise ValueError("action.kind must be press|down_up|variable")
    if kind in ("press", "down_up"):
        for k in ("page", "row", "column"):
            v = action.get(k)
            if not isinstance(v, int) or v < 0 or v > 999:
                raise ValueError(f"action.{k} must be 0..999")
    if kind == "variable":
        name = action.get("variable", "")
        if not re.match(r"^[A-Za-z0-9_]{1,64}$", name):
            raise ValueError("action.variable must match [A-Za-z0-9_]{1,64}")


def get_ipconfig():
    """Return list of interfaces with their IPv4 addresses."""
    ifaces = []
    try:
        out = subprocess.run(
            ["ip", "-4", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=3,
        )
        data = json.loads(out.stdout)
        for i in data:
            name = i.get("ifname")
            if name == "lo":
                continue
            addrs = [a.get("local") for a in i.get("addr_info", []) if a.get("local")]
            state = i.get("operstate", "UNKNOWN")
            ifaces.append({"name": name, "addresses": addrs, "state": state})
    except Exception as e:
        ifaces.append({"error": str(e)})
    # Hostname info
    hostname = socket.gethostname()
    return {"hostname": hostname, "interfaces": ifaces}


def get_numato_state():
    """Return Numato watcher state (written by numato_watcher.py)."""
    if not NUMATO_STATE_FILE.exists():
        return {"connected": False, "device": None}
    try:
        return json.loads(NUMATO_STATE_FILE.read_text())
    except Exception:
        return {"connected": False, "device": None, "error": "state parse error"}


# ---------------------------------------------------------------------------
# Tally support (ATEM only).
# ---------------------------------------------------------------------------
GPIO_TRIGGER_MODES = ("pgm", "pgm_pvw", "manual")
GPIO_INPUT_EDGES = ("falling", "rising", "both")
GPIO_INPUT_ACTIONS = ("none", "companion", "atem_aux")

# BCM pins safe to claim (avoid I2C/UART/SPI/EEPROM).
USABLE_BCMS = (4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27)

DEFAULT_TALLY_CONFIG = {
    "atem_ip": "",
    # Each device groups the configuration for one ATEM source:
    #   id, name, input, me?, aux?           (browser-tally)
    #   out_gpio?, out_trigger?              (hardware tally-lamp output)
    #   in_gpio?, in_edge?, in_action_type?  (trigger-button input)
    #     in_atem_aux?, in_atem_source?      (when in_action_type=atem_aux)
    #     in_companion_page?, in_companion_row?, in_companion_col?
    #                                        (when in_action_type=companion)
    "devices": [],
}


def _migrate_device(d):
    """Rename legacy gpio/gpio_trigger fields to out_gpio/out_trigger."""
    if not isinstance(d, dict):
        return d
    if "out_gpio" not in d and "gpio" in d:
        d["out_gpio"] = d.pop("gpio")
    if "out_trigger" not in d and "gpio_trigger" in d:
        d["out_trigger"] = d.pop("gpio_trigger")
    return d


def load_tally_config():
    if not TALLY_CONFIG_FILE.exists():
        return dict(DEFAULT_TALLY_CONFIG)
    try:
        d = json.loads(TALLY_CONFIG_FILE.read_text())
        if not isinstance(d, dict):
            return dict(DEFAULT_TALLY_CONFIG)
        out = dict(DEFAULT_TALLY_CONFIG)
        out.update({k: v for k, v in d.items() if k in DEFAULT_TALLY_CONFIG})
        if not isinstance(out["devices"], list):
            out["devices"] = []
        out["devices"] = [_migrate_device(dev) for dev in out["devices"]
                          if isinstance(dev, dict)]
        return out
    except Exception:
        return dict(DEFAULT_TALLY_CONFIG)


def save_tally_config(cfg):
    TALLY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(TALLY_CONFIG_FILE.parent), prefix=".tally.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, TALLY_CONFIG_FILE)
        try:
            os.chmod(TALLY_CONFIG_FILE, 0o664)
        except Exception:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def validate_tally_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("config must be object")
    atem_ip = cfg.get("atem_ip", "") or ""
    if atem_ip and not re.match(r"^[0-9a-zA-Z\.\-]{1,64}$", atem_ip):
        raise ValueError("atem_ip contains invalid characters")
    devs = cfg.get("devices") or []
    if not isinstance(devs, list):
        raise ValueError("devices must be list")
    ids = set()
    seen_gpio = set()
    for d in devs:
        if not isinstance(d, dict):
            raise ValueError("device entry must be object")
        did = d.get("id", "")
        if not isinstance(did, str) or not re.match(r"^[A-Za-z0-9_\-]{1,32}$", did):
            raise ValueError("device id must match [A-Za-z0-9_-]{1,32}")
        if did in ids:
            raise ValueError(f"duplicate device id: {did}")
        ids.add(did)
        name = d.get("name", "")
        if not isinstance(name, str) or len(name) > 64:
            raise ValueError("device name must be string, max 64 chars")
        inp = d.get("input")
        if not isinstance(inp, int) or inp < 0 or inp > 99999:
            raise ValueError("device input must be non-negative int")
        me = d.get("me", 1)
        if not isinstance(me, int) or me < 1 or me > 4:
            raise ValueError("device me must be int 1..4")
        aux = d.get("aux", [])
        if not isinstance(aux, list):
            raise ValueError("device aux must be list")
        for a in aux:
            if not isinstance(a, int) or a < 1 or a > 32:
                raise ValueError("device aux entries must be int 1..32")

        def _check_pin(value, label):
            if value is None:
                return None
            if not isinstance(value, int) or value not in USABLE_BCMS:
                raise ValueError(f"{label} must be one of {USABLE_BCMS}")
            if value in seen_gpio:
                raise ValueError(f"GPIO {value} already used by another device "
                                 f"({label})")
            seen_gpio.add(value)
            return value

        # Hardware tally-lamp output (Pi drives pin from ATEM state).
        _check_pin(d.get("out_gpio"), "out_gpio")
        trig = d.get("out_trigger", "pgm")
        if trig not in GPIO_TRIGGER_MODES:
            raise ValueError(f"out_trigger must be one of {GPIO_TRIGGER_MODES}")
        if not isinstance(d.get("out_active_high", False), bool):
            raise ValueError("out_active_high must be true or false")

        # Trigger button input (Pi watches pin, fires action on edge).
        in_pin = _check_pin(d.get("in_gpio"), "in_gpio")
        edge = d.get("in_edge", "falling")
        if edge not in GPIO_INPUT_EDGES:
            raise ValueError(f"in_edge must be one of {GPIO_INPUT_EDGES}")
        act = d.get("in_action_type", "none")
        if act not in GPIO_INPUT_ACTIONS:
            raise ValueError(f"in_action_type must be one of {GPIO_INPUT_ACTIONS}")
        if in_pin is not None and act == "atem_aux":
            aux_n = d.get("in_atem_aux")
            if not isinstance(aux_n, int) or aux_n < 1 or aux_n > 32:
                raise ValueError("in_atem_aux must be int 1..32")
            src = d.get("in_atem_source")
            if src is not None and (not isinstance(src, int) or src < 0 or src > 99999):
                raise ValueError("in_atem_source must be int 0..99999 or null")
        if in_pin is not None and act == "companion":
            for f in ("in_companion_page", "in_companion_row", "in_companion_col"):
                v = d.get(f, 0)
                if not isinstance(v, int) or v < 0 or v > 99:
                    raise ValueError(f"{f} must be int 0..99")
            mode = d.get("in_companion_mode", "tap")
            if mode not in ("tap", "hold"):
                raise ValueError("in_companion_mode must be 'tap' or 'hold'")
        # Optional per-binding debounce override (libgpiod ms).
        deb = d.get("in_debounce_ms")
        if deb is not None and (not isinstance(deb, int) or deb < 0 or deb > 2000):
            raise ValueError("in_debounce_ms must be int 0..2000 or null")


def get_atem_state():
    if not ATEM_STATE_FILE.exists():
        return {"connected": False, "error": "atem watcher not running"}
    try:
        return json.loads(ATEM_STATE_FILE.read_text())
    except Exception:
        return {"connected": False, "error": "state parse error"}


# ---------------------------------------------------------------------------
# Tally outputs — own GPIO pins in open-drain mode.
#
# Idle = HIGH on an open-drain line = high-impedance, i.e. the pin behaves
# like an open switch contact. Active = LOW = pulled to GND, i.e. as if the
# contact was bridged. Companion (or any other system) toggles state via
# POST /tally-out/<bcm>/on|off|pulse so it never needs to drive the pin
# itself.
# ---------------------------------------------------------------------------
GPIO_CHIP = "/dev/gpiochip0"
TALLY_OUT_CONSUMER = "pi-tally-out"


class TallyOutputs:
    def __init__(self):
        self._lock = threading.Lock()
        self._req = None       # single multi-line gpiod.LineRequest
        self._bcms = []        # active line offsets, in claim order
        self._values = {}      # bcm -> bool (True = active/LOW/bridged)
        self._pulse_timers = {}  # bcm -> threading.Timer
        self._latched = set()  # bcms currently held manually (auto-driver paused)
        self._error = None
        self._value_off = None
        self._value_on = None

    def configure(self, bcms):
        """Claim the given BCM pins as tally outputs (idempotent).

        Single multi-line gpiod request, push-pull output. Toggle between:

          OFF  → output_value HIGH (+3.3 V), relay-module LED gets 0 mA → off
          ON   → output_value LOW  (0 V),    relay-module LED gets ~3 mA → on

        Single-request semantics, the standard libgpiod 2.x usage pattern.
        """
        try:
            import gpiod
            from gpiod.line import Bias, Direction, Drive, Value
        except Exception as e:
            with self._lock:
                self._error = f"gpiod not available: {e}"
                self._release_locked()
            return

        bcms = sorted({int(b) for b in bcms if isinstance(b, int) and 0 <= b <= 27})
        with self._lock:
            if bcms == self._bcms and self._req is not None:
                return  # no change
            for t in self._pulse_timers.values():
                try: t.cancel()
                except Exception: pass
            self._pulse_timers.clear()
            # Drop stale latched-markers when the pin map changes — they refer
            # to pins that may no longer be claimed.
            self._latched.clear()
            self._release_locked()
            if not bcms:
                self._error = None
                return

            base_settings = gpiod.LineSettings(
                direction=Direction.OUTPUT,
                drive=Drive.PUSH_PULL,
                bias=Bias.DISABLED,
                output_value=Value.ACTIVE,  # HIGH = idle = relay off
            )
            self._value_off = Value.ACTIVE
            self._value_on  = Value.INACTIVE

            try:
                # ONE request with all lines — standard libgpiod 2.x pattern.
                config = {b: base_settings for b in bcms}
                self._req = gpiod.request_lines(
                    GPIO_CHIP, consumer=TALLY_OUT_CONSUMER, config=config,
                )
                self._bcms = list(bcms)
                self._values = {b: False for b in bcms}
                self._error = None
                print(f"[tally-out] claimed GPIOs (single request, push-pull, idle=HIGH): {self._bcms}",
                      flush=True)
            except Exception as e:
                self._error = f"claim failed: {e}"
                self._req = None
                self._bcms = []
                self._values = {}
                print(f"[tally-out] {self._error}", flush=True)

    def _release_locked(self):
        if self._req is not None:
            try: self._req.release()
            except Exception: pass
        self._req = None
        self._bcms = []
        self._values = {}

    def _write(self, bcm, on, source="auto"):
        # Update internal state, then push ALL claimed lines one-by-one
        # using per-offset set_value(). set_values(dict) was silently
        # dropping updates for some offsets on this Pi 5 / libgpiod 2.2.0
        # (BCM 20 reproducibly stuck at HIGH after a successful API call).
        # set_values(list) is not supported by the binding ("list indices
        # must be integers or slices, not Value"). Per-line set_value()
        # mostly works, but on some BCM pins (20, 22 observed on this Pi 5)
        # the kernel still reports the old level after a "successful" call.
        # Verify via read-back and fall back to pinctrl(8) if the kernel
        # didn't follow our write.
        prev = self._values.get(bcm)
        self._values[bcm] = bool(on)
        for b in self._bcms:
            v = self._value_on if self._values[b] else self._value_off
            self._req.set_value(b, v)
            # Read back: if the kernel-reported value disagrees with what
            # we just wrote, this line is the buggy-set_value() case.
            actual = None
            try:
                actual = self._req.get_value(b)
            except Exception:
                pass
            if actual is not None and actual != v:
                try:
                    log_event("pin_writeback_mismatch", bcm=b,
                              wanted=str(v), actual=str(actual), source=source)
                except Exception:
                    pass
                # Sledgehammer fallback — bypass libgpiod entirely.
                # `pinctrl op <bcm> dh|dl` works even when set_value silently
                # no-ops, as confirmed by user testing.
                try:
                    self._pinctrl_set(b, v == self._value_off)
                    log_event("pin_pinctrl_fallback", bcm=b,
                              value=("HIGH" if v == self._value_off else "LOW"),
                              source=source)
                except Exception as e:
                    log_event("pin_pinctrl_fallback_failed", bcm=b, error=str(e))
        # Log only real transitions so the event log stays useful.
        if prev is not None and prev != bool(on):
            try:
                log_event("pin", bcm=bcm, value=bool(on), source=source)
            except Exception:
                pass

    @staticmethod
    def _pinctrl_set(bcm, high):
        """Fallback path: use `pinctrl op <bcm> dh|dl` to force the level.
        Required on this Pi 5 (libgpiod 2.2.0 set_value silently no-ops for
        some pins). `pinctrl` is part of raspi-utils and ships with Raspberry
        Pi OS by default."""
        cmd = ["pinctrl", "op", str(int(bcm)), "dh" if high else "dl"]
        subprocess.run(cmd, check=True, timeout=2.0,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def set(self, bcm, on, respect_pulse=False, source=None):
        """on=True drives pin LOW (relay-module IN low → relay engaged).
        on=False drives pin HIGH (idle, relay disengaged).

        respect_pulse=True is used by the auto-driver — it skips pins
        with an active pulse timer or a manual latch.
        source: "auto" | "manual" | "latch" | "release-auto" — purely
        informational, propagated into the event log.
        """
        try:
            import gpiod  # noqa: F401
        except Exception as e:
            return False, f"gpiod not available: {e}"
        eff_source = source or ("auto" if respect_pulse else "manual")
        with self._lock:
            if self._req is None or bcm not in self._values:
                return False, f"GPIO{bcm} not configured as tally output"
            if respect_pulse and bcm in self._pulse_timers:
                return True, "skipped (pulse active)"
            if respect_pulse and bcm in self._latched:
                return True, "skipped (latch active)"
            if self._values.get(bcm) == bool(on) and not respect_pulse:
                t = self._pulse_timers.pop(bcm, None)
                if t:
                    try: t.cancel()
                    except Exception: pass
                return True, "ok"
            try:
                self._write(bcm, on, source=eff_source)
                if not respect_pulse:
                    t = self._pulse_timers.pop(bcm, None)
                    if t:
                        try: t.cancel()
                        except Exception: pass
                return True, "ok"
            except Exception as e:
                # surface the error so the auto-driver loop can be debugged
                print(f"[tally-out] set({bcm}, {on}) failed: {e}", flush=True)
                return False, f"set failed: {e}"

    def pulse(self, bcm, ms):
        ok, msg = self.set(bcm, True)
        if not ok:
            return ok, msg
        ms = max(1, min(60_000, int(ms)))

        def _release():
            with self._lock:
                self._pulse_timers.pop(bcm, None)
            self.set(bcm, False)

        timer = threading.Timer(ms / 1000.0, _release)
        timer.daemon = True
        with self._lock:
            old = self._pulse_timers.get(bcm)
            if old:
                try: old.cancel()
                except Exception: pass
            self._pulse_timers[bcm] = timer
        timer.start()
        return True, f"pulse {ms}ms"

    def latch(self, bcm, on):
        """Drive pin to `on` (True=LOW, False=HIGH) and pause the auto-driver
        for this pin until release() is called. Used by the UI test buttons
        as a hold/toggle."""
        # We need the latched-marker added atomically with the pin write,
        # otherwise the auto-driver (running every 250 ms) can squeeze in
        # between the set() return and the _latched.add() call and overwrite
        # the value we just wrote. Once that race happens, future auto-driver
        # ticks DO skip the pin because by then it's in _latched, so the bad
        # value sticks until the user toggles again.
        try:
            import gpiod  # noqa: F401
        except Exception as e:
            return False, f"gpiod not available: {e}"
        with self._lock:
            if self._req is None or bcm not in self._values:
                return False, f"GPIO{bcm} not configured as tally output"
            try:
                self._write(bcm, on, source="latch")
            except Exception as e:
                print(f"[tally-out] latch({bcm}, {on}) failed: {e}", flush=True)
                return False, f"latch failed: {e}"
            # Cancel any in-flight pulse and mark latched, all under the same lock.
            t = self._pulse_timers.pop(bcm, None)
            if t:
                try: t.cancel()
                except Exception: pass
            self._latched.add(bcm)
        try:
            log_event("latch", bcm=bcm, value=bool(on), action="latch")
        except Exception:
            pass
        return True, "latched"

    def release(self, bcm):
        """Remove the manual latch — the auto-driver will reclaim the pin
        on its next tick."""
        with self._lock:
            was = bcm in self._latched
            self._latched.discard(bcm)
        if was:
            try:
                log_event("latch", bcm=bcm, action="release")
            except Exception:
                pass
        return True, "released"

    def state(self):
        with self._lock:
            return {
                "configured": list(self._bcms),
                "values": {str(b): self._values.get(b, False) for b in self._bcms},
                "latched": sorted(self._latched),
                "error": self._error,
            }


TALLY_OUTPUTS = TallyOutputs()


def reconfigure_tally_outputs():
    cfg = load_tally_config()
    bcms = [d.get("out_gpio") for d in (cfg.get("devices") or [])
            if isinstance(d.get("out_gpio"), int)]
    TALLY_OUTPUTS.configure(bcms)


def build_tally_diagnostics():
    """Aggregate everything you'd otherwise need 5 SSH calls for, into one
    JSON the UI polls. Per-device: ATEM state, software-wanted value,
    actual kernel pin level, polarity. Plus ATEM connection / PGM / PVW."""
    cfg = load_tally_config()
    atem = get_atem_state()
    pins = parse_gpio_state()
    sw_values = dict(TALLY_OUTPUTS._values)  # type: ignore[attr-defined]
    latched   = set(TALLY_OUTPUTS._latched)  # type: ignore[attr-defined]

    devices_out = []
    for d in cfg.get("devices") or []:
        bcm = d.get("out_gpio")
        active_high = bool(d.get("out_active_high", False))
        state = tally_state_for_device(cfg, d.get("id"), atem_state=atem)

        # What the software thinks the pin's "logical" state is right now.
        # _values: True = pin driven LOW. For active-low this means "on",
        # for active-high it means "off".
        sw_low = sw_values.get(bcm) if isinstance(bcm, int) else None
        sw_on  = None
        if sw_low is not None:
            sw_on = sw_low if not active_high else (not sw_low)

        # Actual kernel pin level (independent of software).
        pin_info = pins.get(str(bcm), {}) if isinstance(bcm, int) else {}
        pin_level = pin_info.get("value")  # 0=lo, 1=hi
        kernel_on = None
        if pin_level is not None:
            # active-low: pin LOW (0) means on
            # active-high: pin HIGH (1) means on
            kernel_on = (pin_level == 0) if not active_high else (pin_level == 1)

        consistent = (sw_on == kernel_on) if (sw_on is not None and kernel_on is not None) else None

        devices_out.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "atem_input": d.get("input"),
            "atem_me": d.get("me", 1),
            "state": state,             # pgm | pvw | safe | offline | unknown
            "out_gpio": bcm,
            "out_trigger": d.get("out_trigger", "pgm"),
            "out_active_high": active_high,
            "sw_on": sw_on,             # what code thinks (True=lamp should be on)
            "pin_level": pin_level,     # 0=lo, 1=hi, None=not claimed
            "kernel_on": kernel_on,     # True if pin's physical level matches "on" for this polarity
            "consistent": consistent,   # True if sw and kernel agree
            "latched": (bcm in latched) if isinstance(bcm, int) else False,
        })

    return {
        "atem": {
            "connected": atem.get("connected", False),
            "pgm": atem.get("pgm"),
            "pvw": atem.get("pvw"),
            "aux": atem.get("aux"),
            "error": atem.get("error"),
        },
        "devices": devices_out,
        "claimed_bcms": list(TALLY_OUTPUTS._bcms),  # type: ignore[attr-defined]
        "tally_out_error": TALLY_OUTPUTS._error,    # type: ignore[attr-defined]
    }


# ---------------------------------------------------------------------------
# pi-gpio-watcher service control (used by Tally tab to free GPIO pins
# so Companion's RPi_GPIO module can drive them as tally outputs).
# ---------------------------------------------------------------------------
WATCHER_UNIT = "pi-gpio-watcher.service"


def watcher_status():
    def _run(args):
        try:
            r = subprocess.run(["systemctl", *args, WATCHER_UNIT],
                               capture_output=True, text=True, timeout=3)
            return (r.stdout or r.stderr).strip()
        except Exception as e:
            return f"error: {e}"
    active = _run(["is-active"])
    enabled = _run(["is-enabled"])
    return {
        "active": active == "active",
        "enabled": enabled == "enabled",
        "active_state": active,
        "enabled_state": enabled,
    }


def watcher_set(action):
    if action == "enable":
        cmd = ["systemctl", "enable", "--now", WATCHER_UNIT]
    elif action == "disable":
        cmd = ["systemctl", "disable", "--now", WATCHER_UNIT]
    else:
        raise ValueError("action must be 'enable' or 'disable'")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")


def tally_state_for_device(cfg, device_id, atem_state=None):
    """Return 'pgm' | 'pvw' | 'safe' | 'offline' | 'unknown' for a device.

    Considers device.me (which ME bus) and device.aux (list of aux outputs
    that also count as PGM if the device's input is routed there).
    If device_id is a plain integer string (e.g. "5"), treat it as an
    ad-hoc device watching that input on ME 1.
    """
    if atem_state is None:
        atem_state = get_atem_state()
    if not atem_state.get("connected"):
        return "offline"

    device = next((d for d in cfg.get("devices", []) if d.get("id") == device_id), None)
    if device is None and isinstance(device_id, str) and device_id.isdigit():
        # Ad-hoc device: URL path is the input number itself.
        device = {"input": int(device_id), "me": 1, "aux": []}
    if not device:
        return "unknown"

    inp = device.get("input")
    if not isinstance(inp, int):
        return "safe"
    me = device.get("me") or 1
    aux_watch = device.get("aux") or []

    # atem_watcher writes pgm/pvw as {"<me>": input_num} dicts (0-indexed ME),
    # older versions used [{me, input}] lists — support both.
    def _collect(bus):
        out = set()
        if isinstance(bus, dict):
            for k, v in bus.items():
                try:
                    # atem_watcher uses 0-indexed ME; device.me is 1-indexed
                    if int(k) == (me - 1) and isinstance(v, int):
                        out.add(v)
                except Exception:
                    pass
        elif isinstance(bus, list):
            for e in bus:
                if isinstance(e, dict) and e.get("me") == me:
                    out.add(e.get("input"))
        return out

    pgm_inputs = _collect(atem_state.get("pgm"))
    pvw_inputs = _collect(atem_state.get("pvw"))

    # If any watched aux output carries this input, treat as PGM.
    atem_aux = atem_state.get("aux") or {}
    if isinstance(atem_aux, dict):
        for a in aux_watch:
            if atem_aux.get(str(a)) == inp:
                return "pgm"

    if inp in pgm_inputs:
        return "pgm"
    if inp in pvw_inputs:
        return "pvw"
    return "safe"


# ---------------------------------------------------------------------------
# Event log (JSONL)
# ---------------------------------------------------------------------------
def log_event(kind, **fields):
    """Append a single JSON line to the event log. Thread-safe, best-effort.

    kind: short string like 'tally', 'config', 'atem', 'bindings'.
    """
    rec = {"ts": time.time(), "kind": kind}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with EVENT_LOG_LOCK:
            EVENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            # simple size-based rotation
            try:
                if EVENT_LOG_FILE.exists() and EVENT_LOG_FILE.stat().st_size > EVENT_LOG_MAX:
                    rotated = EVENT_LOG_FILE.with_suffix(".log.1")
                    try:
                        if rotated.exists():
                            rotated.unlink()
                    except Exception:
                        pass
                    os.replace(EVENT_LOG_FILE, rotated)
            except Exception:
                pass
            with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def read_event_log(limit=200):
    """Return most recent <limit> event lines (newest last) as list of dicts."""
    if not EVENT_LOG_FILE.exists():
        return []
    try:
        # Read last N lines efficiently-ish (log stays <= 1MB).
        with open(EVENT_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max(1, int(limit)):]
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                out.append({"ts": 0, "kind": "raw", "line": ln})
        return out
    except Exception:
        return []


TALLY_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tally __ID__</title>
<style>
  html,body{margin:0;padding:0;height:100%;background:#000;color:#fff;font-family:system-ui,sans-serif;overflow:hidden}
  #bg{position:fixed;inset:0;background:#000;transition:background 80ms linear}
  #label{position:fixed;top:10px;left:10px;font-size:14px;opacity:.6;mix-blend-mode:difference;pointer-events:none}
  #state{position:fixed;bottom:10px;left:10px;font-size:12px;opacity:.5;mix-blend-mode:difference;pointer-events:none}
  #bg.pgm{background:#e11d48}
  #bg.pvw{background:#16a34a}
  #bg.safe{background:#111}
  #bg.offline{background:#1e293b}
  body:fullscreen #label,body:fullscreen #state{display:none}
</style></head><body>
<div id="bg" class="offline"></div>
<div id="label">__NAME__</div>
<div id="state">verbinde ...</div>
<script>
(function(){
  var bg=document.getElementById('bg');
  var st=document.getElementById('state');
  function set(state){
    bg.className='';
    bg.classList.add(state);
    st.textContent=state.toUpperCase();
  }
  function connect(){
    try { var ev=new EventSource('/tally/stream?id=__ID__'); } catch(e){ return fallback(); }
    ev.onmessage=function(e){ try{ var d=JSON.parse(e.data); set(d.state||'safe'); }catch(_){} };
    ev.onerror=function(){ set('offline'); ev.close(); setTimeout(connect,2000); };
  }
  function fallback(){
    set('offline');
    setTimeout(function poll(){
      fetch('/tally/state?id=__ID__',{cache:'no-store'}).then(r=>r.json()).then(d=>{
        set(d.state||'safe'); setTimeout(poll,500);
      }).catch(()=>{ set('offline'); setTimeout(poll,2000); });
    },100);
  }
  connect();
  document.addEventListener('click',function(){
    if(!document.fullscreenElement) document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen();
    else document.exitFullscreen&&document.exitFullscreen();
  });
})();
</script></body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit=64 * 1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            return b""
        return self.rfile.read(length)

    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/setup-guide.html"
        elif self.path == "/ipconfig":
            self._send_json(get_ipconfig())
            return
        elif self.path == "/gpio":
            self._send_json(parse_gpio_state())
            return
        elif self.path == "/tally-diagnostics":
            self._send_json(build_tally_diagnostics())
            return
        elif self.path == "/numato":
            self._send_json(get_numato_state())
            return
        elif self.path == "/bindings":
            self._send_json(load_bindings())
            return
        elif self.path == "/atem":
            self._send_json(get_atem_state())
            return
        elif self.path == "/tally-config":
            self._send_json(load_tally_config())
            return
        elif self.path == "/tally-out":
            self._send_json(TALLY_OUTPUTS.state())
            return
        elif self.path == "/service/pi-gpio-watcher":
            self._send_json(watcher_status())
            return
        elif self.path.startswith("/logs"):
            self._handle_logs()
            return
        elif self.path.startswith("/tally/state"):
            self._handle_tally_state()
            return
        elif self.path.startswith("/tally/stream"):
            self._handle_tally_stream()
            return
        elif self.path.startswith("/tally/"):
            self._handle_tally_page()
            return
        return super().do_GET()

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(self.path).query)

    def _device_id_from_path(self):
        # /tally/<id> or /tally/state?id=... or /tally/stream?id=...
        from urllib.parse import urlparse
        p = urlparse(self.path)
        parts = [seg for seg in p.path.split("/") if seg]
        if len(parts) >= 2 and parts[0] == "tally" and parts[1] not in ("state", "stream"):
            return parts[1]
        qs = self._query()
        v = qs.get("id", [""])[0]
        return v

    def _handle_tally_page(self):
        did = self._device_id_from_path()
        if not re.match(r"^[A-Za-z0-9_\-]{1,32}$", did or ""):
            self.send_error(400, "bad device id")
            return
        cfg = load_tally_config()
        device = next((d for d in cfg.get("devices", []) if d.get("id") == did), None)
        if device:
            name = device.get("name", did)
        elif did.isdigit():
            # Ad-hoc: use ATEM input long name if available
            atem = get_atem_state() or {}
            info = (atem.get("inputs") or {}).get(did) or {}
            long_name = info.get("long") or ""
            name = f"Input {did}" + (f" – {long_name}" if long_name else "")
        else:
            name = did
        html = (TALLY_PAGE.replace("__ID__", did).replace("__NAME__", name)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _handle_tally_state(self):
        did = self._device_id_from_path()
        if not did:
            self.send_error(400, "missing id")
            return
        cfg = load_tally_config()
        state = tally_state_for_device(cfg, did)
        self._send_json({"id": did, "state": state})

    def _handle_tally_stream(self):
        did = self._device_id_from_path()
        if not did:
            self.send_error(400, "missing id")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_state = None
        # 5 Hz tick, write a heartbeat every ~15 s to keep proxies happy
        heartbeat = 0
        try:
            while True:
                cfg = load_tally_config()
                state = tally_state_for_device(cfg, did)
                if state != last_state:
                    payload = json.dumps({"id": did, "state": state})
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    last_state = state
                heartbeat += 1
                if heartbeat >= 75:  # ~15s at 5 Hz
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    heartbeat = 0
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def _handle_logs(self):
        from urllib.parse import urlparse, parse_qs
        p = urlparse(self.path)
        qs = parse_qs(p.query)
        if p.path == "/logs/stream":
            self._handle_logs_stream()
            return
        try:
            limit = int(qs.get("limit", ["200"])[0])
        except Exception:
            limit = 200
        limit = max(1, min(2000, limit))
        self._send_json({"events": read_event_log(limit=limit)})

    def _handle_logs_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            EVENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if not EVENT_LOG_FILE.exists():
                EVENT_LOG_FILE.touch()
            with open(EVENT_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                heartbeat = 0
                while True:
                    ln = f.readline()
                    if ln:
                        ln = ln.strip()
                        if ln:
                            self.wfile.write(f"data: {ln}\n\n".encode())
                            self.wfile.flush()
                        heartbeat = 0
                    else:
                        time.sleep(0.3)
                        heartbeat += 1
                        if heartbeat >= 30:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            heartbeat = 0
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def do_POST(self):
        if self.path == "/bindings":
            body = self._read_body()
            try:
                data = json.loads(body or b"[]")
                if not isinstance(data, list):
                    raise ValueError("expected JSON array")
                for b in data:
                    validate_binding(b)
                save_bindings(data)
                log_event("bindings", action="save", count=len(data))
                self._send_json({"ok": True, "count": len(data)})
            except Exception as e:
                log_event("bindings", action="save_failed", error=str(e))
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        if self.path == "/tally-config":
            body = self._read_body()
            try:
                data = json.loads(body or b"{}")
                validate_tally_config(data)
                save_tally_config(data)
                reconfigure_tally_outputs()
                devs = data.get("devices") or []
                log_event("config", action="tally_save",
                          atem_ip=data.get("atem_ip"),
                          devices=len(devs),
                          out_gpios=sum(1 for d in devs if isinstance(d.get("out_gpio"), int)),
                          in_gpios=sum(1 for d in devs if isinstance(d.get("in_gpio"), int)))
                self._send_json({"ok": True})
            except Exception as e:
                log_event("config", action="tally_save_failed", error=str(e))
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        if self.path.startswith("/tally-out/"):
            self._handle_tally_out_post()
            return
        if self.path == "/input-test":
            self._handle_input_test()
            return
        if self.path == "/service/pi-gpio-watcher":
            body = self._read_body()
            try:
                data = json.loads(body or b"{}")
                action = data.get("action")
                if action not in ("enable", "disable"):
                    raise ValueError("action must be 'enable' or 'disable'")
                watcher_set(action)
                log_event("guide", action="watcher_" + action)
                resp = {"ok": True}
                resp.update(watcher_status())
                self._send_json(resp)
            except Exception as e:
                log_event("guide", action="watcher_failed", error=str(e))
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        self.send_error(404)

    def _handle_tally_out_post(self):
        # /tally-out/<bcm>/(on|off|pulse|latch-on|latch-off|release)
        from urllib.parse import urlparse, parse_qs
        p = urlparse(self.path)
        parts = [seg for seg in p.path.split("/") if seg]
        if len(parts) != 3 or parts[0] != "tally-out":
            self._send_json({"ok": False, "error": "bad path"}, code=400)
            return
        try:
            bcm = int(parts[1])
        except ValueError:
            self._send_json({"ok": False, "error": "bad bcm"}, code=400)
            return
        action = parts[2]
        if action == "on":
            ok, msg = TALLY_OUTPUTS.set(bcm, True)
        elif action == "off":
            ok, msg = TALLY_OUTPUTS.set(bcm, False)
        elif action == "pulse":
            qs = parse_qs(p.query)
            try:
                ms = int(qs.get("ms", ["200"])[0])
            except ValueError:
                self._send_json({"ok": False, "error": "bad ms"}, code=400)
                return
            ok, msg = TALLY_OUTPUTS.pulse(bcm, ms)
        elif action == "latch-on":
            ok, msg = TALLY_OUTPUTS.latch(bcm, True)
        elif action == "latch-off":
            ok, msg = TALLY_OUTPUTS.latch(bcm, False)
        elif action == "release":
            ok, msg = TALLY_OUTPUTS.release(bcm)
        else:
            self._send_json({"ok": False, "error": "unknown action"}, code=400)
            return
        log_event("tally-out", bcm=bcm, action=action, ok=ok, msg=msg)
        self._send_json({"ok": ok, "message": msg, "state": TALLY_OUTPUTS.state()},
                        code=200 if ok else 409)

    def _handle_input_test(self):
        """Simulate a trigger-button press for a device.

        Body: {"id": "<device_id>", "dry_run": bool, "edge": "falling"|"rising"}
        dry_run=true  → log an `input` event but don't fire the action
        dry_run=false → fire the configured action exactly as the watcher would

        Either way the event lands in the Live-Tally-Log so the user can
        verify the wiring/config without touching the physical button.
        """
        import urllib.request, urllib.parse
        body = self._read_body()
        try:
            data = json.loads(body or b"{}")
        except Exception as e:
            self._send_json({"ok": False, "error": f"bad JSON: {e}"}, code=400)
            return
        did = data.get("id")
        dry = bool(data.get("dry_run", True))
        edge = data.get("edge", "falling")
        if edge not in ("falling", "rising"):
            self._send_json({"ok": False, "error": "edge must be falling/rising"}, code=400)
            return
        cfg = load_tally_config()
        dev = next((d for d in (cfg.get("devices") or []) if d.get("id") == did), None)
        if not dev:
            self._send_json({"ok": False, "error": f"device {did!r} not found"}, code=404)
            return
        bcm = dev.get("in_gpio")
        if not isinstance(bcm, int):
            self._send_json({"ok": False, "error": "device has no in_gpio configured"}, code=400)
            return
        act = dev.get("in_action_type", "none")
        label = dev.get("name") or did
        trig = dev.get("in_edge", "falling")
        if trig != "both" and trig != edge:
            log_event("input", bcm=bcm, edge=edge, label=label,
                      action="ignored", reason=f"trigger={trig}",
                      dry_run=dry, simulated=True)
            self._send_json({"ok": True, "fired": False,
                             "msg": f"edge {edge} ignored (trigger={trig})"})
            return
        if act == "none":
            log_event("input", bcm=bcm, edge=edge, label=label,
                      action="none", dry_run=dry, simulated=True)
            self._send_json({"ok": True, "fired": False, "msg": "no action configured"})
            return
        # Plan the action.
        plan = {"label": label, "bcm": bcm, "edge": edge, "simulated": True,
                "dry_run": dry}
        if act == "atem_aux":
            aux = int(dev.get("in_atem_aux") or 0)
            src = dev.get("in_atem_source")
            if not isinstance(src, int):
                src = dev.get("input")
            plan.update(action="atem_aux", aux=aux, source=src)
        elif act == "companion":
            mode = dev.get("in_companion_mode", "tap")
            sub = "press"
            if mode == "hold":
                sub = "down" if edge == "falling" else "up"
            plan.update(action="companion_" + sub,
                        page=dev.get("in_companion_page", 1),
                        row=dev.get("in_companion_row", 0),
                        column=dev.get("in_companion_col", 0))
        else:
            self._send_json({"ok": False, "error": f"unknown action_type {act!r}"}, code=400)
            return
        if dry:
            log_event("input", **plan, ok=True)
            self._send_json({"ok": True, "fired": False, "dry_run": True,
                             "msg": "dry-run logged"})
            return
        # Real fire: replicate the watcher's behavior here.
        try:
            if act == "atem_aux":
                if not plan["aux"] or not isinstance(plan["source"], int):
                    raise RuntimeError("missing aux/source")
                sock_path = Path("/run/pi-guide/atem-cmd.sock")
                if not sock_path.exists():
                    raise RuntimeError("atem-cmd socket not present (atem watcher down?)")
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2.0)
                try:
                    s.connect(str(sock_path))
                    cmd = {"cmd": "set_aux", "aux": plan["aux"], "source": plan["source"]}
                    s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
                    resp = s.recv(4096).decode("utf-8", errors="replace").strip()
                    if resp:
                        j = json.loads(resp.split("\n", 1)[0])
                        if not j.get("ok"):
                            raise RuntimeError(j.get("error", "atem cmd rejected"))
                finally:
                    try: s.close()
                    except Exception: pass
            elif act == "companion":
                sub = plan["action"].split("_", 1)[1]
                url = f"http://localhost:8000/api/location/{plan['page']}/{plan['row']}/{plan['column']}/{sub}"
                req = urllib.request.Request(url, data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=5):
                    pass
            log_event("input", **plan, ok=True)
            self._send_json({"ok": True, "fired": True, "msg": "fired"})
        except Exception as e:
            log_event("input", **plan, ok=False, error=str(e))
            self._send_json({"ok": False, "fired": False, "error": str(e)}, code=502)

    def log_message(self, *a, **k):
        pass


def start_transition_logger():
    """Background thread that logs tally state changes and ATEM link events.

    Central place so that a state change is logged once, not once per SSE
    subscriber.
    """
    last_states = {}
    last_atem_connected = None
    last_pgm = {}   # me-index (0-based, as atem_watcher emits) -> input num
    last_pvw = {}

    def _bus_dict(bus):
        """Normalize pgm/pvw payload to {me_index_int: input_num}."""
        out = {}
        if isinstance(bus, dict):
            for k, v in bus.items():
                try:
                    out[int(k)] = v if isinstance(v, int) else None
                except Exception:
                    pass
        elif isinstance(bus, list):
            for e in bus:
                if isinstance(e, dict):
                    try:
                        out[int(e.get("me")) - 1] = e.get("input")
                    except Exception:
                        pass
        return out

    def loop():
        nonlocal last_atem_connected
        while True:
            try:
                cfg = load_tally_config()
                atem = get_atem_state()
                connected = bool(atem.get("connected"))
                if connected != last_atem_connected:
                    log_event("atem", connected=connected,
                              error=atem.get("error") or "")
                    last_atem_connected = connected
                pgm_now = _bus_dict(atem.get("pgm"))
                pvw_now = _bus_dict(atem.get("pvw"))
                for me_idx, inp in pgm_now.items():
                    if last_pgm.get(me_idx) != inp:
                        log_event("atem", bus="pgm", me=me_idx + 1, input=inp)
                        last_pgm[me_idx] = inp
                for me_idx, inp in pvw_now.items():
                    if last_pvw.get(me_idx) != inp:
                        log_event("atem", bus="pvw", me=me_idx + 1, input=inp)
                        last_pvw[me_idx] = inp
                current_ids = set()
                for d in cfg.get("devices") or []:
                    did = d.get("id")
                    if not did:
                        continue
                    current_ids.add(did)
                    state = tally_state_for_device(cfg, did, atem_state=atem)
                    prev = last_states.get(did)
                    if prev != state:
                        log_event("tally", id=did, state=state,
                                  input=d.get("input"), me=d.get("me", 1))
                        last_states[did] = state
                    # Auto-drive hardware tally output if configured.
                    bcm = d.get("out_gpio")
                    trig = d.get("out_trigger", "pgm")
                    if isinstance(bcm, int) and trig != "manual":
                        if trig == "pgm_pvw":
                            on_air = state in ("pgm", "pvw")
                        else:
                            on_air = state == "pgm"
                        # `set()` semantics: True = drive LOW, False = drive HIGH.
                        # active_high inverts that so the on-air state drives HIGH.
                        active_high = bool(d.get("out_active_high", False))
                        TALLY_OUTPUTS.set(bcm, on_air != active_high,
                                          respect_pulse=True)
                for gone in [k for k in last_states if k not in current_ids]:
                    last_states.pop(gone, None)
            except Exception:
                pass
            time.sleep(0.25)

    t = threading.Thread(target=loop, name="transition-logger", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # Bind address: default to all interfaces so admins can reach it from
    # the LAN. Set GUIDE_HOST=127.0.0.1 in the systemd unit to lock it down.
    host = os.environ.get("GUIDE_HOST", "0.0.0.0")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    try:
        reconfigure_tally_outputs()
    except Exception as e:
        print(f"[tally-out] init failed: {e}", flush=True)
    start_transition_logger()
    log_event("guide", action="start", host=host, port=PORT)
    # Threading server so SSE streams do not block other requests.
    with socketserver.ThreadingTCPServer((host, PORT), Handler) as httpd:
        httpd.daemon_threads = True
        print(f"Serving guide on http://{host}:{PORT}/")
        httpd.serve_forever()
