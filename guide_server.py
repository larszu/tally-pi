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
import urllib.error
import urllib.request
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


def parse_gpio_state():
    result = {}
    try:
        text = DEBUG_GPIO.read_text(errors="replace")
    except Exception as e:
        return {"error": str(e)}
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
            "global_line": global_line,
            "sysfs": consumer == "sysfs",
        }
    return result


def sysfs_release(bcm):
    """Try to release a sysfs-held GPIO line. Returns (ok, message)."""
    if bcm not in BCM_TO_PIN:
        return False, f"unknown bcm {bcm}"
    state = parse_gpio_state()
    info = state.get(str(bcm)) if isinstance(state, dict) else None
    if not info or "global_line" not in info:
        return False, "gpio not found in debug/gpio"
    if info.get("consumer") != "sysfs":
        return False, f"not held by sysfs (consumer={info.get('consumer') or 'none'})"
    line = info["global_line"]
    unexport = Path("/sys/class/gpio/unexport")
    if not unexport.exists():
        return False, "/sys/class/gpio/unexport not present"
    try:
        unexport.write_text(f"{line}\n")
        return True, f"unexported gpio {line} (GPIO{bcm})"
    except Exception as e:
        return False, f"unexport failed: {e}"


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


COMPANION_API_PATH_RE = re.compile(
    r"^/api/(location/\d+/\d+/\d+/(press|down|up)|custom-variable/[A-Za-z0-9_]{1,64}/value)$"
)


def companion_api_call(path, method="POST", body=b"", content_type="text/plain; charset=utf-8"):
    """Call local Companion API safely through an allow-listed proxy."""
    if not isinstance(path, str) or not COMPANION_API_PATH_RE.match(path):
        raise ValueError("unsupported companion api path")
    method = (method or "POST").upper()
    if method not in ("GET", "POST"):
        raise ValueError("method must be GET or POST")

    url = f"http://127.0.0.1:8000{path}"
    headers = {}
    data = None
    if method == "POST":
        data = body if isinstance(body, (bytes, bytearray)) else b""
        headers["Content-Type"] = content_type or "text/plain; charset=utf-8"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, int(resp.status), text
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        return False, int(e.code), text


# ---------------------------------------------------------------------------
# Tally support (ATEM direct or TallyArbiter client)
# ---------------------------------------------------------------------------
DEFAULT_TALLY_CONFIG = {
    "mode": "atem",              # "atem" or "arbiter"
    "atem_ip": "",
    "arbiter_url": "http://localhost:4455",
    "devices": [],               # [{id, name, input, me?, aux?}]
}


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
    if cfg.get("mode") not in ("atem", "arbiter"):
        raise ValueError("mode must be 'atem' or 'arbiter'")
    atem_ip = cfg.get("atem_ip", "") or ""
    if atem_ip and not re.match(r"^[0-9a-zA-Z\.\-]{1,64}$", atem_ip):
        raise ValueError("atem_ip contains invalid characters")
    arb = cfg.get("arbiter_url", "") or ""
    if arb and not re.match(r"^https?://[A-Za-z0-9\.\-:/_]{1,256}$", arb):
        raise ValueError("arbiter_url must be http(s) URL")
    devs = cfg.get("devices") or []
    if not isinstance(devs, list):
        raise ValueError("devices must be list")
    ids = set()
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


def get_atem_state():
    if not ATEM_STATE_FILE.exists():
        return {"connected": False, "error": "atem watcher not running"}
    try:
        return json.loads(ATEM_STATE_FILE.read_text())
    except Exception:
        return {"connected": False, "error": "state parse error"}


def tally_state_for_device(cfg, device_id, atem_state=None):
    """Return 'pgm' | 'pvw' | 'safe' for a device based on current atem state.

    Considers device.me (which ME bus) and device.aux (list of aux outputs
    that also count as PGM if the device's input is routed there).
    Only implemented for mode=atem. mode=arbiter is handled client-side.

    If device_id is a plain integer string (e.g. "5"), treat it as an ad-hoc
    device watching that input on ME 1 — no config entry required.
    """
    if cfg.get("mode") != "atem":
        return "safe"
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
        elif self.path == "/hostname":
            self._send_text(socket.gethostname())
            return
        elif self.path == "/ipconfig":
            self._send_json(get_ipconfig())
            return
        elif self.path == "/gpio":
            self._send_json(parse_gpio_state())
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
        if cfg.get("mode") == "arbiter":
            # Redirect into the local TallyArbiter tally page.
            base = (cfg.get("arbiter_url") or "").rstrip("/")
            if not base:
                base = "http://localhost:4455"
            target = f"{base}/tally/{did}"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
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
        self._send_json({"id": did, "state": state, "mode": cfg.get("mode")})

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
                    payload = json.dumps({"id": did, "state": state, "mode": cfg.get("mode")})
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
                log_event("config", action="tally_save",
                          mode=data.get("mode"),
                          atem_ip=data.get("atem_ip"),
                          devices=len(data.get("devices") or []))
                self._send_json({"ok": True})
            except Exception as e:
                log_event("config", action="tally_save_failed", error=str(e))
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        if self.path == "/sysfs-release":
            body = self._read_body()
            try:
                data = json.loads(body or b"{}")
                bcm = data.get("bcm")
                if not isinstance(bcm, int):
                    raise ValueError("bcm must be int")
                ok, msg = sysfs_release(bcm)
                self._send_json({"ok": ok, "message": msg}, code=200 if ok else 409)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        if self.path == "/companion-api":
            body = self._read_body(limit=16 * 1024)
            try:
                data = json.loads(body or b"{}")
                path = data.get("path", "")
                method = data.get("method", "POST")
                payload = data.get("body", "")
                content_type = data.get("contentType", "text/plain; charset=utf-8")
                payload_b = str(payload).encode("utf-8")
                ok, status, resp_body = companion_api_call(
                    path=path,
                    method=method,
                    body=payload_b,
                    content_type=content_type,
                )
                log_event("guide", action="companion_proxy", path=path, status=status)
                self._send_json({
                    "ok": bool(ok),
                    "status": int(status),
                    "body": (resp_body or "")[:2000],
                }, code=200)
            except Exception as e:
                log_event("guide", action="companion_proxy_failed", error=str(e))
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        self.send_error(404)

    def log_message(self, *a, **k):
        pass


def start_transition_logger():
    """Background thread that logs tally state changes and ATEM link events.

    Central place so that a state change is logged once, not once per SSE
    subscriber.
    """
    last_states = {}
    last_atem_connected = None

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
                if cfg.get("mode") == "atem":
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
                    # drop removed devices
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
    start_transition_logger()
    log_event("guide", action="start", host=host, port=PORT)
    # Threading server so SSE streams do not block other requests.
    with socketserver.ThreadingTCPServer((host, PORT), Handler) as httpd:
        httpd.daemon_threads = True
        print(f"Serving guide on http://{host}:{PORT}/")
        httpd.serve_forever()
