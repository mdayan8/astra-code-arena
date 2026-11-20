#!/usr/bin/env bash
# Opens ASTRA Code Arena as its own app window (own icon, no tabs, no address bar), never as a browser tab if avoidable.
#   1. Chrome / Chromium / Edge / Brave in app mode, with a separate profile so it is a separate app in the dock
#   2. a small native window (GTK + WebKit) when no such browser is installed
#   3. the default browser, only as a last resort
URL="${1:-http://127.0.0.1:8765/}"
HERE="$(dirname "$(readlink -f "$0")")"
PROFILE="${XDG_CONFIG_HOME:-$HOME/.config}/astra-arena-app"

for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable brave-browser; do
  bin=$(command -v "$b" 2>/dev/null) || continue
  args=(--app="$URL" --class=astra-arena --name=astra-arena --no-first-run --no-default-browser-check --window-size=1200,880)
  # snap-packaged browsers cannot write outside ~/snap, so they keep their own profile folder
  case "$(readlink -f "$bin")" in *snap*) ;; *) args+=(--user-data-dir="$PROFILE") ;; esac
  nohup "$bin" "${args[@]}" >/dev/null 2>&1 &
  exit 0
done

if python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1")' 2>/dev/null \
   || python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.0")' 2>/dev/null; then
  nohup python3 "$HERE/appwindow.py" "$URL" >/dev/null 2>&1 &
  exit 0
fi

xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in a browser on this computer."
