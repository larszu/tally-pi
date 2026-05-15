#!/usr/bin/env python3
"""pi-tally — minimal tally driver and trigger button handler.

One process. Reads /opt/pi-guide/tally.json for config and
/run/pi-guide/atem.json for ATEM state (written by atem_watcher).

Each configured device may have:
  - out_gpio : pin driven LOW when this device's ATEM input is on PGM
  - in_gpio  : pin watched for falling edges; fires the configured action
  - in_action: {"type":"atem_aux", "aux":N, "source":M}
               {"type":"companion_press", "page":P, "row":R, "col":C}
               {"type":"none"}

Serves a single-page config + status UI on :8080,
plus a browser-tally page at /tally/<id>.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import tempfile
import threading
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import gpiod
from gpiod.line import Bias, Direction, Drive, Edge, Value

PORT       = 8080
CHIP       = "/dev/gpiochip0"
CONFIG     = Path("/opt/pi-guide/tally.json")
ATEM_STATE = Path("/run/pi-guide/atem.json")
ATEM_SOCK  = Path("/run/pi-guide/atem-cmd.sock")

# BCM pins safe to use on a Pi 5 40-pin header (no I2C/UART/SPI/EEPROM).
SAFE_BCMS = (4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27)


def log(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


# ─── Config ───────────────────────────────────────────────────────────────

def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {"atem_ip": "", "devices": []}
    except Exception as e:
        log("config", f"parse error: {e}")
        return {"atem_ip": "", "devices": []}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG.parent),
                               prefix=".tally.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG)
        try: os.chmod(CONFIG, 0o664)
        except Exception: pass
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise


def load_atem():
    try:
        return json.loads(ATEM_STATE.read_text())
    except Exception:
        return {"connected": False, "pgm": {}, "pvw": {}, "inputs": {}}


def device_state(device, atem):
    """'pgm' | 'pvw' | 'safe' | 'offline'."""
    if not atem.get("connected"):
        return "offline"
    inp = device.get("atem_input")
    me  = device.get("atem_me", 1)
    if not isinstance(inp, int):
        return "safe"
    me_key = str(int(me) - 1)
    if atem.get("pgm", {}).get(me_key) == inp:
        return "pgm"
    if atem.get("pvw", {}).get(me_key) == inp:
        return "pvw"
    return "safe"


# ─── Actions ─────────────────────────────────────────────────────────────

def atem_send_aux(aux, source):
    """Send {"cmd":"set_aux", aux, source} to atem_watcher's UNIX socket."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(ATEM_SOCK))
        msg = json.dumps({"cmd": "set_aux",
                          "aux": int(aux), "source": int(source)}) + "\n"
        s.sendall(msg.encode())
        s.close()
        return True, None
    except Exception as e:
        return False, str(e)


def companion_press(page, row, col):
    url = f"http://127.0.0.1:8000/api/location/{int(page)}/{int(row)}/{int(col)}/press"
    try:
        urllib.request.urlopen(url, data=b"", timeout=2)
        return True, None
    except Exception as e:
        return False, str(e)


# ─── GPIO ────────────────────────────────────────────────────────────────

