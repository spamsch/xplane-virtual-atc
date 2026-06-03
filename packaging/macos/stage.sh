#!/usr/bin/env bash
#
# Assemble the downloadable macOS release folder under dist/.
# Run from the repo root (the release workflow does this); it also works locally
# after `npm run tauri build -- --bundles app --target universal-apple-darwin`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

APP_NAME="X-Plane Virtual ATC.app"
APP_SRC="ui/src-tauri/target/universal-apple-darwin/release/bundle/macos/$APP_NAME"
OUT="dist/X-Plane Virtual ATC"

if [ ! -d "$APP_SRC" ]; then
  echo "error: built app not found at:" >&2
  echo "  $APP_SRC" >&2
  echo "Build it first: (cd ui && npm run tauri build -- --bundles app --target universal-apple-darwin)" >&2
  exit 1
fi

rm -rf dist
mkdir -p "$OUT"

# 1) The prebuilt desktop app.
cp -R "$APP_SRC" "$OUT/"

# 2) The Python backend — only what backend/server.py imports at runtime.
for item in \
  config.py requirements.txt requirements-local.txt .env.example \
  aircraft airport atc audio backend xplane scenarios
do
  cp -R "$item" "$OUT/"
done

# 3) Double-click launchers + the quick-start note.
cp "packaging/macos/start.command"      "$OUT/start.command"
cp "packaging/macos/setup.command"      "$OUT/setup.command"
cp "packaging/macos/READ ME FIRST.txt"  "$OUT/READ ME FIRST.txt"
chmod +x "$OUT/start.command" "$OUT/setup.command"

# 4) Strip dev cruft that may ride along in the copied trees.
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
find "$OUT" -name '.DS_Store' -delete

echo "Staged release folder:"
du -sh "$OUT"
