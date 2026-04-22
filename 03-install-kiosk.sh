#!/bin/bash
set -e
# Install minimal X + Chromium kiosk
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xserver-xorg xinit x11-xserver-utils \
  openbox unclutter \
  chromium fonts-dejavu

# Autologin for user talentwerk on tty1
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin talentwerk --noclear %I $TERM
EOF

# .bash_profile: startx on tty1
sudo -u talentwerk tee /home/talentwerk/.bash_profile >/dev/null <<'EOF'
if [ -z "$DISPLAY" ] && [ "$XDG_VTNR" = "1" ]; then
  exec startx -- -nocursor
fi
EOF

# .xinitrc: openbox + kiosk launcher
sudo -u talentwerk tee /home/talentwerk/.xinitrc >/dev/null <<'EOF'
#!/bin/sh
xset s off
xset -dpms
xset s noblank
unclutter -idle 3 &
exec openbox-session
EOF
sudo chmod +x /home/talentwerk/.xinitrc

# Openbox autostart: launch chromium with tabs
sudo -u talentwerk mkdir -p /home/talentwerk/.config/openbox
sudo -u talentwerk tee /home/talentwerk/.config/openbox/autostart >/dev/null <<'EOF'
# Wait for Companion to be up
for i in $(seq 1 60); do
  if curl -fs http://localhost:8000/ >/dev/null 2>&1; then break; fi
  sleep 1
done
rm -rf /home/talentwerk/.config/chromium/Singleton* 2>/dev/null
chromium \
  --no-first-run --disable-translate --disable-infobars \
  --noerrdialogs --disable-session-crashed-bubble --disable-features=TranslateUI \
  --start-maximized --new-window \
  "http://localhost:8000/" \
  "http://localhost:8000/#/connections" &
EOF

# Openbox keybindings: quit with Ctrl+Alt+Q, reload tabs F5
sudo -u talentwerk tee /home/talentwerk/.config/openbox/rc.xml >/dev/null <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <keyboard>
    <keybind key="C-A-q"><action name="Exit"><prompt>no</prompt></action></keybind>
    <keybind key="C-A-Left"><action name="DesktopLeft"/></keybind>
    <keybind key="C-A-Right"><action name="DesktopRight"/></keybind>
  </keyboard>
  <applications>
    <application class="*">
      <maximized>yes</maximized>
      <decor>no</decor>
    </application>
  </applications>
</openbox_config>
EOF

echo 'Kiosk installed. Reboot to apply.'
