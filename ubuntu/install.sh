#!/usr/bin/env bash
# ASTRA Code Arena: one-command install for Ubuntu 22.04 / 24.04 (desktop or server).
#   sudo ./ubuntu/install.sh            install or update (keeps all data)
#   sudo ./ubuntu/install.sh --with-ai  also install the local AI model (Prompt challenges, AI Studio); needs ~1.5 GB more RAM
#   sudo ./ubuntu/install.sh --uninstall  remove the app (data in /var/lib/astra-arena is kept)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/astra-arena DATA=/var/lib/astra-arena ENVF=/etc/astra-arena.env SVC=astra-arena
STEP=""
say() { STEP="$*"; printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }   # the setup app reads these lines for its progress bar
trap 'echo "##FAILED during: $STEP"' ERR

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now $SVC 2>/dev/null || true
  rm -rf $APP /etc/systemd/system/$SVC.service /usr/local/bin/astra-arena /usr/share/applications/astra-arena.desktop
  systemctl daemon-reload; echo "Removed. Your data is still in $DATA (delete it yourself if you really want to)."; exit 0
fi

say "Checking this computer"
FREE_GB=$(df -BG --output=avail /var | tail -1 | tr -dc 0-9)
echo "Free disk: ${FREE_GB} GB, RAM: $(free -g | awk '/Mem:/{print $2+1}') GB, cores: $(nproc)"
[ "$FREE_GB" -ge 20 ] || { echo "##FAILED: only ${FREE_GB} GB free on /var. ASTRA Arena needs at least 20 GB (40 GB recommended)."; exit 1; }
[ "$FREE_GB" -ge 40 ] || echo "Note: ${FREE_GB} GB free. Works, but 40 GB+ is recommended; old backups may need clearing after a semester."

say "Installing compilers and Python (needs internet this one time)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q || { echo "##FAILED: cannot reach the Ubuntu servers. Connect this PC to the internet for the installation, then try again."; exit 1; }
apt-get install -y -q python3 python3-venv python3-pip gcc g++ default-jdk-headless sqlite3 rsync curl xz-utils

say "Installing Node.js"
# Ubuntu 22.04's own Node.js is v12 (too old for modern JavaScript), so use the official Node 20 LTS build
if ! node -e 'process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)' 2>/dev/null; then
  NODE_V=v20.18.1; ARCH=$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/')
  curl -fsSL "https://nodejs.org/dist/$NODE_V/node-$NODE_V-linux-$ARCH.tar.xz" | tar -xJ -C /opt
  ln -sf /opt/node-$NODE_V-linux-$ARCH/bin/node /usr/local/bin/node
fi
node -v

say "Copying the app to $APP (data stays in $DATA)"
mkdir -p $APP $DATA/backups
rsync -a --delete --exclude data --exclude logs --exclude .venv --exclude '__pycache__' --exclude .git "$SRC"/ $APP/
(cd "$SRC" && git rev-parse HEAD 2>/dev/null || date +%s) > $APP/.installed-version

say "Installing Python packages"
python3 -m venv $APP/.venv
$APP/.venv/bin/pip install --progress-bar off --upgrade pip
$APP/.venv/bin/pip install --progress-bar off -r $APP/requirements.txt

if [ ! -f $ENVF ]; then
  cat > $ENVF <<EOF
# ASTRA Code Arena settings (edit, then: sudo systemctl restart $SVC)
DATA_DIR=$DATA
APP_TIMEZONE=Asia/Kolkata
JUDGE_TMP=/var/tmp/astra-judge
# AI_BASE_URL=http://127.0.0.1:11434/v1
# AI_MODEL=qwen2.5-coder:1.5b
EOF
fi

if [ "${1:-}" = "--with-ai" ] || [ "${2:-}" = "--with-ai" ]; then
  say "Installing the local AI model (Ollama + qwen2.5-coder:1.5b, about 1 GB download)"
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama
  sleep 3; ollama pull qwen2.5-coder:1.5b
  sed -i 's|^# AI_BASE_URL=.*|AI_BASE_URL=http://127.0.0.1:11434/v1|; s|^# AI_MODEL=.*|AI_MODEL=qwen2.5-coder:1.5b|' $ENVF
fi

say "Creating the background service (starts at boot, restarts on crash)"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=ASTRA Code Arena
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=$ENVF
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python launcher.py --background
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
# keep the desktop responsive while judging
Nice=5
MemoryHigh=80%

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/astra-arena <<'EOF'
#!/usr/bin/env bash
# astra-arena: open | status | start | stop | restart | logs | backup | doctor
case "${1:-open}" in
  open) xdg-open http://127.0.0.1:8765/ >/dev/null 2>&1 || echo "Open http://127.0.0.1:8765/ in a browser on this computer." ;;
  status) systemctl status astra-arena --no-pager ;;
  start|stop|restart) sudo systemctl "$1" astra-arena ;;
  logs) sudo journalctl -u astra-arena -n 100 --no-pager; sudo tail -n 60 /var/lib/astra-arena/logs/*.log ;;
  backup) sudo bash -c 'set -a; . /etc/astra-arena.env; cd /opt/astra-arena && .venv/bin/python -m app.manage backup' ;;
  doctor) curl -s http://127.0.0.1:8765/api/diagnose | python3 -c 'import json,sys
for c in json.load(sys.stdin): print({"ok":"[ OK ]","warn":"[WARN]","err":"[FAIL]"}[c["level"]], c["name"]+":", c["detail"], ("\n        Fix: "+c["fix"]) if c["fix"] else "")' \
          || echo "The control panel is not answering. Try: astra-arena restart" ;;
  *) echo "usage: astra-arena open|status|start|stop|restart|logs|backup|doctor" ;;
esac
EOF
chmod 755 /usr/local/bin/astra-arena

cat > /usr/share/applications/astra-arena.desktop <<EOF
[Desktop Entry]
Name=ASTRA Code Arena
Comment=Run and schedule the campus coding platform
Exec=xdg-open http://127.0.0.1:8765/
Icon=$APP/static/img/arena-logo.png
Terminal=false
Type=Application
Categories=Education;Development;
EOF

say "Keeping the computer awake (a sleeping PC drops every student)"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable $SVC >/dev/null
systemctl restart $SVC
command -v ufw >/dev/null && ufw status | grep -q "Status: active" && ufw allow 8000/tcp >/dev/null || true

for i in $(seq 1 30); do curl -s http://127.0.0.1:8765/api/status -o /tmp/astra-st.json && break; sleep 1; done
IP=$(hostname -I | awk '{print $1}')
PW=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('first_password') or '')" 2>/dev/null || true)
say "Done. ASTRA Code Arena is running and starts by itself after every reboot."
echo "  Control panel (this PC): open 'ASTRA Code Arena' from the app menu, or run: astra-arena"
echo "  Students:                http://$IP:8000/"
echo "  Check health:            astra-arena doctor"
[ -n "$PW" ] && echo "  First admin login:       admin / $PW   (you must change it; the organiser link is in the control panel)"
exit 0