class GPIOManager:
    """Owns ALL pi-tally GPIO claims. ONE request for outputs, ONE for inputs.

    Writes outputs atomically via set_values() with the full state dict —
    per-line set_value() on a multi-line request is unreliable on RP1.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.out_req = None
        self.in_req  = None
        self.out_bcms = []
        self.in_bcms  = []
        self.out_state = {}       # bcm -> bool (True = LOW = active)
        self.in_handlers = {}     # bcm -> callable(edge)
        self.error = None

    def configure(self, out_bcms, in_bcms, in_handlers):
        outs = sorted({int(b) for b in out_bcms if int(b) in SAFE_BCMS})
        ins  = sorted({int(b) for b in in_bcms  if int(b) in SAFE_BCMS} - set(outs))

        with self.lock:
            self._release_locked()
            self.in_handlers = dict(in_handlers)
            self.error = None

            if outs:
                try:
                    self.out_req = gpiod.request_lines(
                        CHIP, consumer="pi-tally-out",
                        config={b: gpiod.LineSettings(
                            direction=Direction.OUTPUT,
                            drive=Drive.PUSH_PULL,
                            bias=Bias.DISABLED,
                            output_value=Value.ACTIVE,  # HIGH = idle
                        ) for b in outs},
                    )
                    self.out_bcms = outs
                    self.out_state = {b: False for b in outs}
                    log("gpio", f"OUT claimed: {outs}")
                except Exception as e:
                    self.error = f"out claim failed: {e}"
                    log("gpio", self.error)

            if ins:
                try:
                    self.in_req = gpiod.request_lines(
                        CHIP, consumer="pi-tally-in",
                        config={b: gpiod.LineSettings(
                            direction=Direction.INPUT,
                            bias=Bias.PULL_UP,
                            edge_detection=Edge.BOTH,
                            debounce_period=timedelta(milliseconds=20),
                        ) for b in ins},
                    )
                    self.in_bcms = ins
                    log("gpio", f"IN claimed:  {ins}")
                except Exception as e:
                    self.error = (self.error + " | " if self.error else "") + f"in claim failed: {e}"
                    log("gpio", f"in claim failed: {e}")

    def _release_locked(self):
        for req in (self.out_req, self.in_req):
            if req is not None:
                try: req.release()
                except Exception: pass
        self.out_req = None
        self.in_req = None
        self.out_bcms = []
        self.in_bcms = []
        self.out_state = {}

    def set_outputs(self, desired):
        """desired: {bcm: bool}. Updates internal state and writes ALL output
        pins to the chip in one atomic transaction."""
        with self.lock:
            if not self.out_req:
                return
            for b, v in desired.items():
                if b in self.out_state:
                    self.out_state[b] = bool(v)
            values = {b: (Value.INACTIVE if self.out_state[b] else Value.ACTIVE)
                      for b in self.out_bcms}
            try:
                self.out_req.set_values(values)
            except Exception as e:
                log("gpio", f"set_values failed: {e}")

    def poll_inputs(self):
        if not self.in_req:
            time.sleep(0.5)
            return
        if self.in_req.wait_edge_events(timeout=timedelta(milliseconds=500)):
            for ev in self.in_req.read_edge_events():
                bcm = ev.line_offset
                edge = ("falling" if ev.event_type == ev.Type.FALLING_EDGE
                        else "rising")
                handler = self.in_handlers.get(bcm)
                if handler:
                    try: handler(edge)
                    except Exception as e:
                        log("input", f"handler GPIO {bcm} crashed: {e}")


GPIO_MGR = GPIOManager()


# ─── Bind config → GPIOs ─────────────────────────────────────────────────

def make_handler(device):
    name = device.get("name") or f"GPIO{device.get('in_gpio')}"
    action = device.get("in_action") or {"type": "none"}
    kind = action.get("type", "none")

    def handler(edge):
        if edge != "falling":  # only fire on press (active-low button)
            return
        if kind == "atem_aux":
            aux = action.get("aux")
            src = action.get("source")
            if src is None:
                src = device.get("atem_input")
            if aux is None or src is None:
                log("trigger", f"{name}: atem_aux missing aux/source")
                return
            ok, err = atem_send_aux(aux, src)
            log("trigger",
                f"{name} → ATEM Aux {aux}={src}" + ("" if ok else f"  FAIL: {err}"))
        elif kind == "companion_press":
            ok, err = companion_press(
                action.get("page", 1),
                action.get("row", 0),
                action.get("col", 0),
            )
            log("trigger",
                f"{name} → Companion press" + ("" if ok else f"  FAIL: {err}"))
        elif kind == "none":
            log("trigger", f"{name} pressed (no action)")
        else:
            log("trigger", f"{name}: unknown action {kind!r}")

    return handler


def reconfigure():
    cfg = load_config()
    out_bcms = []
    in_bcms = []
    in_handlers = {}
    for d in cfg.get("devices") or []:
        og = d.get("out_gpio")
        if isinstance(og, int):
            out_bcms.append(og)
        ig = d.get("in_gpio")
        if isinstance(ig, int):
            in_bcms.append(ig)
            in_handlers[ig] = make_handler(d)
    GPIO_MGR.configure(out_bcms, in_bcms, in_handlers)


# ─── Threads ─────────────────────────────────────────────────────────────

def driver_loop():
    """Every 250 ms: compute desired output state from ATEM + write atomically."""
    while True:
        try:
            cfg = load_config()
            atem = load_atem()
            desired = {}
            for d in cfg.get("devices") or []:
                bcm = d.get("out_gpio")
                if not isinstance(bcm, int):
                    continue
                desired[bcm] = (device_state(d, atem) == "pgm")
            GPIO_MGR.set_outputs(desired)
        except Exception as e:
            log("driver", f"loop error: {e}")
        time.sleep(0.25)


def input_loop():
    while True:
        try:
            GPIO_MGR.poll_inputs()
        except Exception as e:
            log("input", f"loop error: {e}")
            time.sleep(0.5)


# ─── HTTP ────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body, code=200, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(INDEX_HTML, ctype="text/html; charset=utf-8"); return
        if path == "/api/state":
            cfg = load_config()
            atem = load_atem()
            tally = {}
            for d in cfg.get("devices") or []:
                did = d.get("id") or d.get("name") or ""
                tally[did] = device_state(d, atem)
            self._send({
                "atem":     atem,
                "config":   cfg,
                "tally":    tally,
                "outputs":  {str(k): v for k, v in GPIO_MGR.out_state.items()},
                "claimed":  {"out": GPIO_MGR.out_bcms, "in": GPIO_MGR.in_bcms},
                "error":    GPIO_MGR.error,
            })
            return
        if path.startswith("/tally/"):
            did = path[len("/tally/"):]
            self._send(TALLY_HTML.replace("__ID__", did),
                       ctype="text/html; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""

        if path == "/api/config":
            try:
                cfg = json.loads(body or b"{}")
                save_config(cfg)
                reconfigure()
                self._send({"ok": True})
            except Exception as e:
                self._send({"ok": False, "error": str(e)}, 400)
            return

        # /api/test/<bcm>/<action>
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "test":
            try:
                bcm = int(parts[2])
                act = parts[3]
                if act == "on":
                    GPIO_MGR.set_outputs({bcm: True})
                elif act == "off":
                    GPIO_MGR.set_outputs({bcm: False})
                elif act == "pulse":
                    GPIO_MGR.set_outputs({bcm: True})
                    threading.Timer(
                        0.5, lambda: GPIO_MGR.set_outputs({bcm: False})
                    ).start()
                else:
                    raise ValueError(f"unknown action {act!r}")
                self._send({"ok": True})
            except Exception as e:
                self._send({"ok": False, "error": str(e)}, 400)
            return

        self.send_error(404)


# ─── HTML ────────────────────────────────────────────────────────────────

INDEX_HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>pi-tally</title>
<style>
*{box-sizing:border-box}
body{font:14px/1.45 system-ui,sans-serif;background:#0b1020;color:#e2e8f0;margin:0;padding:20px}
h1,h2{margin:0 0 10px}h1{font-size:22px}h2{font-size:16px;margin:24px 0 8px}
.card{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:14px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input,select,button{font:inherit;padding:5px 8px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px}
input[type=number]{width:80px}
button{cursor:pointer;background:#3b82f6;border-color:#3b82f6}
button:hover{background:#2563eb}
button.danger{background:#dc2626;border-color:#dc2626}
button.test{background:#65a30d;border-color:#65a30d}
table{border-collapse:collapse;width:100%}
td,th{padding:6px;text-align:left;border-bottom:1px solid #334155;vertical-align:middle}
th{font-size:11px;text-transform:uppercase;color:#94a3b8;letter-spacing:.05em}
.s-pgm{color:#ef4444;font-weight:600}
.s-pvw{color:#22c55e;font-weight:600}
.s-safe{color:#64748b}
.s-offline{color:#fbbf24}
.pin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:6px}
.pin{padding:6px;border-radius:4px;text-align:center;background:#0f172a;border:1px solid #1e293b;font-size:11px}
.pin .b{font-weight:600;display:block;font-size:13px;margin-bottom:2px}
.pin.out-lo{background:#7f1d1d;color:#fff;border-color:#dc2626}
.pin.out-hi{background:#1e3a8a;color:#cbd5e1;border-color:#3b82f6}
.pin.input{background:#064e3b;color:#fff;border-color:#10b981}
a{color:#60a5fa;text-decoration:none}a:hover{text-decoration:underline}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#94a3b8}
.ok{color:#22c55e}.err{color:#ef4444}
.hint{color:#94a3b8;font-size:12px;margin:2px 0 10px}
</style></head><body>

<h1>pi-tally <span id="atemstatus" class="mono" style="margin-left:10px"></span></h1>
<p class="hint">Ein Pi-GPIO steuert Tally-Lampen über die Bus-Anbindung. ATEM-Status kommt vom <code>atem_watcher</code>. Idle = HIGH, „on air" = LOW (Pull nach GND).</p>

<div class="card">
  <h2>ATEM-IP</h2>
  <div class="row">
    <input id="atem_ip" placeholder="192.168.10.22" style="width:200px">
    <button onclick="save()">Speichern</button>
    <span id="atem_info" class="mono"></span>
  </div>
</div>

<div class="card">
  <h2>Geräte</h2>
  <p class="hint">
    <strong>Tally-Out GPIO:</strong> Pi zieht Pin nach GND wenn die ATEM-Quelle dieses Geräts auf PGM ist.<br>
    <strong>Trigger-In GPIO:</strong> Taster zwischen Pin und GND. Auf Druck (fallende Flanke) wird die Aktion ausgeführt.<br>
    <strong>Tally-URL:</strong> Browser-Page für Smartphone/Tablet; zeigt rot wenn PGM, grün wenn PVW.
  </p>
  <table>
    <thead><tr>
      <th>ID</th><th>Name</th><th>ATEM-Input</th><th>State</th>
      <th>Tally-Out</th><th>Trigger-In</th><th>Aktion</th>
      <th>Tally-URL</th><th></th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="row" style="margin-top:10px">
    <button onclick="addDev()">+ Gerät</button>
    <button onclick="save()">Speichern</button>
    <span id="saveinfo" class="mono"></span>
  </div>
</div>

<div class="card">
  <h2>GPIO-Status (live)</h2>
  <p class="hint">Aktuelle Zustände der von pi-tally beanspruchten Pins. Andere GPIOs (I²C, UART, SPI) sind nicht verwendbar.</p>
  <div class="pin-grid" id="pinview"></div>
  <div id="errinfo" class="mono err" style="margin-top:8px"></div>
</div>

<script>
let cfg = {atem_ip:"", devices:[]};
let lastTally = {};
let lastOutputs = {};
let lastAtem = {};

const SAFE = [4,5,6,12,13,16,17,18,19,20,21,22,23,24,25,26,27];
const ACTIONS = [
  ["none", "— keine —"],
  ["atem_aux", "ATEM Aux setzen"],
  ["companion_press", "Companion-Button"],
];

const esc = s => (s+"").replace(/[&<>"']/g, m => (
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]
));

function pinSelect(value, field, i){
  const used = new Set();
  cfg.devices.forEach((d,j) => {
    if (j === i) return;
    if (d.out_gpio != null) used.add(d.out_gpio);
    if (d.in_gpio  != null) used.add(d.in_gpio);
  });
  let opts = `<option value="">—</option>`;
  for (const b of SAFE) {
    const dis = used.has(b) ? " disabled" : "";
    const sel = (value === b) ? " selected" : "";
    opts += `<option value="${b}"${sel}${dis}>GPIO ${b}</option>`;
  }
  return `<select onchange="cfg.devices[${i}].${field}=this.value?+this.value:null;render()">${opts}</select>`;
}

function renderActionEditor(d, i){
  const a = d.in_action || {type:"none"};
  const opts = ACTIONS.map(([k,l]) =>
    `<option value="${k}"${a.type===k?" selected":""}>${l}</option>`).join("");
  let extra = "";
  if (a.type === "atem_aux") {
    extra = `
      Aux <input type="number" min="1" max="24" value="${a.aux ?? 1}"
                 style="width:60px"
                 onchange="cfg.devices[${i}].in_action.aux=+this.value">
      Src <input type="number" min="0" placeholder="auto"
                 value="${a.source ?? ''}" style="width:60px"
                 onchange="cfg.devices[${i}].in_action.source=this.value?+this.value:null">`;
  } else if (a.type === "companion_press") {
    extra = `
      P <input type="number" min="1" value="${a.page ?? 1}" style="width:50px"
              onchange="cfg.devices[${i}].in_action.page=+this.value">
      R <input type="number" min="0" value="${a.row ?? 0}" style="width:50px"
              onchange="cfg.devices[${i}].in_action.row=+this.value">
      C <input type="number" min="0" value="${a.col ?? 0}" style="width:50px"
              onchange="cfg.devices[${i}].in_action.col=+this.value">`;
  }
  return `<select onchange="setAction(${i}, this.value)">${opts}</select> ${extra}`;
}

function setAction(i, type){
  if (type === "atem_aux") {
    cfg.devices[i].in_action = {type, aux:1, source:null};
  } else if (type === "companion_press") {
    cfg.devices[i].in_action = {type, page:1, row:0, col:0};
  } else {
    cfg.devices[i].in_action = {type:"none"};
  }
  render();
}

function render(){
  const rows = (cfg.devices || []).map((d, i) => {
    if (!d.id) d.id = "cam" + (i+1);
    const st = lastTally[d.id] || "?";
    const url = location.origin + "/tally/" + encodeURIComponent(d.id);
    return `<tr>
      <td><span class="mono">${esc(d.id)}</span></td>
      <td><input value="${esc(d.name || '')}" placeholder="Kamera ${i+1}"
                 onchange="cfg.devices[${i}].name=this.value"></td>
      <td><input type="number" min="0" value="${d.atem_input ?? i+1}"
                 onchange="cfg.devices[${i}].atem_input=+this.value"></td>
      <td class="s-${st}">${st}</td>
      <td>${pinSelect(d.out_gpio, "out_gpio", i)}
          <button class="test" onclick="test('${d.id}')">Test</button></td>
      <td>${pinSelect(d.in_gpio, "in_gpio", i)}</td>
      <td>${renderActionEditor(d, i)}</td>
      <td><a href="${url}" target="_blank">öffnen</a></td>
      <td><button class="danger" onclick="del(${i})">×</button></td>
    </tr>`;
  }).join("");
  document.getElementById("rows").innerHTML = rows;
  renderPins();
}

function renderPins(){
  const grid = document.getElementById("pinview");
  grid.innerHTML = SAFE.map(b => {
    let cls = "pin";
    let lvl = "frei";
    if (b in lastOutputs) {
      cls += lastOutputs[b] ? " out-lo" : " out-hi";
      lvl = lastOutputs[b] ? "LOW (an)" : "HIGH (aus)";
    } else if (cfg.devices.some(d => d.in_gpio === b)) {
      cls += " input";
      lvl = "INPUT";
    }
    return `<div class="${cls}"><span class="b">GPIO ${b}</span>${lvl}</div>`;
  }).join("");
}

function addDev(){
  const n = cfg.devices.length + 1;
  cfg.devices.push({id:"cam"+n, name:"Kamera "+n, atem_input:n, atem_me:1,
                    in_action:{type:"none"}});
  render();
}

function del(i){ cfg.devices.splice(i,1); render(); }

async function test(devId){
  const d = cfg.devices.find(x => x.id === devId);
  if (!d || d.out_gpio == null) return;
  await fetch("/api/test/" + d.out_gpio + "/pulse", {method:"POST"});
}

async function save(){
  cfg.atem_ip = document.getElementById("atem_ip").value.trim();
  const info = document.getElementById("saveinfo");
  info.textContent = "speichere…";
  try {
    const r = await fetch("/api/config",
                          {method:"POST", body:JSON.stringify(cfg)});
    const j = await r.json();
    info.textContent = j.ok ? "✓ gespeichert" : "✗ " + (j.error || "Fehler");
  } catch (e) {
    info.textContent = "✗ " + (e.message || e);
  }
  setTimeout(() => info.textContent = "", 2500);
}

async function refresh(){
  try {
    const r = await fetch("/api/state");
    const j = await r.json();
    cfg = j.config || {atem_ip:"", devices:[]};
    lastTally    = j.tally || {};
    lastOutputs  = j.outputs || {};
    lastAtem     = j.atem || {};

    const ipBox = document.getElementById("atem_ip");
    if (document.activeElement !== ipBox) ipBox.value = cfg.atem_ip || "";

    const stat = document.getElementById("atemstatus");
    const a = lastAtem;
    if (a.connected) {
      stat.textContent = "● ATEM verbunden";
      stat.className = "mono ok";
    } else {
      stat.textContent = "○ ATEM " + (a.error || "offline");
      stat.className = "mono err";
    }
    document.getElementById("atem_info").textContent =
      a.connected ? `PGM=${a.pgm?.[0] ?? "-"}  PVW=${a.pvw?.[0] ?? "-"}` : "";

    document.getElementById("errinfo").textContent = j.error || "";
    render();
  } catch (e) {
    document.getElementById("atemstatus").textContent = "✗ Server nicht erreichbar";
    document.getElementById("atemstatus").className = "mono err";
  }
}

refresh();
setInterval(refresh, 500);
</script>
</body></html>
"""

