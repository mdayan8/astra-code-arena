#!/usr/bin/env bash
# ASTRA Code Arena: run this to install, update or open the platform.
#   Terminal:  ./start.sh            Files app: right-click start.sh -> Run as a Program
#   ./start.sh --setup   always show the setup window (repair / reinstall)
cd "$(dirname "$0")" && exec python3 ubuntu/setup_ui.py "$@"
