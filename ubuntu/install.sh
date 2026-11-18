#!/usr/bin/env bash
# ASTRA Code Arena: one-command install for Ubuntu 22.04 / 24.04 (desktop or server).
#   sudo ./ubuntu/install.sh            install or update (keeps all data)
#   sudo ./ubuntu/install.sh --with-ai  also install the local AI model (Prompt challenges, AI Studio); needs ~1.5 GB more RAM
#   sudo ./ubuntu/install.sh --import FILE   bring all data from an old computer (a move package from its control panel)
#   sudo ./ubuntu/install.sh --uninstall [--purge]   remove the app (data kept unless --purge; a final move package is always kept)
# Safe to run again at any time: every step checks what is already there, retries downloads, and repairs what is broken.
# Only the essentials (Python, the app, the service) can stop the install; Node.js, the AI model, the icon and the firewall
# fall back or are skipped with a WARNING, and can be fixed later by running this again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin   # pkexec gives a short PATH without /usr/local/bin
export DEBIAN_FRONTEND=noninteractive
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/astra-arena DATA=/var/lib/astra-arena ENVF=/etc/astra-arena.env SVC=astra-arena
STEP="" WARNINGS=()
say() { STEP="$*"; printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }   # the setup app reads these lines for its progress bar
warn() { echo "WARNING: $*"; WARNINGS+=("$*"); }
die() { echo "##FAILED: $*"; exit 1; }
retry() {  # retry N command...  (waits a little longer after each failure)
  local n=$1 i; shift
  for i in $(seq 1 "$n"); do
    "$@" && return 0
    [ "$i" = "$n" ] || { echo "  attempt $i of $n failed; trying again in $((i * 5)) s"; sleep $((i * 5)); }
  done
  return 1
}
trap 'echo "##FAILED: an unexpected problem during \"$STEP\" (line $LINENO). Press Try again; if it repeats, send the details below to the organiser."' ERR

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
mkdir -p /run
exec 9>/run/astra-arena-install.lock   # one install/update/uninstall at a time
flock -n 9 || die "another install or update is already running. Wait for it to finish."
[ -z "$IMPORT" ] || [ -f "$IMPORT" ] || die "move package not found: $IMPORT"

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
  systemctl daemon-reload || true
  if [ -n "$PURGE" ]; then rm -rf $DATA $ENVF; echo "Removed ASTRA Arena and all its data from this computer."
  else echo "Removed ASTRA Arena. Data and move packages are kept in $DATA (sudo $0 --uninstall --purge deletes them)."; fi
  exit 0
fi

# ---------------------------------------------------------------- 1. this computer
say "Checking this computer"
for d in /usr/local/bin /usr/share/applications /usr/share/icons/hicolor/scalable/apps /opt /var/tmp /var/lib; do   # some PCs lack these
  [ -d "$d" ] || { rm -f "$d" 2>/dev/null || true; mkdir -p "$d"; }
done
FREE_GB=$(df -BG --output=avail /var | tail -1 | tr -dc 0-9)
echo "Free disk: ${FREE_GB} GB, RAM: $(free -g | awk '/Mem:/{print $2+1}') GB, cores: $(nproc)"
[ "${FREE_GB:-0}" -ge 20 ] || die "only ${FREE_GB} GB free on /var. ASTRA Arena needs at least 20 GB (40 GB recommended)."
[ "$FREE_GB" -ge 40 ] || echo "Note: ${FREE_GB} GB free. Works, but 40 GB+ is recommended; old backups may need clearing after a semester."

# ---------------------------------------------------------------- 2. system packages
say "Installing compilers and Python (needs internet this one time)"
APT="apt-get -y -q -o DPkg::Lock::Timeout=600 -o Acquire::Retries=3"   # waits if Software Updater is busy
PKGS="python3 python3-venv python3-pip gcc g++ default-jdk-headless sqlite3 rsync curl xz-utils git ca-certificates"
have_tools() { command -v python3 gcc g++ javac rsync curl git xz >/dev/null && python3 -c 'import venv, ensurepip' 2>/dev/null; }
if [ -z "$UPDATE" ] || ! have_tools; then
  dpkg --configure -a >/dev/null 2>&1 || true                          # finish any install that was interrupted earlier
  retry 3 $APT update || echo "Note: some software sources did not refresh (often an unrelated third-party source); continuing."
  if ! $APT install $PKGS; then
    $APT -f install || true                                            # repair broken dependencies, then one by one
    for p in $PKGS; do $APT install "$p" || echo "  could not install $p"; done
  fi
