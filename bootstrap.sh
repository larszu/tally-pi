#!/bin/bash
# TW Broadcast Interface — one-shot installer for a fresh Companion Pi.
#
# Usage on a brand new Pi (already flashed with Companion Pi image,
# SSH enabled, logged in as user 'talentwerk'):
#
#   curl -fsSL https://raw.githubusercontent.com/larszu/tw-broadcast-interface/main/bootstrap.sh | sudo bash
#
# or clone first:
#
#   git clone https://github.com/larszu/tw-broadcast-interface.git
#   cd tw-broadcast-interface && sudo bash bootstrap.sh
#
# The script is idempotent: re-running it updates to the latest main.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/larszu/tw-broadcast-interface.git}"
REPO_DIR="${REPO_DIR:-/opt/tw-broadcast-interface}"
TARGET_USER="${TARGET_USER:-talentwerk}"

log() { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this as root (sudo bash bootstrap.sh)"
id "$TARGET_USER" >/dev/null 2>&1 || die "User '$TARGET_USER' does not exist"

log "1/7 Installing apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    i2c-tools python3-pip python3-venv python3-dev \
    python3-serial python3-libgpiod \
    libjpeg-dev zlib1g-dev libfreetype6-dev \
    xserver-xorg xinit x11-xserver-utils openbox unclutter chromium \
    fonts-dejavu \
    onboard at-spi2-core

log "2/7 Cloning / updating repository at $REPO_DIR"
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" fetch --depth 1 origin main
    git -C "$REPO_DIR" reset --hard origin/main
else
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

log "3/7 Enabling I²C + HDMI compatibility in /boot/firmware/config.txt"
CONFIG_TXT="/boot/firmware/config.txt"
if [ -f "$CONFIG_TXT" ]; then
    if ! grep -qE '^\s*dtparam=i2c_arm=on' "$CONFIG_TXT"; then
        echo "dtparam=i2c_arm=on" >> "$CONFIG_TXT"
        REBOOT_NEEDED=1
    fi
    # HDMI: force output even with couplers/adapters that block HPD or EDID
    for setting in \
        "hdmi_force_hotplug=1" \
        "hdmi_drive=2" \
        "config_hdmi_boost=7" \
        "hdmi_ignore_edid=0xa5000080" \
        "hdmi_group=2" \
        "hdmi_mode=82"
    do
        key="${setting%%=*}"
        if grep -qE "^\s*${key}=" "$CONFIG_TXT"; then
            sed -i "s|^\s*${key}=.*|${setting}|" "$CONFIG_TXT"
        else
            echo "$setting" >> "$CONFIG_TXT"
        fi
    done
fi
adduser "$TARGET_USER" i2c    >/dev/null 2>&1 || true
adduser "$TARGET_USER" dialout >/dev/null 2>&1 || true
adduser "$TARGET_USER" gpio    >/dev/null 2>&1 || true

log "4/7 Deploying files to /opt/pi-guide and /opt/pi-status"
install -d /opt/pi-guide /opt/pi-status
install -m 644 setup-guide.html    /opt/pi-guide/
install -m 755 guide_server.py     /opt/pi-guide/
install -m 755 gpio_watcher.py     /opt/pi-guide/
install -m 755 numato_watcher.py   /opt/pi-guide/
install -m 755 atem_watcher.py     /opt/pi-guide/
install -m 755 pi_status.py        /opt/pi-status/
[ -f /opt/pi-guide/bindings.json ] || echo '[]' > /opt/pi-guide/bindings.json
[ -f /opt/pi-guide/tally.json ]   || echo '{"atem_ip":"","devices":[]}' > /opt/pi-guide/tally.json
chmod 664 /opt/pi-guide/bindings.json /opt/pi-guide/tally.json

# Optional: build the pi-status venv if it doesn't exist yet. pi-status itself
# stays disabled — enable manually once an OLED is connected.
if [ ! -d /opt/pi-status/venv ]; then
    python3 -m venv /opt/pi-status/venv
    /opt/pi-status/venv/bin/pip install --quiet --upgrade pip
    /opt/pi-status/venv/bin/pip install --quiet luma.oled || true
fi

log "5/7 Installing systemd units and udev rules"
install -m 644 pi-guide.service          /etc/systemd/system/
install -m 644 pi-gpio-watcher.service   /etc/systemd/system/
install -m 644 pi-numato-watcher.service /etc/systemd/system/
install -m 644 pi-atem-watcher.service   /etc/systemd/system/
install -m 644 pi-status.service         /etc/systemd/system/
install -m 644 10-modesetting.conf       /etc/X11/xorg.conf.d/
install -m 644 99-numato.rules           /etc/udev/rules.d/
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=tty   || true
systemctl daemon-reload

log "6/7 Setting up Chromium kiosk (auto-login on tty1, Openbox autostart)"
# autologin on tty1
install -d /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $TARGET_USER --noclear %I \$TERM
EOF

HOME_DIR="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$HOME_DIR/.config/openbox"

# startx on login
if ! grep -q 'exec startx' "$HOME_DIR/.bash_profile" 2>/dev/null; then
    cat >> "$HOME_DIR/.bash_profile" <<'EOF'
if [ -z "$DISPLAY" ] && [ "$XDG_VTNR" = "1" ]; then
    exec startx
fi
EOF
    chown "$TARGET_USER:$TARGET_USER" "$HOME_DIR/.bash_profile"
fi

# .xinitrc
cat > "$HOME_DIR/.xinitrc" <<'EOF'
xset s off
xset -dpms
xset s noblank
exec openbox-session
EOF
chown "$TARGET_USER:$TARGET_USER" "$HOME_DIR/.xinitrc"
chmod +x "$HOME_DIR/.xinitrc"

# Openbox autostart — force LF endings (never CRLF)
install -m 755 -o "$TARGET_USER" -g "$TARGET_USER" openbox-autostart "$HOME_DIR/.config/openbox/autostart"
# strip any \r that might sneak in from Windows
sed -i 's/\r$//' "$HOME_DIR/.config/openbox/autostart"

# Openbox keybindings
cat > "$HOME_DIR/.config/openbox/rc.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <keyboard>
    <keybind key="C-A-q"><action name="Exit"/></keybind>
  </keyboard>
</openbox_config>
EOF
chown "$TARGET_USER:$TARGET_USER" "$HOME_DIR/.config/openbox/rc.xml"

log "7/7 Enabling services"
# Companion may or may not be present (image vs. plain Raspberry Pi OS).
systemctl enable companion                >/dev/null 2>&1 || true
systemctl enable pi-guide                 >/dev/null 2>&1 || true
# pi-guide owns GPIO OUTPUTS (Tally-Lampen, open-drain) and pi-gpio-watcher
# owns GPIO INPUTS (Trigger-Taster). Both run together — the config UI
# enforces disjoint pins.
systemctl enable pi-gpio-watcher          >/dev/null 2>&1 || true
systemctl enable pi-atem-watcher          >/dev/null 2>&1 || true
systemctl enable pi-numato-watcher        >/dev/null 2>&1 || true
systemctl enable getty@tty1               >/dev/null 2>&1 || true
# pi-status stays disabled until an OLED is physically present
systemctl disable pi-status               >/dev/null 2>&1 || true

# Restart what's safe to bounce now (without flapping Companion / kiosk).
systemctl restart pi-guide          || true
systemctl restart pi-gpio-watcher   || true
systemctl restart pi-atem-watcher   || true
systemctl restart pi-numato-watcher || true

PI_IP="$(hostname -I | awk '{print $1}')"
log "=================================================================="
log "  Installation complete."
log "  Setup-Guide:  http://${PI_IP}:8080/"
log "  Companion:    http://${PI_IP}:8000/"
log "  Exit kiosk:   Ctrl+Alt+Q"
log ""
log "  Next steps:"
log "    1. Setup-Guide öffnen → Tab 'Tally'"
log "    2. ATEM-IP eintragen → 'Übernehmen'"
log "    3. Geräte (Kameras) anlegen, pro Gerät GPIO-Pin wählen"
log ""
if [ -n "${REBOOT_NEEDED:-}" ]; then
    log "  REBOOT REQUIRED to activate I²C. Run: sudo reboot"
else
    log "  Reboot optional. Kiosk will start on next boot."
fi
log "=================================================================="
