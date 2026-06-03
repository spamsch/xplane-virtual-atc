#!/bin/bash
# Double-click this file to install everything X-Plane Virtual ATC needs.
# It's safe to run more than once.

cd "$(dirname "$0")" || exit 1

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
    echo "✗ Homebrew install failed. Please try again with an internet connection."; exit 1; }
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ]   && eval "$(/usr/local/bin/brew shellenv)"
fi
echo "✓ Homebrew ready"

# 2) Python and Node.
echo "▶ Installing Python and Node…"
brew install python node >/dev/null || { echo "✗ Could not install Python/Node."; exit 1; }
echo "✓ Python and Node ready"

# 3) Claude (the controller's brain). Installed via Node.
if ! command -v claude >/dev/null 2>&1; then
  echo "▶ Installing Claude…"
  npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || \
    echo "  (couldn't auto-install Claude — see the install guide if voice/ATC doesn't work)"
fi
command -v claude >/dev/null 2>&1 && echo "✓ Claude installed"

# 4) The app's Python pieces (in an isolated folder, nothing system-wide).
echo "▶ Setting up the app…"
python3 -m venv .venv || { echo "✗ Could not create the Python environment."; exit 1; }
.venv/bin/pip install -U pip >/dev/null
.venv/bin/pip install -r requirements.txt >/dev/null || { echo "✗ Dependency install failed."; exit 1; }

# 5) The app's screen pieces.
( cd ui && npm install >/dev/null 2>&1 ) || { echo "✗ UI dependency install failed."; exit 1; }

# 6) Config file (you'll paste your ElevenLabs key in the app, not here).
[ -f .env ] || cp .env.example .env
echo "✓ App ready"
echo
echo "────────────────────────────────────────────────────────"
echo "  Almost done — two quick things:"
echo
echo "  1) Sign in to Claude. In this window, type:  claude"
echo "     …press Return, and follow the browser sign-in. Then"
echo "     type  exit  to leave it."
echo
echo "  2) To start the app from now on, double-click:"
echo "     start.command"
echo "────────────────────────────────────────────────────────"
echo
echo "Press Return to close this window."
read -r _
