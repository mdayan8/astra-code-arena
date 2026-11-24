#!/bin/bash
# ASTRA remote watch: install / uninstall the systemd service.
#   sudo watch/install.sh               install + start (dashboard + Cloudflare tunnel if cloudflared exists)
#   sudo watch/install.sh --no-tunnel   install without the public tunnel (dashboard stays on 127.0.0.1)
#   sudo watch/install.sh --uninstall   stop and remove the service (keeps /etc/astra-watch.json)
set -euo pipefail
UNIT=/etc/systemd/system/astra-watch.service
WATCH="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now astra-watch 2>/dev/null || true
  rm -f "$UNIT"
  systemctl daemon-reload
  echo "astra-watch removed. Config/state kept: /etc/astra-watch.json /var/lib/astra-watch (delete them to forget the topic)."
  exit 0
fi

TUNNEL="--tunnel"; [ "${1:-}" = "--no-tunnel" ] && TUNNEL=""
PY=$(command -v python3) || { echo "python3 not found"; exit 1; }
case "$WATCH" in /opt/astra-arena/*)
  echo "Note: $WATCH is replaced on platform updates; if watch/ is not part of the release, copy the repo elsewhere and run install.sh from there.";;
esac

"$PY" "$WATCH/watch.py" --info >/dev/null   # creates /etc/astra-watch.json (mode 600) with a random topic + dashboard key

cat > "$UNIT" <<EOF
[Unit]
Description=ASTRA Code Arena remote watch (alerts + status page, read-only)
After=network-online.target astra-arena.service
Wants=network-online.target

[Service]
# root: the platform DB is root-owned mode 600. The watcher only opens it read-only.
ExecStart=$PY $WATCH/watch.py --serve $TUNNEL
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/astra-arena.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable astra-watch >/dev/null
systemctl restart astra-watch
command -v cloudflared >/dev/null || [ -z "$TUNNEL" ] || "$PY" -c "import sys; sys.path.insert(0, '$WATCH'); import watch; print(watch.CLOUDFLARED_HELP)"
echo
echo "astra-watch is running."
"$PY" "$WATCH/watch.py" --info
echo
echo "Phone: install the ntfy app, tap +, subscribe to the topic above (or open the Subscribe link in a browser)."
echo "Test:  sudo $PY $WATCH/watch.py --test-alert      Logs: journalctl -u astra-watch -f"
