#!/bin/sh
# macOS: double-clicking a .command file runs it in Terminal.
# Keep this window open while BiliPlayer is running; close it to quit.
cd "$(dirname "$0")" || exit 1
exec sh start.sh
