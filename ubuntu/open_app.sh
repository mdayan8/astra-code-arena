#!/usr/bin/env bash
# Opens ASTRA Code Arena as a desktop app.
#   1. its own native window (GTK + WebKit, part of Ubuntu): no browser at all
#   2. only if that cannot start: Chrome / Chromium / Edge / Brave in app mode
#   3. only if neither exists: the default browser
URL="${1:-http://127.0.0.1:8765/}"
HERE="$(dirname "$(readlink -f "$0")")"
LOG="${XDG_CACHE_HOME:-$HOME/.cache}/astra-arena-app.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null

if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v python3 >/dev/null; then
  # Start it straight away. If the window component is missing it exits at once with an error and we fall through.
  nohup python3 "$HERE/appwindow.py" "$URL" >"$LOG" 2>&1 &
  pid=$!
  for _ in 1 2 3 4; do   # 2 seconds: still running = window is up; exit 0 = the existing window was brought to the front
    sleep 0.5
    kill -0 "$pid" 2>/dev/null || { wait "$pid"; [ $? -eq 0 ] && exit 0; break; }
  done
  kill -0 "$pid" 2>/dev/null && exit 0
  echo "native window failed (see $LOG); trying a browser app window" >&2
fi

PROFILE="${XDG_CONFIG_HOME:-$HOME/.config}/astra-arena-app"
for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable brave-browser; do
  bin=$(command -v "$b" 2>/dev/null) || continue
  args=(--app="$URL" --class=astra-arena --name=astra-arena --no-first-run --no-default-browser-check --window-size=1200,880)
  case "$(readlink -f "$bin")" in *snap*) ;; *) args+=(--user-data-dir="$PROFILE") ;; esac   # snap browsers keep their own profile folder
  nohup "$bin" "${args[@]}" >/dev/null 2>&1 &
  exit 0
done

xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in a browser on this computer."
