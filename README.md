# pi-tally

Minimal tally driver and trigger button handler for a Raspberry Pi 5
attached to a Bitfocus Companion / ATEM workflow.

Each configured device has:

- **Tally-Out GPIO** (optional): Pi drives the pin LOW when this device's
  ATEM input is on PGM. Idle = HIGH. Designed to feed an active-low
  optocoupler relay module which then closes a contact for things like
  a Telecast Copperhead tally input.
- **Trigger-In GPIO** (optional): button between the pin and GND. On the
  falling edge (press), the configured action fires:
    - `atem_aux`: set an ATEM Aux output to a chosen source.
    - `companion_press`: press a Companion button (page/row/column).
- **Browser-Tally URL**: `http://<pi>:8080/tally/<id>` — fullscreen
  red/green page for a phone or tablet, follows live ATEM state.

The web UI on `:8080` is the single place to configure all of this, and
it shows the live GPIO state of every claimed pin.

## Components

| File | Role |
|---|---|
| `pi-tally.py` | The whole daemon — HTTP UI, GPIO outputs (single multi-line libgpiod request, atomic `set_values()` writes), GPIO inputs (edge-detect), config persistence. |
| `atem_watcher.py` | Hand-rolled UDP client for the ATEM switcher. Writes live state to `/run/pi-guide/atem.json`; listens on `/run/pi-guide/atem-cmd.sock` for `set_aux` commands from pi-tally. |
| `numato_watcher.py` | Hot-plug poller for an optional Numato 32-CH USB GPIO module. |
| `pi_status.py` | Optional OLED status display. |
| `pi-tally.service` `pi-atem-watcher.service` `pi-numato-watcher.service` `pi-status.service` | systemd units. |
| `bootstrap.sh` | One-shot installer for a fresh Pi. |

## Quick install

On a Pi running the Companion Pi image (or plain Raspberry Pi OS with
Companion installed and a user `talentwerk`):

```bash
curl -fsSL https://raw.githubusercontent.com/larszu/tw-broadcast-interface/main/bootstrap.sh \
  | sudo bash
sudo reboot   # only the first time — activates I²C
```

The installer is idempotent: re-running it pulls `main`, redeploys and
bounces the services. To deploy a feature branch instead:

```bash
curl -fsSL https://raw.githubusercontent.com/larszu/tw-broadcast-interface/<branch>/bootstrap.sh \
  | sudo REPO_BRANCH=<branch> bash
```

## After install

1. Open `http://<pi>:8080/`.
2. Enter the ATEM IP and click **Speichern**.
3. **+ Gerät** to add a camera. Set:
   - `ATEM-Input` (the input number that this camera occupies on the switcher)
   - `Tally-Out` GPIO (optional)
   - `Trigger-In` GPIO + action (optional)
4. **Speichern** to apply.

The "GPIO-Status" card at the bottom shows every claimed pin and its
live state.

## Wiring notes — Tally output

The Pi GPIO **actively drives 3.3 V HIGH in the idle state** and **0 V
LOW in the active state**. We don't use open-drain on Pi 5 because the
RP1 pinctrl driver leaks ~3 V through a hidden pull-up in its
"released" state, which would keep a passive contact-sense circuit
permanently closed.

For an AZ-Delivery / Sainsmart-style active-low relay module
(SRD-05VDC-SL-C with optocoupler input), the typical issue is that the
optocoupler triggers even at the 3.3 V Pi high level. Two well-tested
workarounds:

1. **Series diode per channel** (1N4148 between Pi GPIO and module IN,
   cathode/ring toward the Pi). Drops the idle voltage another 0.7 V
   below the optocoupler threshold.
2. **Module-VCC at 3.3 V, JD-VCC at 5 V** (jumper removed). Only works
   if the module's optocoupler turns off cleanly at 0 V across its LED
   — i.e. when Pi-VCC and Pi-HIGH match exactly. For modules with very
   sensitive optocouplers, may still leak.

## Wiring notes — Trigger input

Pin is configured as input with internal pull-up + 20 ms debounce. Wire
a momentary button between the GPIO pin and any Pi GND pin. Press =
falling edge = action fires.

## Configuration file

`/opt/pi-guide/tally.json` — created by the UI, but editable:

```json
{
  "atem_ip": "192.168.10.22",
  "devices": [
    {
      "id": "cam1",
      "name": "Kamera 1",
      "atem_input": 1,
      "atem_me": 1,
      "out_gpio": 5,
      "in_gpio": 4,
      "in_action": { "type": "atem_aux", "aux": 1, "source": 1 }
    }
  ]
}
```

`in_action` shapes:

- `{"type": "none"}`
- `{"type": "atem_aux", "aux": <1-24>, "source": <input_number_or_null>}`  
  (`source: null` falls back to the device's own `atem_input`)
- `{"type": "companion_press", "page": P, "row": R, "col": C}`

## License

Internal project. Not for redistribution.
