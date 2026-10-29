#!/bin/sh
# BiliPlayer launcher (macOS / Linux).
# Double-click BiliPlayer.command on macOS, or run ./start.sh in a terminal.
cd "$(dirname "$0")" || exit 1

for p in python3 python; do
  if command -v "$p" >/dev/null 2>&1; then
    exec "$p" launch.py
  fi
done

echo "BiliPlayer needs Python 3. Install it and run this again:"
echo "  macOS  https://www.python.org/downloads/   (or: brew install python)"
echo "  Linux  sudo apt install python3"
exit 1
