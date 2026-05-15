#!/bin/bash
# Run from the extracted tarball directory on the Pi.
set -euo pipefail

log() { printf '\033[1;34m[update]\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "needs sudo" >&2; exit 1; }

log "Python-Watcher und setup-guide.html nach /opt/pi-guide"
install -d /opt/pi-guide
install -m 644 setup-guide.html    /opt/pi-guide/
install -m 755 guide_server.py     /opt/pi-guide/
install -m 755 gpio_watcher.py     /opt/pi-guide/
install -m 755 numato_watcher.py   /opt/pi-guide/
install -m 755 atem_watcher.py     /opt/pi-guide/

[ -f /opt/pi-guide/bindings.json ] || echo '[]' > /opt/pi-guide/bindings.json
[ -f /opt/pi-guide/tally.json ]   || echo '{"atem_ip":"","devices":[]}' > /opt/pi-guide/tally.json
chmod 664 /opt/pi-guide/bindings.json /opt/pi-guide/tally.json

log "Systemd-Units nach /etc/systemd/system"
install -m 644 pi-guide.service          /etc/systemd/system/
install -m 644 pi-gpio-watcher.service   /etc/systemd/system/
install -m 644 pi-numato-watcher.service /etc/systemd/system/
install -m 644 pi-atem-watcher.service   /etc/systemd/system/
systemctl daemon-reload

log "Services enable + restart"
systemctl enable pi-guide pi-gpio-watcher pi-atem-watcher pi-numato-watcher >/dev/null 2>&1 || true
systemctl restart pi-guide pi-gpio-watcher pi-atem-watcher pi-numato-watcher || true

log "Status:"
systemctl is-active pi-guide pi-gpio-watcher pi-atem-watcher pi-numato-watcher || true
