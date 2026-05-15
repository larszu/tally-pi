#!/usr/bin/env python3
"""Watch an ATEM switcher via its native UDP protocol (port 9910).

No external libraries required.  Writes state to /run/pi-guide/atem.json.
Reads ATEM IP from /opt/pi-guide/tally.json (key: "atem_ip").
"""
import json
import os
import queue
import signal
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

STATE_FILE = Path("/run/pi-guide/atem.json")
CONFIG_FILE = Path("/opt/pi-guide/tally.json")
CMD_SOCKET = Path("/run/pi-guide/atem-cmd.sock")
ATEM_PORT = 9910
RECV_TIMEOUT = 2.0          # socket recv timeout (seconds)
KEEPALIVE_INTERVAL = 1.0    # send keepalive ping every N seconds
RECONNECT_DELAY = 5.0
STATE_WRITE_INTERVAL = 0.1  # throttle disk writes

# ATEM packet flags (top 5 bits of the 2-byte flags|length field)
FLAG_RELIABLE = 0x01   # server wants ACK
FLAG_SYN      = 0x02   # handshake
FLAG_ACK      = 0x10   # we're ACKing

# Outbound command queue, drained from the main loop after each tick().
CMD_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=256)


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


def make_hello() -> bytes:
    """Build the 20-byte ATEM connect packet."""
    # flags=SYN (0x02) -> packed as (0x02 << 11) | 20 = 0x1014
    header = struct.pack(">HHHHHH", 0x1014, 0x53AB, 0x0000, 0x0000, 0x003A, 0x0000)
    return header + b"\x01\x00\x00\x00\x00\x00\x00\x00"


def make_ack(session_id: int, remote_pkt_id: int) -> bytes:
    """Build a 12-byte ACK packet."""
    # flags=ACK (0x10) -> (0x10 << 11) | 12 = 0x800C
    return struct.pack(">HHHHHH", 0x800C, session_id, remote_pkt_id, 0x0000, 0x0000, 0x0000)


def make_ping(session_id: int, local_pkt_id: int) -> bytes:
    """Build a 12-byte empty reliable packet (keepalive)."""
    # flags=RELIABLE (0x01) -> (0x01 << 11) | 12 = 0x080C
    return struct.pack(">HHHHHH", 0x080C, session_id, 0x0000, local_pkt_id, 0x0000, 0x0000)


def make_caus(session_id: int, local_pkt_id: int, aux_index_0based: int, source_input: int) -> bytes:
    """Build a 24-byte reliable packet containing one CAuS (set aux source) command."""
    # 12-byte header (RELIABLE) + 12-byte command block.
    # flags=RELIABLE (0x01) -> (0x01 << 11) | 24 = 0x0818
    header = struct.pack(">HHHHHH", 0x0818, session_id, 0x0000, local_pkt_id, 0x0000, 0x0000)
    # cmd_len=12, pad=0, name="CAuS", mask=0x01 (source), aux_idx, source (BE)
    cmd = struct.pack(">HH4sBBH", 12, 0x0000, b"CAuS",
                      0x01, aux_index_0based & 0xFF, source_input & 0xFFFF)
    return header + cmd


def make_cpgi(session_id: int, local_pkt_id: int, me_index_0based: int, source_input: int) -> bytes:
    """Build a reliable packet containing one CPgI (Change Program Input) command."""
    header = struct.pack(">HHHHHH", 0x0818, session_id, 0x0000, local_pkt_id, 0x0000, 0x0000)
    # cmd_len=12, pad=0, name="CPgI", me_index(0-based), pad, source (BE)
    cmd = struct.pack(">HH4sBBH", 12, 0x0000, b"CPgI",
                      me_index_0based & 0xFF, 0x00, source_input & 0xFFFF)
    return header + cmd


def make_cpvi(session_id: int, local_pkt_id: int, me_index_0based: int, source_input: int) -> bytes:
    """Build a reliable packet containing one CPvI (Change Preview Input) command."""
    header = struct.pack(">HHHHHH", 0x0818, session_id, 0x0000, local_pkt_id, 0x0000, 0x0000)
    cmd = struct.pack(">HH4sBBH", 12, 0x0000, b"CPvI",
                      me_index_0based & 0xFF, 0x00, source_input & 0xFFFF)
    return header + cmd


def parse_commands(data: bytes):
    """Yield (name, raw_data) tuples from the command blocks in a data packet."""
    offset = 12
    while offset + 8 <= len(data):
        cmd_len = struct.unpack_from(">H", data, offset)[0]
        if cmd_len < 8 or offset + cmd_len > len(data):
            break
        name = data[offset + 4: offset + 8].decode("ascii", errors="ignore")
        payload = data[offset + 8: offset + cmd_len]
        yield name, payload
        offset += cmd_len


