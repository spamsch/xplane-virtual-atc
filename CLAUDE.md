# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup + run (preferred)
make setup           # venv + deps + ui npm install + .env, checks for claude CLI
make dev             # backend + Tauri desktop app together
make dev-web         # backend + browser UI at :1420 (no Rust toolchain)
make test            # pytest tests/

# Python backend — default deps: websockets, numpy, scipy (requirements.txt).
# requirements-local.txt (faster-whisper/piper/sounddevice) only for offline voice.
python3 main.py                      # CLI mode (requires X-Plane running; stdlib only)
python3 backend/server.py            # WebSocket server for the UI

# Tests (no X-Plane or LLM needed — all mocked)
pip install -r requirements-dev.txt
pytest tests/
pytest tests/test_session.py -v      # single file
pytest tests/ -k "test_squawk"       # single test by name

# UI (Tauri + SvelteKit)
cd ui && npm install
npm run dev          # SvelteKit dev server at :1420
npm run tauri dev    # Desktop app (requires backend running at ws://localhost:8765)
npm run tauri build  # Production bundle
```

The backend **starts even when unconfigured** (missing apt.dat or keys): it serves a `config_status` ("doctor") over the WebSocket and the UI shows a Settings/onboarding view until Claude + a voice key + the X-Plane path are all present. apt.dat is loaded lazily (`_ensure_airport_db`) so the X-Plane path can be set at runtime via `set_config`.

## Architecture

The system has three layers that compose cleanly: a **data source**, an **ATC session engine**, and a **transport** (either CLI or WebSocket + Tauri UI).

### Data sources

`xplane/connector.py` (XPlaneConnector) and `xplane/simulator.py` (ScenarioSimulator) both satisfy the same informal `FlightDataSource` protocol — a `.state` property returning a `FlightState` dataclass. The backend and CLI swap between them at startup; everything downstream is source-agnostic. Scenarios are JSON files in `scenarios/`.

### Airport data

`airport/parser.py` streams and parses X-Plane's `apt.dat` (~500 MB) on first run and writes a pickle cache next to the file. It extracts runways (`100`), frequencies (`50–56`), the taxi-route network (`1201` nodes / `1202` edges / `1204` runway active zones), and ramp starts (`1300`). `airport/database.py` wraps the parsed data with a 1°×1° spatial grid for O(1) candidate selection and haversine distance ranking. APT.DAT path discovery logic lives in `airport/parser.py`.

`airport/taxi.py` computes real ground taxi routes — it snaps the aircraft's position onto the taxi network and runs Dijkstra to the nearest hold-short point for the active runway (found via `1204` zones), returning the actual taxiway designators. `ATCSession` hands this computed route to the LLM (in `_taxi_instruction`) with a hard rule to relay it verbatim, so the controller never invents taxiways or holding points. apt.dat has no AIP holding-point names, so holds are described by runway ("hold short of runway 27R").

### ATC engine

`atc/session.py` is the core state machine. It manages:
- 10 flight phases (PRE_DEPARTURE → ... → PARKED)
- 6 station types (Ground, Tower, Approach, Departure/Radar, FIS)
- Squawk lifecycle (assign at Tower, 7000 on CTR exit, reassign at Approach)
- Frequency handoff detection by regex over LLM response text
- Phase transitions keyed on keywords in LLM responses ("cleared for takeoff", "radar contact", etc.)

`atc/engine.py` wraps the `claude` CLI via subprocess. It makes two kinds of calls:
1. **boundary_check** — Opus, once per airport; determines active runway and ATC callsign from airport context, returns JSON
2. **respond** — Opus on the first call per station, Sonnet for routine exchanges; takes the full session history as system context, returns a plain ATC response string

`atc/parser.py` extracts station, callsign, and message from raw pilot radio text via regex.

### Backend / transport

`backend/server.py` runs an asyncio WebSocket server at `ws://localhost:8765`. It polls flight state, auto-detects the nearest airport every 10 s, fires boundary_check on airport change, maintains one `ATCSession` per airport, and fans LLM calls out to a thread pool to avoid blocking the event loop. All state changes are broadcast as typed JSON events to connected UI clients.

`main.py` is a simpler synchronous CLI that does the same flow without WebSocket machinery.

### UI

`ui/` is a Tauri 2 app wrapping a SvelteKit 5 / Svelte 5 frontend. The Svelte stores in `store.js` hold all application state; `ws.js` handles the WebSocket connection with 3-second auto-reconnect. The frontend is purely reactive — it receives events from the backend and sends pilot transmissions back.

## Key design constraints

- **AI suggests, state machine decides.** Phase and station transitions in `ATCSession` are triggered by keyword matching on LLM output — the LLM never directly drives state. This keeps behavior predictable under hallucination.
- **Opus at decision boundaries.** boundary_check (runway/callsign decisions) and first-call-per-station always use Opus (`claude-opus-4-8`). Routine exchanges use Sonnet (`claude-sonnet-4-6`). Model selection is in `config.py`.
- **Lean default deps.** `main.py` and the xplane/airport/atc modules use only stdlib. The backend needs `websockets` + `numpy`/`scipy` (audio pipeline). Offline STT/TTS (`faster-whisper`, `piper-tts`) and CLI mic (`sounddevice`) are optional — `requirements-local.txt`. Defaulting to ElevenLabs means the default install pulls no ML model.
- **Test suite is fully mocked.** All tests `@patch` the `atc.engine.respond()` and `atc.engine.boundary_check()` calls — no live Claude or X-Plane connection needed. `test_full_flight_exchange.py` contains `xfail` tests marking en-route/arrival phases as not yet fully implemented. Two opt-in tests are the exceptions, each gated behind an env var:
  - `tests/test_live_xplane.py` (`VATC_LIVE=1`) — taxi-routing diagnostic against a running X-Plane; prints live position, snapped stand/node, and the computed route to every runway end.
  - `tests/test_live_claude.py` (`VATC_LLM=1`) — really shells out to `claude -p` to check phraseology (initial contact → "pass your message", taxi only when requested, no hallucinated taxiways) and that `boundary_check` returns valid JSON. Needs the `claude` CLI signed in; costs tokens; assertions are loose because the model is non-deterministic.
