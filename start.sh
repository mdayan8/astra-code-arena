#!/usr/bin/env bash
# ASTRA Code Arena: run this to install the platform, or to open the app once it is installed.
#   Terminal:  bash start.sh            Files app: right-click start.sh -> Run as a Program
#   bash start.sh --setup   always show the setup window (repair / reinstall)
cd "$(dirname "$0")" || exit 1
# Native Ubuntu window first (GTK is part of every Ubuntu desktop). If it cannot start, fall back to the web version.
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null; then
  python3 ubuntu/setup_gtk.py "$@" && exit 0
  echo "The native setup window could not start; opening the web version instead." >&2
fi
exec python3 ubuntu/setup_ui.py "$@"
