#!/usr/bin/env bash
#
# Set up the tee time site on a Mac that stays on (an office machine, a
# spare Mac Pro). Installs launchd agents for the collector, the web
# server and the tunnel, plus the 8am card.
#
#   ./deploy/setup-mac.sh           # install and start
#   ./deploy/setup-mac.sh --stop    # unload everything
#   ./deploy/setup-mac.sh --status
#
# Safe to re-run. Paths are generated from wherever this repo lives, so
# nothing needs hand-editing.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="${HOME}/Library/LaunchAgents"
PY="$(command -v python3)"
LABELS=(collector server tunnel dailycard)

usage_stop() {
  for label in "${LABELS[@]}"; do
    plist="${AGENTS}/com.golfbook.${label}.plist"
    [ -f "${plist}" ] && launchctl unload "${plist}" 2>/dev/null || true
  done
  echo "Unloaded. The plists are still in ${AGENTS} — delete them to remove entirely."
  exit 0
}

usage_status() {
  launchctl list | grep -E 'com\.golfbook\.' || echo "Nothing loaded."
  echo
  if curl -fsS --max-time 4 http://localhost:8080/healthz 2>/dev/null; then echo; fi
  if [ -f "${REPO_DIR}/data/tunnel.log" ]; then
    echo -n "Public URL: "
    grep -oE 'https://[a-z0-9-]+\.(trycloudflare\.com|ngrok-free\.app)' \
      "${REPO_DIR}/data/tunnel.log" | tail -1 || echo "(not up yet)"
  fi
  exit 0
}

[ "${1:-}" = "--stop" ] && usage_stop
[ "${1:-}" = "--status" ] && usage_status

echo "==> Tee time site setup in ${REPO_DIR}"
mkdir -p "${REPO_DIR}/data" "${AGENTS}"

# ---------------------------------------------------------------- deps
echo "==> Checking dependencies"
"${PY}" -c 'import playwright' 2>/dev/null || \
  pip3 install --break-system-packages --upgrade playwright
"${PY}" -m playwright install chromium >/dev/null 2>&1 || true

# Pick the tunnel: ngrok when a reserved domain is configured, else a
# cloudflared quick tunnel (whose URL changes on every restart).
TUNNEL_BIN=""
TUNNEL_ARGS=""
if [ -f "${REPO_DIR}/deploy/tunnel.env" ]; then
  # shellcheck disable=SC1091
  . "${REPO_DIR}/deploy/tunnel.env"
  if ! command -v ngrok >/dev/null 2>&1; then
    echo "!!  deploy/tunnel.env exists but ngrok is not installed."
    echo "!!  brew install ngrok  — then re-run this script."
    exit 1
  fi
  TUNNEL_BIN="$(command -v ngrok)"
  TUNNEL_ARGS="<string>http</string>
    <string>--domain=${NGROK_DOMAIN}</string>
    <string>--log=stdout</string>
    <string>8080</string>"
  echo "==> Tunnel: ngrok on ${NGROK_DOMAIN}"
else
  if ! command -v cloudflared >/dev/null 2>&1; then
    echo "==> Installing cloudflared"
    brew install cloudflared
  fi
  TUNNEL_BIN="$(command -v cloudflared)"
  TUNNEL_ARGS="<string>tunnel</string>
    <string>--url</string>
    <string>http://localhost:8080</string>"
  echo "==> Tunnel: cloudflared quick tunnel (URL changes on restart)."
  echo "    For a stable address see deploy/tunnel.env.example"
fi

# ---------------------------------------------------------------- agents
# KeepAlive restarts a crashed job. RunAtLoad starts it now and on login.
write_agent() {
  local label="$1" program="$2" args="$3" keepalive="$4" runatload="$5" extra="${6:-}"
  cat > "${AGENTS}/com.golfbook.${label}.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.golfbook.${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${program}</string>
    ${args}
  </array>
  <key>WorkingDirectory</key><string>${REPO_DIR}</string>
  <key>RunAtLoad</key><${runatload}/>
  <key>KeepAlive</key><${keepalive}/>
${extra}
  <key>StandardOutPath</key><string>${REPO_DIR}/data/${label}.log</string>
  <key>StandardErrorPath</key><string>${REPO_DIR}/data/${label}.log</string>
</dict>
</plist>
PLIST
}

echo "==> Writing launchd agents"

write_agent collector "${PY}" \
  "<string>collector.py</string>
    <string>--interval</string>
    <string>300</string>" true true

write_agent server "${PY}" \
  "<string>server.py</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>8080</string>" true true

write_agent tunnel "${TUNNEL_BIN}" "${TUNNEL_ARGS}" true true

# The card is a once-a-day job, so no KeepAlive — a calendar trigger
# instead. launchd runs a missed 8am job when the Mac wakes; cron would
# simply skip it.
write_agent dailycard "${PY}" \
  "<string>daily_card.py</string>
    <string>--course</string>
    <string>charleston-muni</string>" false false \
  "  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"

echo "==> Loading"
for label in "${LABELS[@]}"; do
  plist="${AGENTS}/com.golfbook.${label}.plist"
  plutil -lint "${plist}" >/dev/null
  launchctl unload "${plist}" 2>/dev/null || true
  launchctl load "${plist}"
done

cat <<EOF

==> Done. The first collection pass takes a couple of minutes.

    Status:   ./deploy/setup-mac.sh --status
    Stop:     ./deploy/setup-mac.sh --stop
    Logs:     tail -f ${REPO_DIR}/data/collector.log
    Local:    http://localhost:8080

    Keep the Mac awake, or none of this runs:
      sudo pmset -a sleep 0 disablesleep 1
      sudo pmset -a autorestart 1     # come back after a power cut

    This Mac can also run Charleston Muni — WebTrac needs a real browser
    window, which doesn't matter on a machine nobody sits at. Set
    "enabled": true for charleston-muni in courses.json; the collector
    re-reads it on the next pass.
EOF
