#!/bin/bash
# Install TallyArbiter on the Pi via the official Debian package.
# Usage: sudo bash install-tallyarbiter.sh
set -e

VERSION="3.2.0"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo arm64)"
case "$ARCH" in
  arm64) DEB="tallyarbiter-v${VERSION}-linux-arm64.deb" ;;
  amd64) DEB="tallyarbiter-v${VERSION}-linux-amd64.deb" ;;
  *) echo "unsupported arch: $ARCH"; exit 1 ;;
esac
URL="https://github.com/josephdadams/TallyArbiter/releases/download/v${VERSION}/${DEB}"

TMP="$(mktemp -d)"
trap "rm -rf '$TMP'" EXIT

echo "--- downloading $DEB"
curl -fL -o "$TMP/$DEB" "$URL"

echo "--- installing"
sudo apt-get update -qq
sudo apt-get install -y "$TMP/$DEB" || sudo dpkg -i "$TMP/$DEB" || {
  sudo apt-get -f install -y
  sudo dpkg -i "$TMP/$DEB"
}

# Package ships a systemd unit. Enable + start it.
SERVICE="$(systemctl list-unit-files 'tallyarbiter*.service' --no-legend | awk '{print $1}' | head -n1)"
if [ -n "$SERVICE" ]; then
  sudo systemctl enable --now "$SERVICE"
  sleep 2
  echo "--- $SERVICE: $(systemctl is-active "$SERVICE")"
else
  echo "(no systemd unit shipped — start the app manually)"
fi

echo "--- HTTP check"
curl -s -o /dev/null -w "tallyarbiter HTTP %{http_code}\n" http://localhost:4455/ || true
echo
echo "Done. Open http://$(hostname -I | awk '{print $1}'):4455/ to configure."
echo "In pi-guide Tally tab, set mode=TallyArbiter and URL to http://localhost:4455"