fi
command -v python3 >/dev/null && python3 -c 'import venv, ensurepip' 2>/dev/null \
  || die "Python could not be installed. Connect this PC to the internet (the Ubuntu servers must be reachable) and press Try again."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || die "this Ubuntu has Python $(python3 -V 2>&1 | cut -d' ' -f2), but ASTRA Arena needs Python 3.10 or newer. Use Ubuntu 22.04 or 24.04."
command -v rsync >/dev/null && command -v curl >/dev/null || die "rsync/curl could not be installed. Connect this PC to the internet and press Try again."
command -v gcc >/dev/null && command -v g++ >/dev/null || warn "C/C++ compiler missing: C and C++ answers will not run until it installs (run the installer again with internet)."
command -v javac >/dev/null || warn "Java missing: Java answers will not run until it installs (run the installer again with internet)."

# ---------------------------------------------------------------- 3. Node.js
say "Installing Node.js"
node_ok() { node -e 'process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)' 2>/dev/null; }
install_node() {   # Ubuntu 22.04's own Node.js is v12 (too old for modern JavaScript): use the official Node 20 LTS build
  node_ok && return 0
  local v=v20.18.1 arch sum dir
  arch=$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/')
  case "$arch" in   # pinned checksums: a tampered or broken download is refused
    x64) sum=c6fa75c841cbffac851678a472f2a5bd612fff8308ef39236190e1f8dbb0e567 ;;
    arm64) sum=44d1ffc5905c005ace4515ca6f8c090c4c7cfce3a9a67df0dba35c727590b8f6 ;;
    *) echo "  unsupported processor: $arch"; return 1 ;;
  esac
  dir=/opt/node-$v-linux-$arch
  if [ ! -x "$dir/bin/node" ]; then
    retry 3 curl -fL -sS --connect-timeout 20 --max-time 600 -o /tmp/node.tar.xz "https://nodejs.org/dist/$v/node-$v-linux-$arch.tar.xz" || return 1
    echo "$sum  /tmp/node.tar.xz" | sha256sum -c --quiet || { rm -f /tmp/node.tar.xz; echo "  download did not match its checksum"; return 1; }
    rm -rf "$dir"; tar -xJf /tmp/node.tar.xz -C /opt || return 1
    rm -f /tmp/node.tar.xz
  fi
  ln -sfn "$dir/bin/node" /usr/local/bin/node
  node_ok
}
if install_node; then echo "Node.js $(node -v)"
else
  $APT install nodejs >/dev/null 2>&1 || true   # old but better than nothing
  if command -v node >/dev/null; then warn "Node.js 20 could not be downloaded; using Ubuntu's older Node.js $(node -v). Modern JavaScript answers may fail. Run the installer again later to fix."
  else warn "Node.js could not be installed: JavaScript answers will not run. Everything else works. Run the installer again later to fix."; fi
fi

# ---------------------------------------------------------------- 4. the app
say "Copying the app to $APP (data stays in $DATA)"
mkdir -p $APP $DATA/backups
[ -z "$UPDATE" ] || rsync -a --delete --exclude .venv $APP/ $APP.prev/   # the running version, kept for rollback
rsync -a --delete --exclude data --exclude logs --exclude .venv --exclude '__pycache__' --exclude .git --exclude .req-hash "$SRC"/ $APP/
(cd "$SRC" && git rev-parse HEAD 2>/dev/null || date +%s) > $APP/.installed-version
[ -f $APP/launcher.py ] && [ -f $APP/requirements.txt ] || die "the app files did not copy correctly from $SRC. Download the code again and retry."

