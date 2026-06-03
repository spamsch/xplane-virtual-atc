# X-Plane Virtual ATC — local setup & run.
#
#   make setup     one-time: venv + deps + UI deps + .env
#   make dev       run backend + desktop (Tauri) app together
#   make dev-web   run backend + browser UI (no Rust toolchain needed)
#   make test      run the Python test suite
#   make release   bump the patch version everywhere, tag, and push a release
#
# Override the interpreter with `make PY=python3.12 setup`.

PY     ?= python3
VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.PHONY: setup dev dev-web backend test clean release

setup:
	$(PY) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
	cd ui && npm install
	@[ -f .env ] || (cp .env.example .env && echo "Created .env from .env.example")
	@command -v claude >/dev/null 2>&1 \
		&& echo "✓ claude CLI found" \
		|| echo "✗ claude CLI not found — install it: https://claude.ai/code"
	@echo ""
	@echo "Setup complete. Add your ElevenLabs key in the app's Settings view"
	@echo "(or edit .env), then run:  make dev"

# Backend + native desktop app. Ctrl-C stops both (kill 0 = whole process group).
dev:
	@trap 'kill 0' EXIT INT TERM; \
	$(PYTHON) backend/server.py & \
	cd ui && npm run tauri dev

# Backend + browser UI at http://localhost:1420 — no Rust/Tauri toolchain.
dev-web:
	@trap 'kill 0' EXIT INT TERM; \
	$(PYTHON) backend/server.py & \
	cd ui && npm run dev

backend:
	$(PYTHON) backend/server.py

test:
	$(PYTHON) -m pytest tests/ -q

clean:
	rm -rf $(VENV) ui/node_modules ui/.svelte-kit ui/build

# Cut a release. Bumps the patch level in all four version files, syncs
# Cargo.lock, commits, tags vX.Y.Z, and pushes. Pushing the tag triggers
# .github/workflows/release.yml, which builds the prepackaged macOS app and
# publishes it to the GitHub Release.
release:
	@cur=$$(perl -ne 'print $$1 if /"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"/' ui/package.json); \
	[ -n "$$cur" ] || { echo "Could not read current version from ui/package.json"; exit 1; }; \
	next=$$(echo "$$cur" | awk -F. '{printf "%d.%d.%d", $$1, $$2, $$3 + 1}'); \
	echo "Releasing v$$cur -> v$$next"; \
	perl -i -pe "s/\"version\": \"$$cur\"/\"version\": \"$$next\"/" ui/package.json ui/src-tauri/tauri.conf.json; \
	perl -i -pe "s/^version = \"$$cur\"/version = \"$$next\"/" ui/src-tauri/Cargo.toml; \
	perl -i -pe "s/^VERSION = \"$$cur\"/VERSION = \"$$next\"/" backend/server.py; \
	( cd ui/src-tauri && cargo update --workspace --offline >/dev/null 2>&1 ) || true; \
	git add ui/package.json ui/src-tauri/tauri.conf.json ui/src-tauri/Cargo.toml ui/src-tauri/Cargo.lock backend/server.py; \
	git commit -m "Release v$$next"; \
	git tag "v$$next"; \
	git push origin HEAD && git push origin "v$$next"; \
	echo "Pushed v$$next — the release workflow will build and publish it."
