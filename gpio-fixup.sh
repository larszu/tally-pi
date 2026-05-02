#!/bin/bash
# Reset active_low=1 that Companion's raspberry-gpio module sets on GPIO4
# (Pi 5: BCM4 = global gpio575). Without this fix the watcher reads the pin
# inverted compared to other inputs.
#
# Waits up to 60s for Companion to export the pin after boot.

set -u

PIN_GLOBAL=575
PIN_PATH="/sys/class/gpio/gpio${PIN_GLOBAL}/active_low"

for i in $(seq 1 60); do
    if [ -f "$PIN_PATH" ]; then
        if [ "$(cat "$PIN_PATH")" = "1" ]; then
            echo 0 > "$PIN_PATH"
            echo "gpio-fixup: reset active_low on gpio${PIN_GLOBAL} (BCM4)"
        else
            echo "gpio-fixup: gpio${PIN_GLOBAL} already active_low=0, nothing to do"
        fi
        exit 0
    fi
    sleep 1
done

echo "gpio-fixup: timeout — gpio${PIN_GLOBAL} not exported within 60s" >&2
exit 1