# ---------------------------------------------------------------- 5. Python packages
say "Installing Python packages"
PY=$APP/.venv/bin/python
make_venv() { rm -rf $APP/.venv; python3 -m venv $APP/.venv; }
pip_install() { $PY -m pip install --progress-bar off --retries 5 --timeout 60 --upgrade pip >/dev/null 2>&1 || true
                $PY -m pip install --progress-bar off --retries 5 --timeout 60 -r $APP/requirements.txt; }
pkgs_ok() { $PY -c 'import fastapi, uvicorn, multipart, pymupdf, pydantic' 2>/dev/null; }
{ [ -x $PY ] && $PY -c 'import sys' 2>/dev/null; } || make_venv || die "could not create the Python environment. Run: sudo apt-get install -y python3-venv, then press Try again."
REQ=$(sha256sum $APP/requirements.txt | cut -c1-64)
if [ "$(cat $APP/.req-hash 2>/dev/null)" != "$REQ" ] || ! pkgs_ok; then   # only when the package list changed or something is missing
  retry 3 pip_install || { echo "  rebuilding the Python environment from scratch"; make_venv && retry 2 pip_install; } \
    || die "Python packages could not be downloaded. Check the internet connection (pypi.org must be reachable) and press Try again."
  if ! pkgs_ok; then   # pip believes they are installed but they do not load (half-deleted files): rebuild from scratch
    echo "  packages do not load: rebuilding the Python environment from scratch"
    make_venv && retry 2 pip_install && pkgs_ok || die "Python packages could not be set up. Check the internet connection and press Try again."
  fi
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
grep -q '^DATA_DIR=' $ENVF || echo "DATA_DIR=$DATA" >> $ENVF   # a hand-edited file must still point at the data

# ---------------------------------------------------------------- 6. AI model (optional: never stops the install)
install_ai() {
  local model=qwen2.5-coder:1.5b i
  if ! command -v ollama >/dev/null; then
    retry 3 curl -fL -sS --connect-timeout 20 -o /tmp/ollama-install.sh https://ollama.com/install.sh || return 1
    [ -s /tmp/ollama-install.sh ] || return 1
    sh /tmp/ollama-install.sh || return 1
  fi
  systemctl enable --now ollama >/dev/null 2>&1 || true
  for i in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; [ "$i" = 5 ] && { systemctl restart ollama >/dev/null 2>&1 || (nohup ollama serve >/var/log/ollama-astra.log 2>&1 &); }; sleep 2; done
  ollama list >/dev/null 2>&1 || { echo "  the AI service did not start"; return 1; }
  ollama list | grep -q "^$model" || retry 3 ollama pull $model || return 1
  sed -i '/^#* *AI_BASE_URL=/d; /^#* *AI_MODEL=/d' $ENVF
  printf 'AI_BASE_URL=http://127.0.0.1:11434/v1\nAI_MODEL=%s\n' $model >> $ENVF
}
if [ -n "$WITH_AI" ]; then
  say "Installing the local AI model (Ollama + qwen2.5-coder:1.5b, about 1 GB download)"
  install_ai || warn "The AI model could not be installed (slow or blocked download). Everything else works; Prompt challenges and AI Studio stay off. Run the installer again with the AI option to retry."
fi

if [ -n "$IMPORT" ]; then
  say "Bringing in data from the old computer"
  systemctl stop $SVC 2>/dev/null || true
  (set -a; . $ENVF; set +a; cd $APP && $PY launcher.py --import "$IMPORT") || die "the move package could not be imported (see the lines above). Nothing was replaced."
fi

# ---------------------------------------------------------------- 7. background service, command, menu icon
say "Creating the background service (starts at boot, restarts on crash)"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=ASTRA Code Arena
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=$ENVF
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
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
{ install -Dm644 $APP/ubuntu/astra-arena.svg /usr/share/icons/hicolor/scalable/apps/astra-arena.svg && cat > /usr/share/applications/astra-arena.desktop <<EOF
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
} || warn "The app-menu icon could not be created. Open the control panel with the command: astra-arena"
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

say "Keeping the computer awake (a sleeping PC drops every student)"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || warn "Could not switch off sleep: set Power → Automatic Suspend to Off in Ubuntu Settings."

