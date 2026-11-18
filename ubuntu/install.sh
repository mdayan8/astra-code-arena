#!/usr/bin/env bash
# ASTRA Code Arena: one-command install for Ubuntu 22.04 / 24.04 (desktop or server).
#   sudo ./ubuntu/install.sh            install or update (keeps all data)
#   sudo ./ubuntu/install.sh --with-ai  also install the local AI model (Prompt challenges, AI Studio); needs ~1.5 GB more RAM
#   sudo ./ubuntu/install.sh --import FILE   bring all data from an old computer (a move package from its control panel)
#   sudo ./ubuntu/install.sh --uninstall [--purge]   remove the app (data kept unless --purge; a final move package is always kept)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/astra-arena DATA=/var/lib/astra-arena ENVF=/etc/astra-arena.env SVC=astra-arena
STEP=""
say() { STEP="$*"; printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }   # the setup app reads these lines for its progress bar
trap 'echo "##FAILED during: $STEP"' ERR

WITH_AI="" IMPORT="" UNINSTALL="" PURGE="" UPDATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-ai) WITH_AI=1 ;;
    --import) IMPORT="$(realpath "$2")"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) PURGE=1 ;;
    --update) UPDATE=1 ;;   # automatic update from GitHub: quick steps, health check, rollback if the new version fails
  esac
  shift
done
exec 9>/run/astra-arena-install.lock   # one install/update/uninstall at a time
flock -n 9 || { echo "##FAILED: another install or update is already running. Wait for it to finish."; exit 1; }
[ -z "$IMPORT" ] || [ -f "$IMPORT" ] || { echo "##FAILED: move package not found: $IMPORT"; exit 1; }

if [ -n "$UNINSTALL" ]; then
  PORT=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('port',8000))" 2>/dev/null || echo 8000)
  systemctl disable --now $SVC 2>/dev/null || true
  if [ -x $APP/.venv/bin/python ]; then
    (set -a; . $ENVF; set +a; cd $APP && .venv/bin/python launcher.py --release-ip) || true   # free the fixed address for the next host
    [ -n "$PURGE" ] || (set -a; . $ENVF; set +a; cd $APP && .venv/bin/python -c "import launcher; print('Final move package:', launcher.EXPORTS / launcher.make_package())") || true
  fi
  command -v ufw >/dev/null && ufw delete allow "$PORT/tcp" >/dev/null 2>&1 || true
  systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
  rm -rf $APP $APP.prev /etc/systemd/system/$SVC.service /usr/local/bin/astra-arena /usr/share/applications/astra-arena.desktop /usr/share/icons/hicolor/scalable/apps/astra-arena.svg
  readlink /usr/local/bin/node 2>/dev/null | grep -q '^/opt/node-v' && rm -f /usr/local/bin/node && rm -rf /opt/node-v20.18.1-linux-*
  systemctl daemon-reload
  if [ -n "$PURGE" ]; then rm -rf $DATA $ENVF; echo "Removed ASTRA Arena and all its data from this computer."
  else echo "Removed ASTRA Arena. Data and move packages are kept in $DATA (sudo $0 --uninstall --purge deletes them)."; fi
  exit 0
fi

say "Checking this computer"
FREE_GB=$(df -BG --output=avail /var | tail -1 | tr -dc 0-9)
echo "Free disk: ${FREE_GB} GB, RAM: $(free -g | awk '/Mem:/{print $2+1}') GB, cores: $(nproc)"
[ "$FREE_GB" -ge 20 ] || { echo "##FAILED: only ${FREE_GB} GB free on /var. ASTRA Arena needs at least 20 GB (40 GB recommended)."; exit 1; }
[ "$FREE_GB" -ge 40 ] || echo "Note: ${FREE_GB} GB free. Works, but 40 GB+ is recommended; old backups may need clearing after a semester."

say "Installing compilers and Python (needs internet this one time)"
export DEBIAN_FRONTEND=noninteractive
if [ -z "$UPDATE" ] || ! command -v g++ javac git rsync curl >/dev/null; then
  apt-get update -q || { echo "##FAILED: cannot reach the Ubuntu servers. Connect this PC to the internet for the installation, then try again."; exit 1; }
  apt-get install -y -q python3 python3-venv python3-pip gcc g++ default-jdk-headless sqlite3 rsync curl xz-utils git
fi

