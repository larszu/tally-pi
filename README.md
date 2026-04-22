# TW Broadcast Interface

Raspberry Pi 5 based broadcast control interface built on top of
[Bitfocus Companion](https://bitfocus.io/companion/). Turns a Pi with a
40-pin header, HDMI screen, USB keyboard/mouse and (optionally) an I²C OLED
into a self-contained Companion appliance:

- **GPIO buttons** directly drive Companion actions (press buttons, set
  custom variables) via Companion's HTTP API.
- **HDMI kiosk** auto-launches Chromium with Companion's UI **and** a
  guided setup web page.
- **Setup guide UI** shows live GPIO state, lets you click a pin and bind
  a Companion action to it, with an ATEM Aux example and full Pi 5 pinout.
- **Optional OLED** (SSD1306 128×64 on I²C) cycles IP/hostname/temperature.

## Components

| File | Role |
|------|------|
| `setup-guide.html` | Kiosk setup UI (served on `:8080`). Live GPIO tiles + binding modal + pinout + ATEM tutorial. |
| `guide_server.py` | Python stdlib HTTP server: serves the guide, exposes `/gpio` (live state from `/sys/kernel/debug/gpio`) and `/bindings` (GET/POST JSON). |
| `gpio_watcher.py` | libgpiod v2 watcher. Reads `bindings.json`, claims lines, fires Companion HTTP API calls on edges. Auto-reloads on file change. |
| `pi_status.py` | OLED status cycler (luma.oled). |
| `pi-guide.service` | systemd unit for the web server. |
| `pi-gpio-watcher.service` | systemd unit for the GPIO watcher. |
| `pi-status.service` | systemd unit for the OLED. |
| `10-modesetting.conf` | Xorg fix for Pi 5 (pins `vc4` to modesetting driver). |
| `openbox-autostart` | Openbox autostart → launches Chromium with 3 tabs (guide, Companion, connections). |
| `01-enable-i2c.sh` | Installer: enables I²C, installs tools. |
| `02-install-status.sh` | Installer: OLED service. |
| `03-install-kiosk.sh` | Installer: Xorg + Openbox + Chromium kiosk + tty1 autologin. |
| `04-install-guide.sh` | Installer: pi-guide service. |
| `05-install-watcher.sh` | Installer: pi-gpio-watcher service. |

## Quick start (on the Pi)

```bash
sudo apt update
git clone https://github.com/larszu/tw-broadcast-interface.git
cd tw-broadcast-interface
cp -r * /tmp/pi-setup/   # installers expect files under /tmp/pi-setup

bash 01-enable-i2c.sh
# reboot if asked
bash 02-install-status.sh     # leave disabled until OLED is wired
bash 03-install-kiosk.sh
bash 04-install-guide.sh
bash 05-install-watcher.sh
sudo reboot
```

After reboot the Pi auto-logs in on tty1, starts Xorg + Openbox, and
Chromium opens three tabs:

1. `http://localhost:8080/` — setup guide (click GPIO tiles to bind
   Companion actions)
2. `http://localhost:8000/` — Companion UI
3. `http://localhost:8000/#/connections` — Companion connections page

Exit kiosk with <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>.

## How bindings work

When you click a GPIO tile in the setup guide, the modal writes an entry
to `/opt/pi-guide/bindings.json`. The watcher service detects the change
(via mtime poll), claims the affected GPIO lines through libgpiod, and on
every edge event calls Companion's HTTP API:

- **Press action:** `POST http://localhost:8000/api/location/<page>/<row>/<column>/press`
- **Down/Up action:** `.../down` on falling edge, `.../up` on rising edge
- **Variable action:** `POST http://localhost:8000/api/custom-variable/<name>/value` with the value as body

Example `bindings.json`:

```json
[
  {
    "bcm": 17,
    "trigger_edge": "falling",
    "bias": "pull-up",
    "debounce_ms": 20,
    "enabled": true,
    "action": { "kind": "press", "page": 1, "row": 0, "column": 0 }
  }
]
```

## Hardware

- **Target:** Raspberry Pi 5 (Debian 13 trixie, aarch64). Pi 4 should also work.
- **Pre-requisite:** [Companion Pi](https://github.com/bitfocus/companion-pi)
  image or a standard Raspberry Pi OS with Companion installed at
  `/opt/companion/` and the `companion.service` enabled.
- **GPIO button:** any push-button between a free GPIO pin and GND.
  Internal pull-up is enabled by default.
- **OLED (optional):** SSD1306 128×64 on I²C bus 1, address `0x3C`.

## License

Internal project. Not for redistribution.
