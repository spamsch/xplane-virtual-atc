#!/bin/bash
# Double-click to start X-Plane Virtual ATC.
# The desktop app opens in its own window. Keep THIS window open while you fly —
# quitting the app (or closing this window) stops everything.

cd "$(dirname "$0")" || exit 1

APP="X-Plane Virtual ATC.app"

# macOS tags downloaded files as "quarantined", which makes Gatekeeper block the
# app. Clear it for this whole folder so the app launches cleanly.
xattr -dr com.apple.quarantine . >/dev/null 2>&1 || true

# Make Homebrew's tools visible (Apple Silicon or Intel) so python3/claude resolve.
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ]   && eval "$(/usr/local/bin/brew shellenv)"

# The backend (the part that talks to X-Plane and Claude) needs Python.
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python isn't installed yet."
  echo "Please right-click  setup.command  and choose Open, let it finish, then"
  echo "double-click  start.command  again."
  echo
  echo "Press Return to close."
  read -r _
  exit 1
fi

# First run: build the isolated Python environment and install dependencies.
if [ ! -x .venv/bin/python ]; then
  echo "First-time setup: preparing the engine. This can take a few minutes…"
  python3 -m venv .venv || { echo "Could not create the Python environment."; read -r _; exit 1; }
  .venv/bin/pip install -U pip >/dev/null
  if ! .venv/bin/pip install -r requirements.txt; then
    echo "Dependency install failed — check your internet connection and try again."
    read -r _
    exit 1
  fi
fi

# Config file (you'll paste your ElevenLabs key in the app, not here).
[ -f .env ] || cp .env.example .env

echo "Starting X-Plane Virtual ATC…  the app window will open in a moment."
echo "Keep this window open while you fly. Quit the app to stop."
echo

# Start the backend, and stop it automatically when this script exits.
.venv/bin/python backend/server.py &
BACKEND=$!
trap 'kill "$BACKEND" 2>/dev/null' EXIT INT TERM

# Open the app and block until the user quits it; then the trap stops the backend.
open -W "$APP"
