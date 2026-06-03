#!/bin/bash
# Run this ONCE, the first time, from Terminal: open Terminal, drag this file
# into the window, and press Return. (An unsigned .command can't be launched by
# double-click or right-click → Open until its quarantine flag is cleared, which
# is exactly what this script does below.) Safe to run again later.

cd "$(dirname "$0")" || exit 1

# Remove the macOS "downloaded from the internet" quarantine so Gatekeeper stops
# blocking things. Clear the whole folder, and — explicitly — the app and
# start.command, so a normal double-click on start.command works from now on.
xattr -dr com.apple.quarantine . >/dev/null 2>&1 || true
xattr -dr com.apple.quarantine "X-Plane Virtual ATC.app" >/dev/null 2>&1 || true
xattr -dr com.apple.quarantine "start.command" >/dev/null 2>&1 || true

echo "────────────────────────────────────────────────────────"
echo "  X-Plane Virtual ATC — Setup"
echo "  This installs the tools the app needs. It can take a"
echo "  few minutes and may ask for your Mac password once."
echo "────────────────────────────────────────────────────────"
echo

# Make Homebrew visible if it's already installed (Apple Silicon or Intel).
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ]   && eval "$(/usr/local/bin/brew shellenv)"

# 1) Homebrew — the installer that brings everything else (and Apple's dev tools).
if ! command -v brew >/dev/null 2>&1; then
  echo "▶ Installing Homebrew (follow any prompts)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || {
    echo "✗ Homebrew install failed. Please try again with an internet connection."; read -r _; exit 1; }
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ]   && eval "$(/usr/local/bin/brew shellenv)"
fi
echo "✓ Homebrew ready"

# 2) Python (runs the backend) and Node (only needed to install Claude, below).
echo "▶ Installing Python and Node…"
brew install python node >/dev/null || { echo "✗ Could not install Python/Node."; read -r _; exit 1; }
echo "✓ Python and Node ready"

# 3) Claude (the controller's brain).
if ! command -v claude >/dev/null 2>&1; then
  echo "▶ Installing Claude…"
  npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || \
    echo "  (couldn't auto-install Claude — see the project's install guide if ATC stays silent)"
fi
command -v claude >/dev/null 2>&1 && echo "✓ Claude installed"

# 4) The app's Python environment (isolated in this folder, nothing system-wide).
echo "▶ Preparing the app's engine…"
python3 -m venv .venv || { echo "✗ Could not create the Python environment."; read -r _; exit 1; }
.venv/bin/pip install -U pip >/dev/null
.venv/bin/pip install -r requirements.txt >/dev/null || { echo "✗ Dependency install failed."; read -r _; exit 1; }
[ -f .env ] || cp .env.example .env
echo "✓ App ready"

echo
echo "────────────────────────────────────────────────────────"
echo "  Almost done — one quick thing:"
echo
echo "  Sign in to Claude. In this window, type:  claude"
echo "  …press Return, follow the browser sign-in, then type  exit."
echo
echo "  To start the app from now on, double-click:  start.command"
echo "────────────────────────────────────────────────────────"
echo
echo "Press Return to close this window."
read -r _
