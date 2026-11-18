#!/usr/bin/env bash
# Pull-based auto-update: the college server checks GitHub and updates itself.
# Installed by ./deploy.sh --enable-autoupdate (runs every 5 minutes via cron). Safe by design:
#   * does nothing if there are no new commits
#   * never updates while a competition is live or paused (it tries again 5 minutes later)
#   * backs up the database before every update (deploy.sh does this)
#   * only fast-forwards: local edits on the server are never overwritten
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=logs/auto-update.log; mkdir -p logs
exec >>"$LOG" 2>&1
BRANCH="${AUTO_UPDATE_BRANCH:-main}"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
git fetch --quiet origin "$BRANCH" || { echo "$(ts) fetch failed (no internet or GitHub unreachable); will retry"; exit 0; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ] && exit 0
PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2); PORT="${PORT:-8000}"
if curl -fs "http://127.0.0.1:${PORT}/api/public/competitions" | grep -qE '"display_status": ?"(Live|Paused)"'; then
  echo "$(ts) update available but a competition is live; postponing"; exit 0
fi
echo "$(ts) updating $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$BRANCH")"
git merge --ff-only "origin/$BRANCH" || { echo "$(ts) cannot fast-forward (local changes on the server?); skipped"; exit 1; }
AUTO=1 ./deploy.sh && echo "$(ts) update done" || echo "$(ts) update FAILED; previous containers keep running if the build failed"
