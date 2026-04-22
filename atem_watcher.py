#!/usr/bin/env python3
"""Watch an ATEM switcher and write program/preview state to /run/pi-guide/atem.json.

Reads config from /opt/pi-guide/tally.json (key: atem_ip).
Requires the `pyatem` pip package. If not installed, writes a stub file with
{"connected": false, "error": "pyatem not installed"} and sleeps.
"""
import json
import os
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

STATE_FILE = Path("/run/pi-guide/atem.json")
CONFIG_FILE = Path("/opt/pi-guide/tally.json")
RECONNECT_DELAY = 5.0
STATE_WRITE_INTERVAL = 0.25  # throttle disk writes


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".atem.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o664)
        except Exception:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        d = json.loads(CONFIG_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_disconnected(reason: str) -> None:
    atomic_write(STATE_FILE, {
        "connected": False,
        "error": reason,
        "timestamp": time.time(),
    })


def try_import_pyatem():
    try:
        from pyatem.protocol import AtemProtocol  # type: ignore
        return AtemProtocol
    except Exception as e:
        return None


def run():
    # Graceful shutdown
    stop = {"flag": False}

    def _sig(*_a):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    AtemProtocol = try_import_pyatem()
    if AtemProtocol is None:
        write_disconnected("pyatem not installed (pip install pyatem)")
        # idle until told to stop; operator must install package & restart
        while not stop["flag"]:
            time.sleep(2)
        return

    last_ip = None
    mixer = None
    last_write = 0.0
    state = {
        "connected": False,
        "pgm": [],
        "pvw": [],
        "aux": {},
        "inputs": {},
        "timestamp": time.time(),
    }

    def flush(force=False):
        nonlocal last_write
        now = time.time()
        if not force and (now - last_write) < STATE_WRITE_INTERVAL:
            return
        state["timestamp"] = now
        atomic_write(STATE_FILE, state)
        last_write = now

    while not stop["flag"]:
        cfg = load_config()
        ip = cfg.get("atem_ip") or ""
        if not ip:
            state["connected"] = False
            state["error"] = "no ATEM IP configured"
            flush(force=True)
            time.sleep(2)
            continue

        if mixer is None or ip != last_ip:
            if mixer is not None:
                try:
                    mixer.disconnect()
                except Exception:
                    pass
                mixer = None
            try:
                mixer = AtemProtocol(ip)
                mixer.connect()
                last_ip = ip
                state["error"] = ""
            except Exception as e:
                state["connected"] = False
                state["error"] = f"connect failed: {e}"
                flush(force=True)
                time.sleep(RECONNECT_DELAY)
                continue

        # Poll mixer. pyatem is callback-based: we loop once per short tick.
        try:
            mixer.loop()
        except Exception as e:
            state["connected"] = False
            state["error"] = f"loop error: {e}"
            flush(force=True)
            try:
                mixer.disconnect()
            except Exception:
                pass
            mixer = None
            time.sleep(RECONNECT_DELAY)
            continue

        # Extract PGM/PVW for ME 1 and aux outputs
        try:
            mix = mixer.mixerstate
            pgm = mix.get("program-bus-input")
            pvw = mix.get("preview-bus-input")
            aux_state = mix.get("aux-output-source") or {}
            inputs = mix.get("input-properties") or {}

            pgm_inputs = []
            if pgm is not None:
                for idx, entry in pgm.items() if isinstance(pgm, dict) else []:
                    try:
                        pgm_inputs.append({"me": int(idx), "input": int(getattr(entry, "source", entry))})
                    except Exception:
                        pass

            pvw_inputs = []
            if pvw is not None:
                for idx, entry in pvw.items() if isinstance(pvw, dict) else []:
                    try:
                        pvw_inputs.append({"me": int(idx), "input": int(getattr(entry, "source", entry))})
                    except Exception:
                        pass

            aux = {}
            for idx, entry in (aux_state.items() if isinstance(aux_state, dict) else []):
                try:
                    aux[str(int(idx))] = int(getattr(entry, "source", entry))
                except Exception:
                    pass

            input_map = {}
            for idx, entry in (inputs.items() if isinstance(inputs, dict) else []):
                try:
                    long_name = getattr(entry, "long_name", None) or getattr(entry, "name", None) or ""
                    short_name = getattr(entry, "short_name", None) or ""
                    input_map[str(int(idx))] = {"long": str(long_name), "short": str(short_name)}
                except Exception:
                    pass

            state["connected"] = True
            state["error"] = ""
            state["pgm"] = pgm_inputs
            state["pvw"] = pvw_inputs
            state["aux"] = aux
            state["inputs"] = input_map
            flush()
        except Exception as e:
            state["error"] = f"parse error: {e}"
            flush()

        time.sleep(0.05)

    write_disconnected("watcher stopped")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        write_disconnected(f"crash: {e}")
        raise
