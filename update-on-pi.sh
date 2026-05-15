#!/bin/bash
# Run from the extracted tarball directory on the Pi.
set -euo pipefail

log() { printf '\033[1;34m[update]\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "needs sudo" >&2; exit 1; }

log "Stop alte Services (pi-guide / pi-gpio-watcher falls vorhanden)"
systemctl stop pi-guide pi-gpio-watcher 2>/dev/null || true
systemctl disable pi-guide pi-gpio-watcher 2>/dev/null || true
rm -f /etc/systemd/system/pi-guide.service /etc/systemd/system/pi-gpio-watcher.service
rm -f /opt/pi-guide/guide_server.py /opt/pi-guide/gpio_watcher.py /opt/pi-guide/setup-guide.html

log "Files nach /opt/pi-guide"
install -d /opt/pi-guide
install -m 755 pi-tally.py     /opt/pi-guide/
install -m 755 atem_watcher.py /opt/pi-guide/

[ -f /opt/pi-guide/tally.json ] || echo '{"atem_ip":"","devices":[]}' > /opt/pi-guide/tally.json
chmod 664 /opt/pi-guide/tally.json

log "Systemd-Units"
install -m 644 pi-tally.service        /etc/systemd/system/
install -m 644 pi-atem-watcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pi-tally pi-atem-watcher
systemctl restart pi-atem-watcher pi-tally

log "Status:"
sleep 1
systemctl is-active pi-tally pi-atem-watcher
sudo journalctl -u pi-tally -n 5 --no-pager
