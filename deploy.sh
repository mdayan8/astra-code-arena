#!/usr/bin/env bash
# ASTRA Code Arena: one-command install / update on the college server (Ubuntu/Debian).
#   ./deploy.sh                 one command, no questions: installs or updates on port 8000, prints the links + first admin password
#   ./deploy.sh --with-ai       also run a small local AI model (qwen2.5-coder 1.5B, ~1.5 GB RAM while in use)
#   ./deploy.sh --bundle        on a machine WITH internet: build everything into astra-offline.tar.gz
#   ./deploy.sh --offline       on the server WITHOUT internet: install from astra-offline.tar.gz (copied next to this script)
#   ./deploy.sh --reconfigure   change the IP / port later
#   ./deploy.sh --enable-autoupdate   pull new versions from GitHub automatically (every 5 min, never during a live round)
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-}"; WITH_AI=0; [ "$MODE" = "--with-ai" ] && WITH_AI=1
AI_MODEL_DEFAULT="qwen2.5-coder:1.5b"
BUNDLE="astra-offline.tar.gz"
say() { printf "\n\033[1;35m==> %s\033[0m\n" "$*"; }

if [ "$MODE" = "--enable-autoupdate" ]; then
  git rev-parse --git-dir >/dev/null 2>&1 || { echo "This folder is not a git clone. Clone it from GitHub first (see docs/DEPLOYMENT.md)."; exit 1; }
  LINE="*/5 * * * * $PWD/scripts/auto-update.sh"
  ( crontab -l 2>/dev/null | grep -v 'scripts/auto-update.sh'; echo "$LINE" ) | crontab -
  echo "Auto-update enabled: every 5 minutes this server pulls GitHub branch ${AUTO_UPDATE_BRANCH:-main} and redeploys (log: logs/auto-update.log)."
  echo "Disable with: crontab -l | grep -v auto-update.sh | crontab -"
  exit 0
fi

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  if [ "$MODE" = "--offline" ]; then echo "Docker is not installed. Install the docker.io + docker-compose-v2 .deb packages first (see docs/DEPLOYMENT.md)."; exit 1; fi
  sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
  sudo systemctl enable --now docker
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

if [ "$MODE" = "--bundle" ]; then
  say "Building the offline bundle (needs internet once)"
  $DOCKER compose build
  $DOCKER pull ollama/ollama:latest
  $DOCKER volume create astra-code-arena_ollama-models >/dev/null
  $DOCKER run --rm -v astra-code-arena_ollama-models:/root/.ollama --entrypoint sh ollama/ollama:latest -c "ollama serve & sleep 4; ollama pull ${AI_MODEL_DEFAULT}"
  $DOCKER run --rm -v astra-code-arena_ollama-models:/m -v "$PWD":/out --entrypoint tar ollama/ollama:latest czf /out/ai-models.tgz -C /m .
  $DOCKER save astra-arena:latest ollama/ollama:latest | gzip > "$BUNDLE"
  echo "Done. Copy this whole folder (with $BUNDLE and ai-models.tgz) to the server and run: ./deploy.sh --offline"
  exit 0
fi

say "Configuration (.env)"
if [ "${AUTO:-0}" = 1 ] && [ ! -f .env ]; then echo "No .env yet; run ./deploy.sh once by hand first."; exit 1; fi
if [ ! -f .env ] || [ "$MODE" = "--reconfigure" ]; then
  [ -f .env ] || cp .env.example .env
  BIND="${APP_BIND:-0.0.0.0}"; PORT="${APP_PORT:-8000}"
  if [ "$MODE" = "--reconfigure" ]; then   # only asks when you explicitly want to change IP/port
    echo "  Server IP addresses: $(hostname -I 2>/dev/null || echo unknown)"
    read -r -p "IP to serve on (press Enter for all addresses) [${BIND}]: " B2; BIND="${B2:-$BIND}"
    read -r -p "Port [${PORT}]: " P2; PORT="${P2:-$PORT}"
  fi
  CORES=$(nproc 2>/dev/null || echo 4); WORKERS=$(( CORES > 3 ? CORES - 2 : 1 ))
  sed -i "s/^APP_BIND=.*/APP_BIND=${BIND}/; s/^APP_PORT=.*/APP_PORT=${PORT}/; s/^JUDGE_WORKERS=.*/JUDGE_WORKERS=${WORKERS}/" .env
  grep -q '^APP_BIND=' .env || echo "APP_BIND=${BIND}" >> .env
  echo "  APP_BIND=${BIND}  APP_PORT=${PORT}  JUDGE_WORKERS=${WORKERS}"