class AtemClient:
    """Minimal ATEM UDP client — connects, parses state, sends ACKs."""

    def __init__(self, ip: str):
        self.ip = ip
        self.sock = None
        self.session_id = 0
        self.local_pkt_id = 0
        self.connected = False
        self.last_ping = 0.0
        self.state = {
            "connected": False,
            "pgm": {},   # ME (0-based as str) -> input number
            "pvw": {},
            "aux": {},   # aux (1-based as str) -> input number
            "inputs": {},
            "error": "",
            "timestamp": time.time(),
        }

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(RECV_TIMEOUT)
        self.connected = False
        self.local_pkt_id = 0
        self.state["connected"] = False
        self.state["error"] = "connecting..."

        self.sock.sendto(make_hello(), (self.ip, ATEM_PORT))

        # Wait for SYN from server (up to 5 seconds)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            if len(data) < 12:
                continue
            flags_len = struct.unpack_from(">H", data, 0)[0]
            flags = (flags_len >> 11) & 0x1F
            if flags & FLAG_SYN:
                self.session_id = struct.unpack_from(">H", data, 2)[0]
                # For SYN packets, the remote packet id is at offset 10
                remote_pkt_id = struct.unpack_from(">H", data, 10)[0]
                self.sock.sendto(make_ack(self.session_id, remote_pkt_id), (self.ip, ATEM_PORT))
                self.connected = True
                self.last_ping = time.time()
                return
        raise ConnectionError(f"ATEM at {self.ip} did not respond to HELLO")

    def _handle_packet(self, data: bytes):
        if len(data) < 12:
            return
        flags_len = struct.unpack_from(">H", data, 0)[0]
        flags = (flags_len >> 11) & 0x1F
        # Remote (server) packet id is at offset 10, not 6
        remote_pkt_id = struct.unpack_from(">H", data, 10)[0]

        # Update session id if the server sent us one
        srv_session = struct.unpack_from(">H", data, 2)[0]
        if srv_session and srv_session != 0x53AB:
            self.session_id = srv_session

        # ACK reliable packets (data packets from ATEM carry state updates)
        if flags & FLAG_RELIABLE:
            self.sock.sendto(make_ack(self.session_id, remote_pkt_id), (self.ip, ATEM_PORT))

        # Parse command blocks
        for name, payload in parse_commands(data):
            self._apply_command(name, payload)

    def _apply_command(self, name: str, data: bytes):
        try:
            if name == "PrgI" and len(data) >= 4:
                me = data[0]
                src = struct.unpack_from(">H", data, 2)[0]
                self.state["pgm"][str(me)] = src

            elif name == "PrvI" and len(data) >= 4:
                me = data[0]
                src = struct.unpack_from(">H", data, 2)[0]
                self.state["pvw"][str(me)] = src

            elif name == "AuxS" and len(data) >= 4:
                aux = data[0]
                src = struct.unpack_from(">H", data, 2)[0]
                self.state["aux"][str(aux + 1)] = src  # 1-indexed

            elif name == "InPr" and len(data) >= 26:
                # 2-byte input id, 20-byte long name (null-terminated), 4-byte short name
                inp = struct.unpack_from(">H", data, 0)[0]
                long_name = data[2:22].split(b"\x00")[0].decode("utf-8", errors="replace")
                short_name = data[22:26].split(b"\x00")[0].decode("utf-8", errors="replace")
                self.state["inputs"][str(inp)] = {"long": long_name, "short": short_name}

            elif name in ("_ver", "pin "):
                # These appear at end of initial state dump
                self.state["connected"] = True
                self.state["error"] = ""
        except Exception:
            pass

    def tick(self) -> bool:
        """Receive and handle one batch of packets. Returns False if error."""
        if not self.sock:
            return False

        try:
            data, _ = self.sock.recvfrom(65536)
            self._handle_packet(data)
            self.state["connected"] = True
            self.state["error"] = ""
        except socket.timeout:
            pass  # normal – just no data this tick
        except Exception as e:
            self.state["connected"] = False
            self.state["error"] = f"recv error: {e}"
            return False

        # Keepalive ping
        now = time.time()
        if now - self.last_ping >= KEEPALIVE_INTERVAL:
            try:
                self.local_pkt_id = (self.local_pkt_id + 1) & 0x7FFF
                self.sock.sendto(make_ping(self.session_id, self.local_pkt_id), (self.ip, ATEM_PORT))
                self.last_ping = now
            except Exception:
                pass

        return True

    def send_aux(self, aux_1based: int, source: int) -> None:
        """Tell the ATEM: route `source` to Aux output `aux_1based`."""
        if not self.sock or not self.connected:
            raise RuntimeError("not connected")
        if not (1 <= aux_1based <= 24):
            raise ValueError(f"aux out of range: {aux_1based}")
        self.local_pkt_id = (self.local_pkt_id + 1) & 0x7FFF
        pkt = make_caus(self.session_id, self.local_pkt_id, aux_1based - 1, int(source))
        self.sock.sendto(pkt, (self.ip, ATEM_PORT))

    def send_program(self, me_1based: int, source: int) -> None:
        """Tell the ATEM: set the Program bus on `me_1based` to `source`."""
        if not self.sock or not self.connected:
            raise RuntimeError("not connected")
        if not (1 <= me_1based <= 4):
            raise ValueError(f"me out of range: {me_1based}")
        self.local_pkt_id = (self.local_pkt_id + 1) & 0x7FFF
        pkt = make_cpgi(self.session_id, self.local_pkt_id, me_1based - 1, int(source))
        self.sock.sendto(pkt, (self.ip, ATEM_PORT))

    def send_preview(self, me_1based: int, source: int) -> None:
        """Tell the ATEM: set the Preview bus on `me_1based` to `source`."""
        if not self.sock or not self.connected:
            raise RuntimeError("not connected")
        if not (1 <= me_1based <= 4):
            raise ValueError(f"me out of range: {me_1based}")
        self.local_pkt_id = (self.local_pkt_id + 1) & 0x7FFF
        pkt = make_cpvi(self.session_id, self.local_pkt_id, me_1based - 1, int(source))
        self.sock.sendto(pkt, (self.ip, ATEM_PORT))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.connected = False


