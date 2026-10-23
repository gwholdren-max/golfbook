#!/usr/bin/env bash
#
# Set up the tee time site on a fresh Raspberry Pi.
#
#   git clone https://github.com/gwholdren-max/golfbook.git ~/golfbook
#   cd ~/golfbook && ./deploy/setup-pi.sh
#
# Needs 64-bit Raspberry Pi OS — Playwright only ships an ARM64 Chromium.
# Safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
NEED_BROWSER=1

echo "==> Tee time site setup for ${USER_NAME} in ${REPO_DIR}"

if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "arm64" ]; then
  echo "!!  This is not ARM64 ($(uname -m)). On a 32-bit Pi OS, Playwright has"
  echo "!!  no Chromium build — reinstall with the 64-bit image, or run"
  echo "!!  HTTP-only by leaving Charleston Muni disabled in courses.json."
  NEED_BROWSER=0
fi

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip git curl
if [ "${NEED_BROWSER}" = "1" ]; then
  # xvfb gives the WebTrac adapter a virtual display; the rest are HTTP-only
  sudo apt-get install -y xvfb
fi

echo "==> Installing Python dependencies"
pip3 install --break-system-packages --upgrade playwright

if [ "${NEED_BROWSER}" = "1" ]; then
  echo "==> Installing Chromium for Playwright (this one is slow)"
  python3 -m playwright install --with-deps chromium
fi

echo "==> Installing cloudflared"
if ! command -v cloudflared >/dev/null 2>&1; then
  ARCH="arm64"; [ "$(uname -m)" = "armv7l" ] && ARCH="arm"
  curl -fsSL -o /tmp/cloudflared \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}"
  sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
  rm -f /tmp/cloudflared
fi
cloudflared --version

mkdir -p "${REPO_DIR}/data"

echo "==> Installing systemd services"
for unit in collector server tunnel; do
  sudo cp "${REPO_DIR}/deploy/teetime-${unit}@.service" /etc/systemd/system/
done
sudo systemctl daemon-reload
# The units are templates: %i becomes the username, so paths resolve to
# /home/<user>/golfbook without editing the files.
for unit in collector server tunnel; do
  sudo systemctl enable --now "teetime-${unit}@${USER_NAME}.service"
done

echo
echo "==> Done. First collection pass takes a minute or two."
echo
echo "    Status:   systemctl status 'teetime-*'"
echo "    Logs:     journalctl -u teetime-collector@${USER_NAME} -f"
echo "    Local:    http://$(hostname -I | awk '{print $1}'):8080"
echo "    Health:   curl -s localhost:8080/healthz"
echo
echo "    Public URL (quick tunnels change on every restart):"
echo "      grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ${REPO_DIR}/data/tunnel.log | head -1"
echo
if [ "${NEED_BROWSER}" = "1" ]; then
  echo "    Charleston Muni is paused in courses.json. On a headless Pi there's"
  echo "    no window to see, so you can turn it back on:"
  echo "      \"enabled\": false  ->  true"
fi
