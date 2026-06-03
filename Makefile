# X-Plane Virtual ATC — local setup & run.
#
#   make setup     one-time: venv + deps + UI deps + .env
#   make dev       run backend + desktop (Tauri) app together
#   make dev-web   run backend + browser UI (no Rust toolchain needed)
#   make test      run the Python test suite
#
# Override the interpreter with `make PY=python3.12 setup`.

PY     ?= python3
VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.PHONY: setup dev dev-web backend test clean

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
