# TW Broadcast Interface

Raspberry Pi 5 based broadcast control appliance built on top of
[Bitfocus Companion](https://bitfocus.io/companion/). Turns a Pi with a
40-pin header, HDMI screen, USB keyboard/mouse and (optionally) an I²C OLED
into a self-contained ATEM/Companion controller.

## What it does

Each device (camera / source) configured in the setup-guide UI can carry
**three independent functions**, freely combinable per GPIO pin:

1. **📱 Browser-Tally** — QR-code URL that any smartphone/tablet can open;
   the page paints itself red (PGM) / green (PVW) / black (safe) from
   live ATEM state.
2. **💡 Tally-Lamp (GPIO output)** — the Pi watches the configured ATEM
   input and pulls a chosen GPIO pin to GND when the source is live.
   Open-drain: idle = floating (like an open contact), active = shorted
   to GND. Wire it to a lamp, an optocoupler, or a camera’s tally input.
3. **🔘 Trigger button (GPIO input)** — external short-to-GND on a chosen
   GPIO pin fires one of two actions:
   - **ATEM direct:** send an Aux-source change straight to the switcher
     (no Companion needed).
   - **Companion:** press a Companion button at a chosen page/row/column
     via Companion’s HTTP API.

## Quick install — fresh Pi

Prerequisite: any Raspberry Pi 5 (Pi 4 should also work) running either
the [Companion Pi image](https://github.com/bitfocus/companion-pi) **or**
plain Raspberry Pi OS with Companion installed at `/opt/companion/`.
A non-root user named `talentwerk` must exist (Companion Pi default).

One-shot install — pipe the bootstrap script through `bash`:

```bash
curl -fsSL https://raw.githubusercontent.com/larszu/tw-broadcast-interface/main/bootstrap.sh \
  | sudo bash
```

…or clone first if you want to inspect it:

```bash
git clone https://github.com/larszu/tw-broadcast-interface.git
cd tw-broadcast-interface
sudo bash bootstrap.sh
```

The installer:

1. Installs apt packages (X.org, Chromium, libgpiod, etc.).
2. Clones / updates this repo to `/opt/tw-broadcast-interface`.
3. Enables I²C and a few HDMI safety flags in `/boot/firmware/config.txt`.
4. Deploys all Python services to `/opt/pi-guide` and `/opt/pi-status`.
5. Installs systemd units + udev rules.
6. Sets up tty1 autologin and Chromium kiosk via Openbox.
7. Enables and starts the watcher services.

Reboot **once** after the first install so the I²C kernel parameter
takes effect:

```bash
sudo reboot
```

Subsequent runs of `bootstrap.sh` are idempotent — they pull `main`,
redeploy, and bounce the services. No reboot required.

## After install

Three Chromium tabs open automatically on the connected HDMI screen:

- `http://<pi>:8080/` — **Setup Guide** (Status · Tally · Hilfe)
- `http://<pi>:8000/` — Companion UI
- `http://<pi>:8000/#/connections`

Exit kiosk with <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Q</kbd>.

### Setting up tally

1. Open the **Tally** tab in the setup guide.
2. Enter your ATEM IP → *Übernehmen*. The status indicator turns green.
3. Click **+ Gerät** to add a camera/source. Set name + ATEM-Input + ME.
4. Expand the blocks you need:
   - **📱 Browser-Tally** is always available; the QR-code already works.
     Optionally add Aux outputs that should also count as PGM.
   - **💡 Tally-Lampe** — pick a free GPIO pin and an *Auslöser* (PGM /
     PGM+PVW / manual). The pin follows the ATEM state automatically.
     *Test ⚡* pulses the pin for 500 ms.
   - **🔘 Trigger-Taster** — pick a GPIO pin and a *Flanke*. Then choose
     an *Aktion*:
     - **ATEM direkt: Aux setzen** — pick Aux output number; source
       defaults to this device’s ATEM input but can be overridden.
     - **Companion-Button drücken** — pick Page / Row / Column.
5. Hit **Speichern**.

### Wiring notes

- The output pin is **open-drain**: idle = high-impedance (open contact),
  active = pulled to GND (closed contact). You can put up to ~5 V on it
  (cap at the chip’s pad tolerance) and let your lamp / optocoupler /
  camera tally input pull-up itself.
- Output pin sink current: ≈16 mA max. For anything heavier than an LED
  with current-limiting resistor, drive a MOSFET / optocoupler.
- The input pin uses an internal pull-up; wire your push-button between
  the GPIO and GND. Short = falling edge = action fires (default).
- The setup-guide’s validation blocks I²C/UART/SPI/EEPROM-reserved pins
  and rejects collisions where two devices want the same GPIO.

## Components

| File | Role |
|---|---|
| `setup-guide.html` | Single-page Setup-Guide UI (served on `:8080`) — Status, Tally, Hilfe. |
| `guide_server.py` | Python-stdlib HTTP server. Endpoints: `/ipconfig` `/gpio` `/numato` `/atem` `/bindings` `/tally-config` `/tally-out/<bcm>/(on\|off\|pulse)` `/service/pi-gpio-watcher` `/logs` + `/logs/stream` (SSE) + `/tally/{state,stream,<id>}`. Owns GPIO output pins via libgpiod in open-drain. |
| `gpio_watcher.py` | libgpiod input watcher. Merges `tally.json` devices (with `in_gpio` set) + legacy `bindings.json`. Fires Companion HTTP API calls or sends an ATEM CAuS via the atem-watcher socket. |
| `atem_watcher.py` | Hand-rolled ATEM UDP client (no `pyatem` dep). Writes state to `/run/pi-guide/atem.json` and listens on `/run/pi-guide/atem-cmd.sock` for JSON command lines like `{"cmd":"set_aux","aux":1,"source":5}`. |
| `numato_watcher.py` | Hot-plug watcher for Numato 32-CH USB GPIO modules via the udev-stable `/dev/numato0` symlink. |
| `pi_status.py` | OLED status cycler (luma.oled). Disabled by default; enable once an OLED is connected. |
| `pi-*.service` | systemd units for the four watchers + the guide server + pi-status. |
| `99-numato.rules` | udev rule → stable `/dev/numato0` for any Numato board. |
| `10-modesetting.conf` | Xorg fix for Pi 5 (pins `vc4` to modesetting driver). |
| `openbox-autostart` | Openbox autostart → Chromium with the three tabs. |
| `bootstrap.sh` | One-shot installer. |

## Configuration files

`/opt/pi-guide/tally.json` — owned by the setup-guide UI:

```json
{
  "atem_ip": "192.168.10.240",
  "devices": [
    {
      "id": "cam1",
      "name": "Kamera 1",
      "input": 1,
      "me": 1,
      "aux": [],

      "out_gpio": 17,
      "out_trigger": "pgm",

      "in_gpio": 27,
      "in_edge": "falling",
      "in_action_type": "atem_aux",
      "in_atem_aux": 1,
      "in_atem_source": null
    }
  ]
}
```

`/opt/pi-guide/bindings.json` — **legacy** GPIO-input bindings (still
read by `gpio_watcher.py` for back-compat). Prefer configuring inputs
through the Tally tab instead.

## Services

| Unit | Purpose | Default state |
|---|---|---|
| `pi-guide.service` | Setup-Guide HTTP server + owns GPIO outputs. | enabled |
| `pi-gpio-watcher.service` | Watches input GPIOs, fires actions. | enabled |
| `pi-atem-watcher.service` | ATEM state reader + Aux command sender. | enabled |
| `pi-numato-watcher.service` | Numato USB module poller. | enabled |
| `pi-status.service` | OLED status display. | disabled (enable manually) |
| `getty@tty1` | Auto-login + kiosk start. | enabled |

## Hardware

- **Target:** Raspberry Pi 5 (Debian 13 trixie, aarch64). Pi 4 supported.
- **GPIO output (Tally-Lampe):** any pin from {4, 5, 6, 12, 13, 16, 17,
  18, 19, 20, 21, 22, 23, 24, 25, 26, 27}. Open-drain, sink ≤16 mA.
- **GPIO input (Trigger):** same allow-list. Wire button between pin and
  GND; internal pull-up is enabled.
- **OLED (optional):** SSD1306 128×64 on I²C bus 1, address `0x3C`.

## License

Internal project. Not for redistribution.
