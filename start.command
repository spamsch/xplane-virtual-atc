#!/bin/bash
# Double-click this file to start X-Plane Virtual ATC.
# It opens in your web browser. Close this window (or press Ctrl-C) to stop.

cd "$(dirname "$0")" || exit 1

[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ]   && eval "$(/usr/local/bin/brew shellenv)"

if [ ! -x .venv/bin/python ]; then
  echo "It looks like setup hasn't run yet."
  echo "Please double-click  setup.command  first, then try again."
  echo
  echo "Press Return to close."
  read -r _
  exit 1
fi

echo "Starting X-Plane Virtual ATC…  (your browser will open in a moment)"
echo "Keep this window open while you fly. Close it to stop."
echo

# Stop the backend when this window/script is closed.
trap 'kill 0' EXIT INT TERM

# Backend (the part that talks to X-Plane and Claude).
.venv/bin/python backend/server.py &

# Give the UI a moment, then open the browser to it.
( sleep 4; open "http://localhost:1420" ) &

# The screen (web UI). Stays in the foreground; closing the window stops everything.
cd ui && npm run dev
