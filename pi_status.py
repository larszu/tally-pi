#!/usr/bin/env python3
"""Companion Pi status display on SSD1306 128x32 I2C OLED."""
import pwd
import socket
import subprocess
import time
from pathlib import Path

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont


def current_ip() -> str:
    for iface in ("eth0", "wlan0"):
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr", "show", "dev", iface],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                parts = line.split()
                if "inet" in parts:
                    idx = parts.index("inet")
                    return parts[idx + 1].split("/")[0]
        except Exception:
            pass
    return "-"


def cpu_temp() -> str:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return f"{int(p.read_text().strip()) / 1000:.1f}C"
    except Exception:
        return "-"


def ssh_user() -> str:
    try:
        for p in pwd.getpwall():
            if p.pw_uid >= 1000 and p.pw_shell not in ("/usr/sbin/nologin", "/bin/false"):
                return p.pw_name
    except Exception:
        pass
    return "pi"


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "pi"


def uptime() -> str:
    try:
        s = float(Path("/proc/uptime").read_text().split()[0])
        d, s = divmod(int(s), 86400)
        h, s = divmod(s, 3600)
        m, _ = divmod(s, 60)
        if d:
            return f"{d}d {h}h"
        if h:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "-"


def main() -> None:
    serial = i2c(port=1, address=0x3C)
    device = ssd1306(serial, width=128, height=32)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 8
        )
        font_bold = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 11
        )
    except Exception:
        font = ImageFont.load_default()
        font_bold = font

    Y_POSITIONS = [0, 13, 23]

    while True:
        ip = current_ip()
        temp = cpu_temp()
        up = uptime()
        host = hostname()
        user = ssh_user()

        pages = [
            [
                ("Admin UI", font_bold),
                (f"{ip}:8080/", font),
                ("", font),
            ],
            [
                ("Companion UI", font_bold),
                (f"{ip}:8000/", font),
                ("", font),
            ],
            [
                ("CPU / System", font_bold),
                (f"Temp: {temp}", font),
                (f"up: {up}", font),
            ],
            # [
            #     ("SSH Zugang", font_bold),
            #     (f"ssh {user}@{ip}", font),
            #     ("Port: 22", font),
            # ],
        ]

        for page in pages:
            with canvas(device) as draw:
                for (text, f), y in zip(page, Y_POSITIONS):
                    draw.text((0, y), text, font=f, fill=255)
            time.sleep(5)


if __name__ == "__main__":
    main()
