#!/bin/bash
set -e
nmcli con mod "Wired connection 1" ipv4.addresses 192.168.10.52/24 ipv4.gateway 192.168.10.1 ipv4.dns "1.1.1.1 8.8.8.8" ipv4.method manual ipv4.ignore-auto-dns yes connection.autoconnect yes
nmcli con mod "Talentwerk" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" ipv4.ignore-auto-dns no
echo OK-profiles
nmcli -t -f NAME,DEVICE,STATE con show