else
  echo "  keeping existing .env (run ./deploy.sh --reconfigure to change IP/port)"
fi
grep -q '^AI_BASE_URL=http://ollama' .env 2>/dev/null && WITH_AI=1   # keep the AI service on updates
if [ "$WITH_AI" = 1 ] || { [ "$MODE" = "--offline" ] && [ -f ai-models.tgz ]; }; then
  WITH_AI=1
  sed -i "s|^AI_BASE_URL=.*|AI_BASE_URL=http://ollama:11434/v1|; s|^AI_MODEL=.*|AI_MODEL=${AI_MODEL_DEFAULT}|" .env
fi
set -a; . ./.env; set +a
PORT="${APP_PORT:-8000}"

say "Backup before update (if already running)"
if $DOCKER compose ps --services --filter status=running 2>/dev/null | grep -q web; then
  $DOCKER compose exec -T web python -m app.manage backup || true
fi

PROFILE=""; [ "$WITH_AI" = 1 ] && PROFILE="--profile ai"
if [ "$MODE" = "--offline" ]; then
  say "Loading images from $BUNDLE (no internet needed)"
  [ -f "$BUNDLE" ] || { echo "$BUNDLE not found next to deploy.sh"; exit 1; }
  gunzip -c "$BUNDLE" | $DOCKER load
  if [ -f ai-models.tgz ]; then
    $DOCKER volume create astra-code-arena_ollama-models >/dev/null
    $DOCKER run --rm -v astra-code-arena_ollama-models:/m -v "$PWD":/in --entrypoint tar ollama/ollama:latest xzf /in/ai-models.tgz -C /m
  fi
  $DOCKER compose $PROFILE up -d --no-build
else
  say "Build and start (first time takes several minutes)"
  $DOCKER compose $PROFILE up -d --build
fi
for i in $(seq 1 60); do curl -fs "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fs "http://127.0.0.1:${PORT}/api/health" >/dev/null || { echo "Web server did not start. See: $DOCKER compose logs web"; exit 1; }
if [ "$WITH_AI" = 1 ] && [ "$MODE" != "--offline" ]; then
  say "Downloading AI model ${AI_MODEL_DEFAULT} (about 1 GB, one time)"
  $DOCKER compose exec -T ollama ollama pull "${AI_MODEL_DEFAULT}"
fi

say "First Super Admin"
CREDS=$($DOCKER compose exec -T web python -m app.manage auto-admin | tr -d '\r')
if echo "$CREDS" | grep -q ADMIN_PASSWORD; then
  ADMIN_PW=$(echo "$CREDS" | grep ADMIN_PASSWORD | cut -d= -f2)
else
  echo "  admin account already exists"
fi

say "Done"
IP="${APP_BIND:-0.0.0.0}"; [ "$IP" = "0.0.0.0" ] && IP=$(hostname -I 2>/dev/null | awk '{print $1}'); IP="${IP:-<server-ip>}"
ROUTE=$($DOCKER compose exec -T web python -c "from app.main import ADMIN; print(ADMIN)" | tr -d '\r')
cat <<MSG

  Students  : http://${IP}:${PORT}/
  Organisers: http://${IP}:${PORT}/${ROUTE}/     (keep this private)
$( [ -n "${ADMIN_PW:-}" ] && printf '\n  First sign-in: username  admin   password  %s\n  (you will be asked to choose your own password; write this down now, it is shown only once)\n' "$ADMIN_PW" )

  Status    : $DOCKER compose ps        Logs: $DOCKER compose logs -f
  Stop      : $DOCKER compose down      (data is kept)
  Change IP / port: ./deploy.sh --reconfigure
MSG
