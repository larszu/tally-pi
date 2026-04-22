#!/bin/bash
set -e
CFG=/boot/firmware/config.txt
if ! grep -qE '^dtparam=i2c_arm=on' "$CFG"; then
  echo "Enabling I2C in $CFG"
  echo 'dtparam=i2c_arm=on' | sudo tee -a "$CFG" >/dev/null
  NEED_REBOOT=1
fi
if ! lsmod | grep -q '^i2c_dev'; then
  echo 'i2c-dev' | sudo tee -a /etc/modules >/dev/null 2>&1 || true
  sudo modprobe i2c-dev || true
fi
sudo apt-get update
sudo apt-get install -y i2c-tools python3-venv python3-pip python3-dev libjpeg-dev zlib1g-dev libfreetype6-dev
echo '--- I2C devices ---'
ls /dev/i2c-* 2>/dev/null || true
echo '--- scan (if bus up) ---'
if [ -e /dev/i2c-1 ]; then i2cdetect -y 1 || true; fi
if [ "${NEED_REBOOT:-0}" = "1" ]; then
  echo 'REBOOT_REQUIRED'
fi
