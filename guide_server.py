#!/usr/bin/env python3
"""Serves setup-guide.html, live GPIO status, and bindings API on port 8080."""
import http.server
import json
import os
import re
import socket
import socketserver
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8080
DEBUG_GPIO = Path("/sys/kernel/debug/gpio")
BINDINGS_FILE = Path("/opt/pi-guide/bindings.json")

# Physical header pin for each BCM GPIO (2..27)
BCM_TO_PIN = {
    2: 3, 3: 5, 4: 7, 5: 29, 6: 31, 7: 26, 8: 24, 9: 21,
    10: 19, 11: 23, 12: 32, 13: 33, 14: 8, 15: 10, 16: 36,
    17: 11, 18: 12, 19: 35, 20: 38, 21: 40, 22: 15, 23: 16,
    24: 18, 25: 22, 26: 37, 27: 13,
}

LINE_RE = re.compile(
    r"gpio-\d+\s+\(\s*GPIO(\d+)\s*(?:\|([^)]*?))?\s*\)"
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
        bcm = int(m.group(1))
        if bcm not in BCM_TO_PIN:
            continue
        consumer = (m.group(2) or "").strip()
        direction = m.group(3) or None
        value_s = m.group(4)
        value = 1 if value_s == "hi" else (0 if value_s == "lo" else None)
        result[str(bcm)] = {
            "bcm": bcm,
            "pin": BCM_TO_PIN[bcm],
            "consumer": consumer,
            "claimed": bool(consumer),
            "direction": direction,
            "value": value,
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
    bcm = b.get("bcm")
    if not isinstance(bcm, int) or bcm not in BCM_TO_PIN:
        raise ValueError(f"bcm must be one of {sorted(BCM_TO_PIN)}")
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
        elif self.path == "/gpio":
            self._send_json(parse_gpio_state())
            return
        elif self.path == "/bindings":
            self._send_json(load_bindings())
            return
        return super().do_GET()

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
                self._send_json({"ok": True, "count": len(data)})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, code=400)
            return
        self.send_error(404)

    def log_message(self, *a, **k):
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Serving guide on http://0.0.0.0:{PORT}/")
        httpd.serve_forever()
