#!/usr/bin/env python3
"""Companion Pi status display on SSD1306 128x32 I2C OLED."""
import json
import re
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


def gpio_states() -> list:
    try:
        bindings = json.loads(Path("/opt/pi-guide/bindings.json").read_text())
        bcm_list = sorted(b["bcm"] for b in bindings if b.get("enabled", True) and "bcm" in b)
    except Exception:
        return []
    if not bcm_list:
        return []
    states = {}
    try:
        text = Path("/sys/kernel/debug/gpio").read_text(errors="replace")
        for line in text.splitlines():
            m = re.search(r"GPIO(\d+)[^)]*\)\s+(?:in|out)\s+(hi|lo)", line)
            if m:
                states[int(m.group(1))] = "H" if m.group(2) == "hi" else "L"
    except Exception:
        pass
    return [(bcm, states.get(bcm, "?")) for bcm in bcm_list]


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

    while True:
        ip = current_ip()
        temp = cpu_temp()
        up = uptime()
        gpio = gpio_states()

        gpio_tokens = [f"{bcm}:{v}" for bcm, v in gpio]
        mid = (len(gpio_tokens) + 1) // 2
        gpio_row1 = " ".join(gpio_tokens[:mid]) if gpio_tokens else "keine Bindings"
        gpio_row2 = " ".join(gpio_tokens[mid:])

        # Each page: list of (text, font, y_px)
        pages = [
            [
                ("Admin UI",    font_bold, 0),
                (f"{ip}:8080/", font,      13),
                ("",            font,      23),
            ],
            [
                ("Companion UI", font_bold, 0),
                (f"{ip}:8000/",  font,      13),
                ("",             font,      23),
            ],
            [
                ("CPU / System",  font_bold, 0),
                (f"Temp: {temp}", font,      13),
                (f"up: {up}",     font,      23),
            ],
            [
                ("GPIO Status", font_bold, 0),
                (gpio_row1,     font,      13),
                (gpio_row2,     font,      23),
            ],
            # XLR 3-pin breakout wiring reference
            [
                ("PIN 1: GPI Return", font, 0),
                ("PIN 2: GND",        font, 13),
                ("PIN 3: GPO Tally",  font, 23),
            ],
            # [
            #     ("SSH Zugang", font_bold),
            #     (f"ssh {user}@{ip}", font),
            #     ("Port: 22", font),
            # ],
        ]

        for page in pages:
            with canvas(device) as draw:
                for text, f, y in page:
                    draw.text((0, y), text, font=f, fill=255)
            time.sleep(5)


if __name__ == "__main__":
    main()
