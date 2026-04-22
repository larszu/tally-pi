#!/bin/bash
set -e
sudo mkdir -p /opt/pi-guide
sudo cp /tmp/pi-setup/setup-guide.html /opt/pi-guide/
sudo cp /tmp/pi-setup/guide_server.py /opt/pi-guide/
sudo cp /tmp/pi-setup/gpio_watcher.py /opt/pi-guide/
sudo cp /tmp/pi-setup/pi-guide.service /etc/systemd/system/
sudo cp /tmp/pi-setup/pi-gpio-watcher.service /etc/systemd/system/
[ -f /opt/pi-guide/bindings.json ] || { echo '[]' | sudo tee /opt/pi-guide/bindings.json >/dev/null; }
sudo chmod 664 /opt/pi-guide/bindings.json
sudo systemctl daemon-reload
sudo systemctl restart pi-guide
sudo systemctl enable --now pi-gpio-watcher
sleep 2
echo --- pi-guide: $(systemctl is-active pi-guide)
echo --- pi-gpio-watcher: $(systemctl is-active pi-gpio-watcher)
curl -s -o /dev/null -w "guide HTTP %{http_code}\n" http://localhost:8080/
curl -s http://localhost:8080/bindings
echo
echo --- watcher log ---
journalctl -u pi-gpio-watcher --no-pager -n 10