say "Installing Node.js"
# Ubuntu 22.04's own Node.js is v12 (too old for modern JavaScript), so use the official Node 20 LTS build
if ! node -e 'process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)' 2>/dev/null; then
  NODE_V=v20.18.1; ARCH=$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/')
  case "$ARCH" in   # pinned checksums: a tampered download is refused
    x64) SUM=c6fa75c841cbffac851678a472f2a5bd612fff8308ef39236190e1f8dbb0e567 ;;
    arm64) SUM=44d1ffc5905c005ace4515ca6f8c090c4c7cfce3a9a67df0dba35c727590b8f6 ;;
    *) echo "##FAILED: unsupported processor $ARCH"; exit 1 ;;
  esac
  curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/$NODE_V/node-$NODE_V-linux-$ARCH.tar.xz"
  echo "$SUM  /tmp/node.tar.xz" | sha256sum -c --quiet || { echo "##FAILED: the Node.js download did not match its checksum. Try again."; exit 1; }
  tar -xJf /tmp/node.tar.xz -C /opt && rm -f /tmp/node.tar.xz
  ln -sf /opt/node-$NODE_V-linux-$ARCH/bin/node /usr/local/bin/node
fi
node -v

say "Copying the app to $APP (data stays in $DATA)"
mkdir -p $APP $DATA/backups
[ -z "$UPDATE" ] || rsync -a --delete --exclude .venv $APP/ $APP.prev/   # the running version, kept for rollback
rsync -a --delete --exclude data --exclude logs --exclude .venv --exclude '__pycache__' --exclude .git --exclude .req-hash "$SRC"/ $APP/
(cd "$SRC" && git rev-parse HEAD 2>/dev/null || date +%s) > $APP/.installed-version

say "Installing Python packages"
[ -x $APP/.venv/bin/python ] || python3 -m venv $APP/.venv
REQ=$(sha256sum $APP/requirements.txt | cut -c1-64)
if [ "$(cat $APP/.req-hash 2>/dev/null)" != "$REQ" ]; then   # only when the package list changed
  $APP/.venv/bin/pip install --progress-bar off --upgrade pip
  $APP/.venv/bin/pip install --progress-bar off -r $APP/requirements.txt
  echo "$REQ" > $APP/.req-hash
fi

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

if [ -n "$WITH_AI" ]; then
  say "Installing the local AI model (Ollama + qwen2.5-coder:1.5b, about 1 GB download)"
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama
  sleep 3; ollama pull qwen2.5-coder:1.5b
  sed -i 's|^# AI_BASE_URL=.*|AI_BASE_URL=http://127.0.0.1:11434/v1|; s|^# AI_MODEL=.*|AI_MODEL=qwen2.5-coder:1.5b|' $ENVF
fi