def cmd_listener_thread():
    """Listen on a Unix socket for JSON commands and enqueue them.

    Wire format: one JSON object per connection, terminated by newline.
    Examples:
        {"cmd": "set_aux", "aux": 1, "source": 5}

    The response is a single JSON line: {"ok": bool, "error": "..."}.
    """
    try:
        CMD_SOCKET.parent.mkdir(parents=True, exist_ok=True)
        try:
            CMD_SOCKET.unlink()
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(CMD_SOCKET))
        try:
            os.chmod(str(CMD_SOCKET), 0o660)
        except Exception:
            pass
        srv.listen(8)
    except Exception as e:
        print(f"atem cmd listener bind failed: {e}", flush=True)
        return

    while True:
        try:
            conn, _ = srv.accept()
        except Exception:
            time.sleep(0.5)
            continue
        try:
            conn.settimeout(2.0)
            buf = b""
            while b"\n" not in buf and len(buf) < 8192:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            try:
                req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("expected JSON object")
            except Exception as e:
                conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
                continue
            try:
                CMD_QUEUE.put_nowait(req)
                conn.sendall((json.dumps({"ok": True, "queued": True}) + "\n").encode())
            except queue.Full:
                conn.sendall((json.dumps({"ok": False, "error": "queue full"}) + "\n").encode())
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def drain_commands(client):
    """Pop pending commands and send them through the open ATEM connection."""
    while True:
        try:
            req = CMD_QUEUE.get_nowait()
        except queue.Empty:
            return
        cmd = req.get("cmd")
        try:
            if cmd == "set_aux":
                client.send_aux(int(req["aux"]), int(req["source"]))
                print(f"atem set_aux {req['aux']} <- {req['source']}", flush=True)
            elif cmd == "set_program":
                client.send_program(int(req.get("me", 1)), int(req["source"]))
                print(f"atem set_program ME{req.get('me', 1)} <- {req['source']}", flush=True)
            elif cmd == "set_preview":
                client.send_preview(int(req.get("me", 1)), int(req["source"]))
                print(f"atem set_preview ME{req.get('me', 1)} <- {req['source']}", flush=True)
            else:
                print(f"atem cmd unknown: {cmd!r}", flush=True)
        except Exception as e:
            print(f"atem cmd {cmd} failed: {e}", flush=True)




def run():
    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    threading.Thread(target=cmd_listener_thread, name="atem-cmd",
                     daemon=True).start()

    last_ip = None
    client = None
    last_write = 0.0

    def flush(state: dict, force=False):
        nonlocal last_write
        now = time.time()
        if not force and (now - last_write) < STATE_WRITE_INTERVAL:
            return
        state["timestamp"] = now
        atomic_write(STATE_FILE, state)
        last_write = now

    def write_disconnected(reason: str):
        atomic_write(STATE_FILE, {
            "connected": False,
            "pgm": {}, "pvw": {}, "aux": {}, "inputs": {},
            "error": reason,
            "timestamp": time.time(),
        })

    while not stop["flag"]:
        cfg = load_config()
        ip = cfg.get("atem_ip", "").strip()

        if not ip:
            write_disconnected("no ATEM IP configured (set atem_ip in tally.json)")
            time.sleep(2)
            continue

        if client is None or ip != last_ip:
            if client:
                client.close()
            client = AtemClient(ip)
            last_ip = ip

        if not client.connected:
            try:
                client.connect()
            except Exception as e:
                write_disconnected(f"connect failed: {e}")
                time.sleep(RECONNECT_DELAY)
                continue

        ok = client.tick()
        drain_commands(client)
        flush(client.state)

        if not ok:
            client.close()
            client = None
            time.sleep(RECONNECT_DELAY)

    if client:
        client.close()
    write_disconnected("watcher stopped")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        atomic_write(STATE_FILE, {
            "connected": False,
            "pgm": {}, "pvw": {}, "aux": {}, "inputs": {},
            "error": f"crash: {e}",
            "timestamp": time.time(),
        })
        raise