if [ -z "$UPDATE" ]; then
  say "Setting up a fixed network address (the student link never changes)"
  systemctl stop $SVC 2>/dev/null || true
  (set -a; . $ENVF; set +a; cd $APP && $PY launcher.py --fixed-ip) || echo "Fixed address skipped: the normal address is used."
fi

# ---------------------------------------------------------------- 8. start and verify
panel_up() { curl -fs --max-time 3 http://127.0.0.1:8765/api/ping >/dev/null; }
wait_panel() { local i; for i in $(seq 1 "$1"); do panel_up && return 0; sleep 2; done; return 1; }
systemctl daemon-reload
systemctl enable $SVC >/dev/null 2>&1 || warn "Could not enable start-at-boot; switch it on in the control panel under Keep it running."
systemctl restart $SVC || true
PORT=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('port',8000))" 2>/dev/null || echo 8000)
command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active" && ufw allow "$PORT/tcp" >/dev/null 2>&1 || true

if [ -n "$UPDATE" ]; then
  say "Checking the new version"
  VER=$(cat $APP/.installed-version); RUNNING=$(python3 -c "import json;print(int(json.load(open('$DATA/launcher.json')).get('running',True)))" 2>/dev/null || echo 1)
  ok=""
  for i in $(seq 1 45); do
    panel_up && { [ "$RUNNING" = 0 ] || curl -fs --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; } && { ok=1; break; }
    sleep 2
  done
  if [ -z "$ok" ]; then
    echo "The new version did not start: restoring the previous version."
    rsync -a --delete --exclude .venv $APP.prev/ $APP/
    $PY -m pip install -q -r $APP/requirements.txt || true
    systemctl restart $SVC || true
    echo "{\"ok\": false, \"version\": \"$VER\", \"at\": $(date +%s), \"message\": \"Version ${VER:0:12} did not start, so the previous version was restored automatically.\"}" > $DATA/update-status.json
    die "the new version did not start, so the previous version was restored."
  fi
  echo "{\"ok\": true, \"version\": \"$VER\", \"at\": $(date +%s), \"message\": \"Updated to ${VER:0:12}.\"}" > $DATA/update-status.json
  say "Done. Updated to ${VER:0:12}."
  exit 0
fi

if ! wait_panel 30; then   # one self-repair attempt: free a stuck port, restart, look again
  echo "The service did not answer yet: restarting it once more."
  systemctl stop $SVC 2>/dev/null || true
  command -v fuser >/dev/null && fuser -k 8765/tcp >/dev/null 2>&1 || true
  systemctl start $SVC || true
  wait_panel 30 || { journalctl -u $SVC -n 40 --no-pager 2>/dev/null || true; tail -n 30 $DATA/logs/*.log 2>/dev/null || true
                     die "everything is installed, but the service did not start. The lines above say why. Try: sudo systemctl restart $SVC"; }
fi

for i in $(seq 1 20); do curl -s --max-time 5 -H "X-Token: $(cat $DATA/panel.token 2>/dev/null)" http://127.0.0.1:8765/api/status -o /tmp/astra-st.json && grep -q student_url /tmp/astra-st.json && break; sleep 1; done
STUDENTS=$(python3 -c "import json;print(json.load(open('/tmp/astra-st.json'))['student_url'])" 2>/dev/null || echo "http://$(hostname -I | awk '{print $1}'):$PORT/")
PW=$(python3 -c "import json;print(json.load(open('$DATA/launcher.json')).get('first_password') or '')" 2>/dev/null || true)
say "Done. ASTRA Code Arena is running and starts by itself after every reboot."
echo "  Control panel (this PC): open 'ASTRA Code Arena' from the app menu, or run: astra-arena"
echo "  Students:                $STUDENTS"
echo "  Check health:            astra-arena doctor"
[ -n "$PW" ] && echo "  First admin login:       admin / $PW   (you must change it; the organiser link is in the control panel)"
if [ ${#WARNINGS[@]} -gt 0 ]; then
  echo; echo "Finished with ${#WARNINGS[@]} note(s). The platform works; these parts can be fixed later by running the installer again:"
  for w in "${WARNINGS[@]}"; do echo "NOTE: $w"; done
fi
exit 0
