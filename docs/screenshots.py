#!/usr/bin/env python3
"""Regenerate the README screenshots headlessly — no Pi, no ATEM, no GPIO.

Starts the real guide_server.py against a throwaway config (three demo
devices plus a stubbed ATEM state file), drives it with headless Chromium
and writes the PNGs into docs/img/.

    pip install playwright && playwright install chromium
    python3 docs/screenshots.py

Set CHROME_PATH=/path/to/chrome to use a Chromium that Playwright did not
install itself.

The host needs no GPIO hardware: guide_server degrades gracefully, which is
why the diagnostics shot shows empty SW/HW columns and a "gpiod not
available" note. That is the honest output for a machine without a 40-pin
header, not a broken build.

Note that guide_server reads its config from the absolute paths
/opt/pi-guide/{tally,bindings}.json and /run/pi-guide/atem.json, so this
script writes there. It refuses to clobber an existing /opt/pi-guide —
never run it on a Pi that is actually in service.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
STATE_DIR = Path("/run/pi-guide")
CONF_DIR = Path("/opt/pi-guide")
PORT = 8080
BASE = f"http://127.0.0.1:{PORT}"

DEMO_DEVICES = {
    "atem_ip": "192.168.10.240",
    "devices": [
        {"id": "cam1", "name": "Kamera 1", "input": 1, "me": 1, "aux": [],
         "out_gpio": 17, "out_trigger": "pgm", "out_active_high": False,
         "in_gpio": 27, "in_edge": "falling", "in_debounce_ms": 20,
         "in_hold_release_ms": 5000, "in_burst_min_edges": 100,
         "in_action_type": "atem_aux", "in_atem_aux": 1,
         "in_atem_source": 5, "in_atem_source_release": 10010,
         "in_companion_mode": "tap"},
        {"id": "cam2", "name": "Kamera 2", "input": 2, "me": 1, "aux": [],
         "out_gpio": 22, "out_trigger": "pgm", "out_active_high": False},
        {"id": "cam3", "name": "Kamera 3 (Handheld)", "input": 3, "me": 1, "aux": [1],
         "out_gpio": 23, "out_trigger": "pgm_pvw", "out_active_high": True,
         "in_gpio": 24, "in_edge": "falling", "in_debounce_ms": 20,
         "in_action_type": "atem_pgm", "in_atem_source": 3},
    ],
}

DEMO_ATEM = {
    "connected": True,
    "pgm": {"0": 1}, "pvw": {"0": 2}, "aux": {"1": 5},
    "inputs": {"1": "Kamera 1", "2": "Kamera 2", "3": "Kamera 3",
               "4": "Laptop", "5": "Playback", "10010": "MP 1"},
    "error": "", "timestamp": time.time(),
}


def seed():
    if CONF_DIR.exists() and (CONF_DIR / "tally.json").exists():
        existing = json.loads((CONF_DIR / "tally.json").read_text() or "{}")
        if existing.get("devices") and existing != DEMO_DEVICES:
            sys.exit(f"{CONF_DIR}/tally.json holds a real config — refusing to overwrite it.")
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (CONF_DIR / "tally.json").write_text(json.dumps(DEMO_DEVICES, indent=2))
    (CONF_DIR / "bindings.json").write_text("[]")
    (STATE_DIR / "atem.json").write_text(json.dumps(DEMO_ATEM))


def page_clip(pg, selector, index=0, pad=10):
    """Bounding box of an element in *page* coordinates.

    Element.screenshot() scrolls the element under the sticky topbar and
    clips it, so full-page screenshot + clip is used instead.
    """
    return pg.evaluate(
        """([sel, i, pad]) => {
            window.scrollTo(0, 0);
            const r = document.querySelectorAll(sel)[i].getBoundingClientRect();
            return {x: Math.max(0, r.left - pad),
                    y: Math.max(0, r.top + window.scrollY - pad),
                    width: r.width + 2 * pad, height: r.height + 2 * pad};
        }""", [selector, index, pad])


def shoot():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # CHROME_PATH lets you point at a Chromium that Playwright did not
        # install itself (e.g. a distro build, or a preinstalled sandbox one).
        chrome = os.environ.get("CHROME_PATH")
        b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        errors = []

        # Overview: whole Tally tab, devices collapsed. 1x — it is a tall page.
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "/", wait_until="load")
        pg.wait_for_timeout(2500)
        pg.evaluate("window.showView && window.showView('tally')")
        pg.wait_for_timeout(1000)
        pg.evaluate("window.scrollTo(0, 0)")
        pg.screenshot(path=str(OUT / "setup-ui.png"), full_page=True)

        # Details at 2x, devices expanded.
        pg2 = b.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        pg2.on("pageerror", lambda e: errors.append(str(e)))
        pg2.goto(BASE + "/", wait_until="load")
        pg2.wait_for_timeout(2500)
        pg2.evaluate("window.showView && window.showView('tally')")
        pg2.wait_for_timeout(800)
        pg2.get_by_role("button", name="Alle auf").click()
        pg2.wait_for_timeout(1500)
        for selector, name in ((".tally-card", "device-card"), (".cols", "diagnostics")):
            pg2.screenshot(path=str(OUT / f"{name}.png"), full_page=True,
                           clip=page_clip(pg2, selector))

        # Browser tally on a phone-sized viewport: cam1 is PGM, cam2 is PVW.
        for dev, name in (("cam1", "browser-tally-pgm"), ("cam2", "browser-tally-pvw")):
            ph = b.new_page(viewport={"width": 390, "height": 720}, device_scale_factor=2)
            ph.goto(f"{BASE}/tally/{dev}", wait_until="load")
            ph.wait_for_timeout(2500)
            ph.screenshot(path=str(OUT / f"{name}.png"))
            ph.close()

        b.close()
        if errors:
            sys.exit("JS errors on the page: " + "; ".join(errors[:5]))


def main():
    seed()
    server = subprocess.Popen([sys.executable, str(ROOT / "guide_server.py")],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                import urllib.request
                urllib.request.urlopen(BASE + "/", timeout=1).read(1)
                break
            except Exception:
                if server.poll() is not None:
                    sys.exit("guide_server exited: " + server.stdout.read().decode())
        else:
            sys.exit(f"guide_server did not answer on {BASE}")
        shoot()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print("wrote", ", ".join(sorted(f.name for f in OUT.glob("*.png"))))


if __name__ == "__main__":
    if shutil.which("python3") is None:
        sys.exit("python3 required")
    main()