if [ -n "$IMPORT" ]; then
  say "Bringing in data from the old computer"
  systemctl stop $SVC 2>/dev/null || true
  (set -a; . $ENVF; set +a; cd $APP && .venv/bin/python launcher.py --import "$IMPORT")
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
# astra-arena: open | status | start | stop | restart | logs | backup | doctor | update | package | reset-password | remove
URL=http://127.0.0.1:8765/
tok() { sudo cat /var/lib/astra-arena/panel.token 2>/dev/null; }
py() { sudo bash -c "set -a; . /etc/astra-arena.env; cd /opt/astra-arena && .venv/bin/python $*"; }
case "${1:-open}" in
  open)   # its own app window when Chrome/Chromium/Edge is installed, otherwise a browser tab
    for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
      command -v "$b" >/dev/null && { "$b" --app="$URL" --class=astra-arena --window-size=1180,860 >/dev/null 2>&1 & exit 0; }
    done
    xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in a browser on this computer." ;;
  status) systemctl status astra-arena --no-pager ;;
  start|stop|restart) sudo systemctl "$1" astra-arena ;;
  logs) sudo journalctl -u astra-arena -n 100 --no-pager; sudo tail -n 60 /var/lib/astra-arena/logs/*.log ;;
  backup) py -m app.manage backup ;;
  doctor) curl -s -H "X-Token: $(tok)" ${URL}api/diagnose | python3 -c 'import json,sys
for c in json.load(sys.stdin): print({"ok":"[ OK ]","warn":"[WARN]","err":"[FAIL]"}[c["level"]], c["name"]+":", c["detail"], ("\n        Fix: "+c["fix"]) if c["fix"] else "")' \
          || echo "The control panel is not answering. Try: astra-arena restart" ;;
  update) curl -s -X POST -H "X-Token: $(tok)" -H "Content-Type: application/json" -d '{"apply": true}' ${URL}api/update; echo ;;
  package) py -c "'import launcher; print(launcher.EXPORTS / launcher.make_package())'" ;;
  reset-password) py -m app.manage reset-password "${2:-admin}" ;;
  remove) sudo /opt/astra-arena/ubuntu/install.sh --uninstall ;;
  *) echo "usage: astra-arena open|status|start|stop|restart|logs|backup|doctor|update|package|reset-password [user]|remove" ;;
esac
EOF
chmod 755 /usr/local/bin/astra-arena
install -Dm644 $APP/ubuntu/astra-arena.svg /usr/share/icons/hicolor/scalable/apps/astra-arena.svg
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
cat > /usr/share/applications/astra-arena.desktop <<EOF
[Desktop Entry]
Name=ASTRA Code Arena
GenericName=Coding Contest Platform
Comment=Run, schedule and watch the campus coding platform
Exec=/usr/local/bin/astra-arena open
Icon=astra-arena
Terminal=false
Type=Application
StartupWMClass=astra-arena
Categories=Education;Development;
Keywords=contest;coding;judge;astra;pesitm;
EOF

say "Keeping the computer awake (a sleeping PC drops every student)"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true

if [ -z "$UPDATE" ]; then
  say "Setting up a fixed network address (the student link never changes)"
  systemctl stop $SVC 2>/dev/null || true
  (set -a; . $ENVF; set +a; cd $APP && .venv/bin/python launcher.py --fixed-ip) || echo "Fixed address skipped: the normal address is used."
fi

systemctl daemon-reload
systemctl enable $SVC >/dev/null
systemctl restart $SVC
PORT=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('port',8000))" 2>/dev/null || echo 8000)
command -v ufw >/dev/null && ufw status | grep -q "Status: active" && ufw allow "$PORT/tcp" >/dev/null || true

if [ -n "$UPDATE" ]; then
  say "Checking the new version"
  VER=$(cat $APP/.installed-version); RUNNING=$(python3 -c "import json;print(int(json.load(open('$DATA/launcher.json')).get('running',True)))" 2>/dev/null || echo 1)
  ok=""
  for i in $(seq 1 45); do
    curl -fs http://127.0.0.1:8765/api/ping >/dev/null && { [ "$RUNNING" = 0 ] || curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null; } && { ok=1; break; }
    sleep 2
  done
  if [ -z "$ok" ]; then
    echo "The new version did not start: restoring the previous version."
    rsync -a --delete --exclude .venv $APP.prev/ $APP/
    $APP/.venv/bin/pip install -q -r $APP/requirements.txt || true
    systemctl restart $SVC
    echo "{\"ok\": false, \"version\": \"$VER\", \"at\": $(date +%s), \"message\": \"Version ${VER:0:12} did not start, so the previous version was restored automatically.\"}" > $DATA/update-status.json
    echo "##FAILED: the new version did not start, so the previous version was restored."; exit 1
  fi
  echo "{\"ok\": true, \"version\": \"$VER\", \"at\": $(date +%s), \"message\": \"Updated to ${VER:0:12}.\"}" > $DATA/update-status.json
  say "Done. Updated to ${VER:0:12}."
  exit 0
fi

for i in $(seq 1 30); do curl -s -H "X-Token: $(cat $DATA/panel.token 2>/dev/null)" http://127.0.0.1:8765/api/status -o /tmp/astra-st.json && grep -q student_url /tmp/astra-st.json && break; sleep 1; done
STUDENTS=$(python3 -c "import json;print(json.load(open('/tmp/astra-st.json'))['student_url'])" 2>/dev/null || echo "http://$(hostname -I | awk '{print $1}'):8000/")
PW=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('first_password') or '')" 2>/dev/null || true)
say "Done. ASTRA Code Arena is running and starts by itself after every reboot."
echo "  Control panel (this PC): open 'ASTRA Code Arena' from the app menu, or run: astra-arena"
echo "  Students:                $STUDENTS"
echo "  Check health:            astra-arena doctor"
[ -n "$PW" ] && echo "  First admin login:       admin / $PW   (you must change it; the organiser link is in the control panel)"
exit 0
