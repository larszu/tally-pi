#!/bin/bash
set -e
sudo mkdir -p /opt/pi-guide
sudo cp /tmp/pi-setup/setup-guide.html /opt/pi-guide/
sudo cp /tmp/pi-setup/guide_server.py /opt/pi-guide/
sudo cp /tmp/pi-setup/pi-guide.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-guide
sleep 2
systemctl is-active pi-guide
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8080/
