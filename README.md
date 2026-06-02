# X-Plane Virtual ATC

LLM-powered VFR air traffic control for [X-Plane 12](https://www.x-plane.com/). Fly the pattern, key the mic, and talk to a controller that actually understands what you said, clears you for the runway in use, hands you off between frequencies, and assigns a squawk — all driven by Claude, grounded in your live flight state and the real airport layout from X-Plane's `apt.dat`.

It runs two ways: a desktop app (Tauri + Svelte) with push-to-talk and synthesized controller voice, or a plain terminal CLI.

> **Status:** Departure and ground/tower phases are solid. En-route and arrival handling is partially implemented (some flows are marked `xfail` in the test suite). VFR only.

## What it does

- **Reads your real flight state** from X-Plane — position, altitude, heading, COM frequencies, transponder, aircraft type, and registration — over the REST API (X-Plane 12.1+) or classic UDP.
- **Knows the airport.** Parses X-Plane's `apt.dat`, builds a spatial index, and auto-detects the nearest field as you taxi or fly. The controller works from the actual runways, frequencies, and elevation.
- **Talks like a controller, not a chatbot.** Claude decides the active runway and station callsign once per airport (Opus), then handles the back-and-forth (Sonnet for routine, Opus for the first call to each station).
- **Drives a real state machine.** Phase and station transitions are triggered by keyword matching on the controller's output — the LLM suggests, the state machine decides. Squawk lifecycle, frequency handoffs, and the 10 flight phases are all deterministic, so behavior stays predictable even when the model gets creative.
- **Push-to-talk that feels right.** Key the mic from the UI, the spacebar, or a bound X-Plane control. You get a mechanical relay *clack* on key and a squelch-tail hiss on unkey, synthesized in the browser.
- **Voice in and out.** Optional speech-to-text (an ATC-fine-tuned Whisper model, or OpenAI) and text-to-speech (OpenAI, Piper, or macOS `say`), with a radio-DSP pass that band-limits the controller's voice and adds the hiss of a VHF set.

## How it works

Three layers compose cleanly: a **data source**, an **ATC session engine**, and a **transport**.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Data source    │     │  ATC engine      │     │  Transport          │
│                 │     │                  │     │                     │
│  X-Plane        │ ──▶ │  ATCSession      │ ──▶ │  WebSocket server   │
│  (REST or UDP)  │     │  state machine   │     │  + Tauri/Svelte UI  │
│       or        │     │  + claude CLI    │     │       or            │
│  Scenario sim   │     │  (Opus / Sonnet) │     │  terminal CLI       │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
```

- **Data source** — `xplane/rest_connector.py` (REST, the default for X-Plane 12.1+), `xplane/connector.py` (UDP/RREF), or `xplane/simulator.py` (scenario replay). All three satisfy the same informal `FlightDataSource` protocol — a `.state` property returning a `FlightState`. Everything downstream is source-agnostic.
- **Airport data** — `airport/parser.py` streams and parses `apt.dat` (~500 MB) once and caches a pickle next to it. `airport/database.py` wraps it with a 1°×1° spatial grid for O(1) candidate lookup and haversine ranking.
- **ATC engine** — `atc/session.py` is the state machine (10 phases, 6 station types, squawk lifecycle, handoff detection). `atc/engine.py` wraps the `claude` CLI: a `boundary_check` call (Opus) determines the active runway and controller callsign per airport, then `respond` generates each transmission.
- **Transport** — `backend/server.py` is an asyncio WebSocket server that polls flight state, detects airport changes, and fans LLM calls out to a thread pool. `main.py` is the synchronous CLI doing the same flow without the WebSocket machinery. The UI in `ui/` is a Tauri 2 app wrapping a Svelte 5 frontend with auto-reconnect.

Design notes live in [`CLAUDE.md`](CLAUDE.md).

## Requirements

- **X-Plane 12** with the local web API enabled (Settings → Network → *Enable local network access* / web server), or UDP data output. The REST connector targets port `8086`.
- **[Claude Code CLI](https://claude.ai/code)** installed and authenticated — the engine shells out to `claude -p`.
- **Python 3.9+** (developed against 3.14). The core runtime is standard-library only.
- **Node + npm** for the desktop UI; the Rust/Tauri toolchain if you want to build a native bundle.

## Quickstart

### Backend + desktop UI

```bash
# 1. Backend (WebSocket server at ws://localhost:8765)
pip install -r requirements-dev.txt        # websockets
python3 backend/server.py

# 2. UI (in a second terminal)
cd ui && npm install
npm run tauri dev                            # desktop app
# or: npm run dev                            # browser at :1420
```

Start X-Plane, load a flight, and the backend auto-detects it within ~2 seconds. Taxi or fly near an airport and the controller comes alive.

### Terminal CLI

```bash
python3 main.py                              # requires X-Plane running
```

### Voice (optional)

```bash
pip install -r requirements-audio.txt
```

Speech-to-text uses an ATC-fine-tuned Whisper model by default (downloaded on first use), or OpenAI if `OPENAI_API_KEY` is set. Text-to-speech prefers OpenAI, then Piper, then macOS `say`. See [Configuration](#configuration).

### Tests

The suite is fully mocked — no X-Plane and no live Claude needed.

```bash
pip install -r requirements-dev.txt
pytest tests/                                # 311 tests
pytest tests/test_session.py -v              # one file
pytest tests/ -k squawk                      # by name
```

## Configuration

Set these via environment variables or a local `.env` file in the project root (gitignored — never committed).

| Variable | Default | Purpose |
| --- | --- | --- |
| `XPLANE_IP` | `127.0.0.1` | X-Plane host |
| `XPLANE_REST_PORT` | `8086` | X-Plane REST/WebSocket API port |
| `XPLANE_PATH` | Steam default | X-Plane install dir (to locate `apt.dat`) |
| `XPLANE_PTT_DATAREF` | *(empty)* | PTT source — see [Push-to-talk](#push-to-talk) |
| `AUDIO_ENABLED` | `true` | Enable STT/TTS voice I/O |
| `TTS_BACKEND` | `auto` | `openai` \| `kokoro` \| `piper` \| `say` |
| `TTS_VOICE` | `onyx` | Voice name (backend-specific) |
| `STT_MODEL` | `large-v3` | Whisper model, or a local CTranslate2 path |
| `OPENAI_API_KEY` | *(empty)* | Enables OpenAI STT/TTS when set |

### Push-to-talk

PTT is watched over the X-Plane WebSocket API for instant press/release edges (no polling lag). `XPLANE_PTT_DATAREF` auto-detects whether you've named a **dataref** or a **command**:

- **Recommended — a dataref that holds while pressed**, e.g. `xpilot/ptt` (from the [xPilot](https://docs.xpilot-project.org/) plugin). Bind your key to the *"xPilot: Radio Push-to-Talk"* command; the dataref then stays `1` for the whole press. A raw joystick button like `sim/joystick/joystick_button_array[32]` also works.
- **Commands** (e.g. `sim/operation/contact_atc_ptt`) resolve too, but most ATC command bindings fire as a one-shot *pulse* — press and release in the same instant — so hold-to-talk won't capture audio. Use a dataref instead.

Leave `XPLANE_PTT_DATAREF` empty to drive PTT from the on-screen button or the spacebar.

## Project layout

```
backend/      asyncio WebSocket server (transport)
main.py       synchronous CLI (transport)
atc/          session state machine + claude CLI wrapper + radio-call parser
xplane/       REST connector, UDP connector, scenario simulator
airport/      apt.dat parser + spatial database
aircraft/     aircraft type lookup
audio/        STT, TTS, and radio DSP
scenarios/    JSON scenarios for the simulator data source
ui/           Tauri 2 + SvelteKit 5 desktop app
tests/        311 mocked tests
```

## License

[MIT](LICENSE) © 2026 Simon Pamies.

Not affiliated with Laminar Research or X-Plane.
