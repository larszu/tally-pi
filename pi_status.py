#!/usr/bin/env python3
"""Companion Pi status display on SSD1306 128x64 I2C OLED.
Cycles pages: hostname/IP, URLs, stats.
"""
import socket
import subprocess
import time
from pathlib import Path

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont


def iface_ip(name: str) -> str:
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in out.splitlines():
            parts = line.split()
            if "inet" in parts:
                i = parts.index("inet")
                return parts[i + 1].split("/")[0]
    except Exception:
        pass
    return "-"


def cpu_temp() -> str:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return f"{int(p.read_text().strip()) / 1000:.1f}C"
    except Exception:
        return "-"


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
    device = ssd1306(serial, width=128, height=64)
    try:
        font_big = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 12
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 10
        )
    except Exception:
        font_big = ImageFont.load_default()
        font_small = font_big

    while True:
        eth = iface_ip("eth0")
        wlan = iface_ip("wlan0")
        host = hostname()
        temp = cpu_temp()
        up = uptime()
        pages = [
            [
                (host, font_big),
                (f"eth0 {eth}", font_small),
                (f"wlan0 {wlan}", font_small),
                (f"temp {temp}  up {up}", font_small),
            ],
            [
                ("Companion UI", font_big),
                (f"http://{wlan if wlan != '-' else eth}:8000", font_small),
                ("", font_small),
                (f"{host}.local:8000", font_small),
            ],
        ]
        for page in pages:
            with canvas(device) as draw:
                y = 0
                for text, font in page:
                    draw.text((0, y), text, font=font, fill=255)
                    bbox = font.getbbox(text or "Ag")
                    y += (bbox[3] - bbox[1]) + 3
            time.sleep(5)


if __name__ == "__main__":
    main()