TALLY_HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Tally __ID__</title>
<style>
html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#000}
#bg{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
    font:bold clamp(2rem,12vw,10rem) system-ui,sans-serif;color:white;
    letter-spacing:0.04em;text-shadow:0 0 12px rgba(0,0,0,0.4);
    transition:background 80ms linear}
.s-pgm{background:#ef4444}
.s-pvw{background:#22c55e}
.s-safe{background:#000;color:#222}
.s-offline{background:#000;color:#fbbf24;font-size:clamp(1rem,4vw,2.5rem)}
</style></head><body>
<div id="bg" class="s-offline">verbinde…</div>
<script>
const id = "__ID__";
async function tick(){
  try {
    const r = await fetch("/api/state", {cache:"no-store"});
    const j = await r.json();
    const st = (j.tally && j.tally[id]) || "unknown";
    const bg = document.getElementById("bg");
    bg.className = "s-" + st;
    bg.textContent =
      st === "pgm"     ? "ON AIR" :
      st === "pvw"     ? "PREVIEW" :
      st === "offline" ? "ATEM offline" :
      st === "safe"    ? "" :
                         id;
  } catch (e) {
    document.getElementById("bg").textContent = "Server nicht erreichbar";
  }
}
tick();
setInterval(tick, 250);
</script></body></html>
"""


def main():
    reconfigure()
    threading.Thread(target=driver_loop, name="driver", daemon=True).start()
    threading.Thread(target=input_loop,  name="input",  daemon=True).start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as srv:
        srv.daemon_threads = True
        log("server", f"listening on :{PORT}")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
