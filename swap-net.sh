#!/bin/bash
set -e
IP="192.168.10.54"
nmcli con mod "Wired connection 1" ipv4.addresses ${IP}/24 ipv4.gateway 192.168.10.1 ipv4.dns "1.1.1.1 8.8.8.8" ipv4.method manual ipv4.ignore-auto-dns yes connection.autoconnect yes
nmcli con down "Wired connection 1" 2>/dev/null || true
nmcli con up "Wired connection 1"
sleep 2
echo "--- eth0 ---"
ip -4 -o addr show eth0 | grep inet || echo "eth0 no IP"
echo "--- wlan0 ---"
ip -4 -o addr show wlan0 | grep inet || echo "wlan0 no IP"
echo "--- route ---"
ip route | grep default
