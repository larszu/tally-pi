#!/bin/bash
set -e
sudo mkdir -p /opt/pi-guide
sudo cp /tmp/pi-setup/setup-guide.html /opt/pi-guide/
sudo cp /tmp/pi-setup/guide_server.py /opt/pi-guide/
sudo cp /tmp/pi-setup/gpio_watcher.py /opt/pi-guide/
sudo cp /tmp/pi-setup/numato_watcher.py /opt/pi-guide/ 2>/dev/null || true
sudo cp /tmp/pi-setup/atem_watcher.py /opt/pi-guide/ 2>/dev/null || true
sudo cp /tmp/pi-setup/pi-guide.service /etc/systemd/system/
sudo cp /tmp/pi-setup/pi-gpio-watcher.service /etc/systemd/system/
sudo cp /tmp/pi-setup/pi-numato-watcher.service /etc/systemd/system/ 2>/dev/null || true
sudo cp /tmp/pi-setup/pi-atem-watcher.service /etc/systemd/system/ 2>/dev/null || true
[ -f /opt/pi-guide/bindings.json ] || { echo '[]' | sudo tee /opt/pi-guide/bindings.json >/dev/null; }
[ -f /opt/pi-guide/tally.json ] || { echo '{"mode":"atem","atem_ip":"","arbiter_url":"http://localhost:4455","devices":[]}' | sudo tee /opt/pi-guide/tally.json >/dev/null; }
sudo chmod 664 /opt/pi-guide/bindings.json /opt/pi-guide/tally.json
# pyatem is optional – only needed for ATEM-direct mode
pip3 show pyatem >/dev/null 2>&1 || sudo pip3 install --break-system-packages pyatem || true
sudo systemctl daemon-reload
sudo systemctl restart pi-guide
# Onboard GPIO access is owned by Companion (raspberry-gpio module) by default.
# pi-gpio-watcher is installed but kept disabled to avoid claiming the same
# /dev/gpiochip0 lines. Enable it manually if you want HTTP-API bindings instead.
sudo systemctl disable --now pi-gpio-watcher 2>/dev/null || true
[ -f /etc/systemd/system/pi-numato-watcher.service ] && sudo systemctl enable --now pi-numato-watcher || true
[ -f /etc/systemd/system/pi-atem-watcher.service ] && sudo systemctl enable --now pi-atem-watcher || true
sleep 2
echo --- pi-guide: $(systemctl is-active pi-guide)
echo --- pi-gpio-watcher: $(systemctl is-active pi-gpio-watcher) "(disabled by default; Companion owns GPIO)"
echo --- pi-atem-watcher: $(systemctl is-active pi-atem-watcher 2>/dev/null || echo n/a)
curl -s -o /dev/null -w "guide HTTP %{http_code}\n" http://localhost:8080/
curl -s http://localhost:8080/bindings
echo
