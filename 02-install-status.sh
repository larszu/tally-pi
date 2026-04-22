#!/bin/bash
set -e
sudo mkdir -p /opt/pi-status
sudo cp /tmp/pi-setup/pi_status.py /opt/pi-status/
sudo python3 -m venv /opt/pi-status/venv
sudo /opt/pi-status/venv/bin/pip install --upgrade pip
sudo /opt/pi-status/venv/bin/pip install luma.oled
sudo cp /tmp/pi-setup/pi-status.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-status.service
sleep 3
sudo systemctl status pi-status.service --no-pager || true
