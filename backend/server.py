"""
WebSocket backend for the VFR ATC UI.

Listens on ws://localhost:8765.
Manages flight state, airport detection, and ATC session.
Streams events to connected Tauri clients.

Server → client event types:
  backend_status   uptime_s, source, airport_loaded
  state_update     lat, lon, alt_ind_ft, ias_kts, gs_kts, heading_mag,
                   on_ground, com1_mhz, com2_mhz, transponder, acf_icao, tail_number
  airport_detected icao, name, elevation_ft, runways[], frequencies[]
  atc_message      role ("pilot"|"atc"), text, model, timestamp
  atc_audio        audio (base64 WAV), text, model, timestamp  [if AUDIO_ENABLED]
  transcription    text                                        [if AUDIO_ENABLED]
  ptt_start        (no payload) — X-Plane PTT button pressed  [if XPLANE_PTT_DATAREF set]
  ptt_end          (no payload) — X-Plane PTT button released [if XPLANE_PTT_DATAREF set]
  thinking         thinking (bool)
  phase_change     phase, station
  source_change    source ("xplane"|"simulated"), scenario_name
  error            message

Client → server message types:
  pilot_transmission  text
  pilot_audio         audio (base64 WAV from mic)             [if AUDIO_ENABLED]
  load_scenario       scenario (dict matching Scenario.to_dict())
  set_source          source ("xplane"|"simulated")
"""

import asyncio
import base64
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import websockets
from websockets import ServerConnection as WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import aircraft.database as acdb
from airport.parser import parse_apt_dat, Airport
from airport.database import AirportDB
from atc import engine as atc_engine
from atc.parser import parse as parse_radio_call
from atc.session import ATCSession
from xplane.connector import FlightState
from xplane.simulator import ScenarioSimulator, Scenario

log = logging.getLogger(__name__)

# ── Optional audio modules (require pip install -r requirements-audio.txt) ───

_AUDIO_READY = False
if config.AUDIO_ENABLED:
    try:
        from audio import radio as _audio_radio
        from audio import tts   as _audio_tts
        from audio import stt   as _audio_stt
        _AUDIO_READY = True
    except ImportError as _e:
        log.warning(f"Audio modules unavailable ({_e}); running text-only.")

# ------------------------------------------------------------------ #
# Global state (single-process, no shared memory concerns)

_clients: set[WebSocketServerProtocol] = set()
_driver: Optional[ScenarioSimulator] = None
_airport_db: Optional[AirportDB] = None
_current_airport: Optional[Airport] = None
_current_acft = None
_session: Optional[ATCSession] = None
_thinking_count: int = 0         # in-flight transmissions; UI sees thinking=True when > 0
_source: str = "simulated"
_start_time: float = 0.0
_prev_ptt: bool = False          # X-Plane PTT edge-detection
_tx_lock: Optional[asyncio.Lock] = None   # serialise concurrent LLM calls

MAX_AUDIO_BYTES = 2 * 1024 * 1024   # 2 MB ≈ 62 s at 16 kHz 16-bit mono


async def _thinking_enter():
    global _thinking_count
    _thinking_count += 1
    if _thinking_count == 1:
        await _broadcast("thinking", thinking=True)


async def _thinking_exit():
    global _thinking_count
    _thinking_count = max(0, _thinking_count - 1)
    if _thinking_count == 0:
        await _broadcast("thinking", thinking=False)


# ------------------------------------------------------------------ #
# Serialisation helpers

def _state_dict(s: FlightState) -> dict:
    return {
        "lat": s.lat,
        "lon": s.lon,
        "alt_ind_ft": s.alt_ind_ft,
        "ias_kts": s.ias_kts,
        "gs_kts": s.gs_kts,
        "heading_mag": s.heading_mag,
        "on_ground": s.on_ground > 0.5,
        "com1_mhz": s.com1_mhz,
        "com2_mhz": s.com2_mhz,
        "transponder": int(s.transponder) if s.transponder else 1200,
        "acf_icao": s.acf_icao,
        "tail_number": s.tail_number,
    }


def _airport_dict(ap: Airport) -> dict:
    return {
        "icao": ap.icao,
        "name": ap.name,
        "elevation_ft": ap.elevation_ft,
        "runways": [
            {"name1": r.name1, "name2": r.name2, "width_m": r.width_m}
            for r in ap.runways
        ],
        "frequencies": [
            {"type_code": f.type_code, "type_name": f.type_name,
             "freq_mhz": f.freq_mhz, "name": f.name}
            for f in ap.frequencies
        ],
    }


# ------------------------------------------------------------------ #
# Broadcast helpers

async def _broadcast(msg_type: str, **data):
    if not _clients:
        return
    payload = json.dumps({"type": msg_type, **data})
    results = await asyncio.gather(
        *(ws.send(payload) for ws in list(_clients)),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            log.debug(f"Send failed (client likely disconnected): {r}")


async def _send_to(ws: WebSocketServerProtocol, msg_type: str, **data):
    try:
        await ws.send(json.dumps({"type": msg_type, **data}))
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Client handler

async def _send_current_state(ws: WebSocketServerProtocol):
    """Snapshot of current state sent to a freshly-connected client."""
    global _driver, _current_airport, _source
    await _send_to(ws, "backend_status",
                   uptime_s=int(time.time() - _start_time),
                   source=_source,
                   airport_loaded=_current_airport is not None,
                   audio_ready=_AUDIO_READY)
    if _driver:
        await _send_to(ws, "state_update", **_state_dict(_driver.state))
    if _current_airport:
        await _send_to(ws, "airport_detected", **_airport_dict(_current_airport))
    history = _session._history if _session else []
    for entry in history:
        await _send_to(ws, "atc_message", role="pilot",
                       text=entry["pilot"], model=None, timestamp=0)
        await _send_to(ws, "atc_message", role="atc",
                       text=entry["atc"], model=entry.get("model"), timestamp=0)


async def _client_handler(ws: WebSocketServerProtocol):
    _clients.add(ws)
    log.info(f"Client connected  [{len(_clients)} total]")
    try:
        await _send_current_state(ws)
        async for raw in ws:
            try:
                msg = json.loads(raw)
                await _handle_client_message(msg)
            except Exception as e:
                log.exception(f"Error handling message: {e}")
                await _send_to(ws, "error", message=str(e))
    except ConnectionClosed:
        pass
    except Exception as e:
        log.exception(f"Unhandled error in client handler: {e}")
    finally:
        _clients.discard(ws)
        log.info(f"Client disconnected [{len(_clients)} total]")


# ------------------------------------------------------------------ #
# Message handling

async def _handle_client_message(msg: dict):
    t = msg.get("type")
    if t == "pilot_transmission":
        asyncio.create_task(_process_transmission(msg["text"]))
    elif t == "pilot_audio":
        asyncio.create_task(_process_audio_transmission(msg["audio"]))
    elif t == "load_scenario":
        await _load_scenario(msg["scenario"])
    elif t == "set_source":
        await _set_source(msg["source"])


async def _process_transmission(text: str):
    global _thinking, _session

    async with _tx_lock:
        await _process_transmission_locked(text)


async def _process_transmission_locked(text: str):
    global _session

    await _thinking_enter()
    await _broadcast("atc_message", role="pilot", text=text,
                     model=None, timestamp=time.time())
    try:
        if _session is None:
            await _broadcast("error", message="No active session — load a scenario first.")
            return

        # session.process() is blocking (calls claude subprocess)
        r = await asyncio.to_thread(_session.process, text)

        await _broadcast("atc_message", role="atc", text=r.text,
                         model=r.model, timestamp=time.time())

        # Synthesize ATC audio (non-fatal if TTS unavailable)
        if _AUDIO_READY:
            try:
                samples, sr = await asyncio.to_thread(_audio_tts.synthesize, r.text)
                samples      = _audio_radio.apply_radio_fx(samples, sr)
                wav_bytes    = _audio_radio.encode_wav(samples, sr)
                await _broadcast("atc_audio",
                                 audio=base64.b64encode(wav_bytes).decode(),
                                 text=r.text, model=r.model,
                                 timestamp=time.time())
            except Exception as tts_err:
                log.warning(f"TTS synthesis failed: {tts_err}")

        # Broadcast state changes so the UI updates immediately
        await _broadcast("phase_change",
                         phase=r.phase_after.value,
                         station=r.station_after.value,
                         atc_callsign=_session._atc_callsign(),
                         active_runway=_session._flat_conditions().get('active_runway', ''),
                         notes="")

        if r.squawk:
            await _broadcast("squawk_assigned", squawk=r.squawk)

    except Exception as e:
        log.exception("Error generating ATC response")
        await _broadcast("error", message=f"ATC response failed: {e}")
    finally:
        await _thinking_exit()


async def _process_audio_transmission(audio_b64: str):
    """Transcribe pilot audio (base64 WAV) then route through the normal text path.

    The thinking indicator is kept lit continuously from STT start through LLM
    finish. Counter-based: audio path increments on entry, _process_transmission
    increments again; the indicator clears only after both exit.
    """
    if not _AUDIO_READY:
        await _broadcast("error", message="Audio (STT) not available — install requirements-audio.txt")
        return

    wav_bytes = base64.b64decode(audio_b64)
    if len(wav_bytes) > MAX_AUDIO_BYTES:
        await _broadcast("error", message="Audio clip too large (max 2 MB)")
        return

    await _thinking_enter()
    try:
        callsign = _session.callsign if _session else None
        try:
            text = await asyncio.to_thread(_audio_stt.transcribe, wav_bytes, callsign=callsign)
        except Exception as e:
            log.warning(f"STT transcription failed: {e}")
            await _broadcast("error", message=f"STT failed: {e}")
            return

        if not text:
            log.debug("STT returned empty transcript — ignoring")
            return

        await _broadcast("transcription", text=text)
        # _process_transmission increments the counter while the LLM runs;
        # our counter stays at ≥1 until both tasks exit their finally blocks.
        await _process_transmission(text)
    finally:
        await _thinking_exit()


async def _load_scenario(data: dict):
    global _driver, _source, _current_airport, _current_acft, _session

    try:
        scenario = Scenario.from_dict(data)
    except (KeyError, TypeError) as e:
        await _broadcast("error", message=f"Invalid scenario: {e}")
        return

    _current_airport = None
    _session = None

    sim = ScenarioSimulator(scenario)
    _driver = sim
    _source = "simulated"
    _current_acft = acdb.lookup(scenario.aircraft_icao)

    await _broadcast("source_change", source="simulated", scenario_name=scenario.name)
    await _broadcast("state_update", **_state_dict(sim.state))

    # Find departure airport
    departure = None
    if _airport_db:
        departure = _airport_db.nearest(scenario.lat, scenario.lon)
        if not departure and scenario.departure_airport:
            departure = _airport_db.get(scenario.departure_airport)

    if departure:
        await _set_airport(departure, scenario)

    log.info(f"Loaded scenario: {scenario.name}")


async def _set_source(source: str):
    global _source, _prev_ptt
    # Reset PTT edge-detection state so we don't emit a spurious ptt_end
    # if the previous source had PTT active at the moment of the switch.
    if _prev_ptt:
        await _broadcast("ptt_end")
    _prev_ptt = False

    if source == "xplane":
        # XPlane mode: start the real connector
        from xplane.connector import XPlaneConnector
        connector = XPlaneConnector(
            xplane_host=config.XPLANE_IP,
            xplane_port=config.XPLANE_UDP_PORT,
            local_port=config.LOCAL_RECV_PORT,
            ptt_dataref=config.XPLANE_PTT_DATAREF,
        )
        try:
            connector.start()
            global _driver
            _driver = connector
            _source = "xplane"
            await _broadcast("source_change", source="xplane", scenario_name=None)
        except OSError as e:
            await _broadcast("error", message=f"Cannot connect to X-Plane: {e}")
    else:
        await _broadcast("error", message="Switch to simulated via load_scenario")


async def _set_airport(airport: Airport, scenario: Optional[Scenario] = None):
    global _current_airport, _session

    _current_airport = airport
    await _broadcast("airport_detected", **_airport_dict(airport))

    # Build per-airport conditions dict for ATCSession
    if scenario:
        cond = scenario.conditions
        session_conditions = {
            airport.icao: {
                'qnh':            cond.get('qnh', 1013),
                'wind_dir':       cond.get('wind_dir', 0),
                'wind_kts':       cond.get('wind_kts', 0),
                'visibility_km':  cond.get('visibility_km', 10),
                'atis':           cond.get('atis', '?'),
                'active_runway':  cond.get('active_runway', ''),
            }
        }
    else:
        session_conditions = {airport.icao: {}}

    # Look up destination airport (if scenario specifies one)
    destination: Optional[Airport] = None
    if scenario and scenario.destination_airport and _airport_db:
        destination = _airport_db.get(scenario.destination_airport)
        if destination:
            dcond = scenario.destination_conditions
            session_conditions[destination.icao] = {
                'qnh':            dcond.get('qnh', 1013),
                'wind_dir':       dcond.get('wind_dir', 0),
                'wind_kts':       dcond.get('wind_kts', 0),
                'visibility_km':  dcond.get('visibility_km', 10),
                'atis':           dcond.get('atis', '?'),
                'active_runway':  dcond.get('active_runway', ''),
            }
        else:
            log.warning(f"Destination {scenario.destination_airport} not found in airport DB")

    # Run Opus boundary check to determine active runway
    log.info(f"Running Opus boundary check for {airport.icao}...")
    flat_cond = {
        'wind': f"{session_conditions[airport.icao].get('wind_dir', '?')}° / {session_conditions[airport.icao].get('wind_kts', '?')} kt",
        'qnh':  str(session_conditions[airport.icao].get('qnh', '??')),
        'vis':  f"{session_conditions[airport.icao].get('visibility_km', '?')} km",
        'time': 'check simulator clock',
    }
    try:
        ctx = await asyncio.to_thread(
            atc_engine.boundary_check,
            airport=airport,
            acft=_current_acft,
            callsign=(_driver.state.tail_number if _driver else "UNKNOWN"),
            conditions=flat_cond,
            model=config.MODEL_BOUNDARY,
        )
        active_runway = ctx.get("active_runway", "unknown")
        notes = ctx.get("notes", "")
    except Exception as e:
        log.warning(f"Boundary check failed: {e}")
        active_runway = "unknown"
        notes = ""

    # Inject active runway into conditions
    session_conditions[airport.icao]['active_runway'] = active_runway

    # Create ATCSession
    callsign = (_driver.state.tail_number if _driver else "UNKNOWN") or "D-UNKN"
    _session = ATCSession(
        departure=airport,
        destination=destination,
        aircraft=_current_acft,
        callsign=callsign,
        conditions=session_conditions,
    )

    await _broadcast("phase_change",
                     phase="pre_departure",
                     station="ground",
                     atc_callsign=_session._atc_callsign(),
                     active_runway=active_runway,
                     notes=notes)


# ------------------------------------------------------------------ #
# Background loops

async def _state_poll_loop():
    global _prev_ptt
    last_airport_check = 0.0

    while True:
        if _driver:
            try:
                state = _driver.state
                if state.is_flight_loaded:
                    await _broadcast("state_update", **_state_dict(state))

                    # Re-check airport every 10 s
                    now = time.time()
                    if _airport_db and now - last_airport_check > 10.0:
                        last_airport_check = now
                        airport = _airport_db.nearest(state.lat, state.lon)
                        if airport and airport.icao != (_current_airport.icao if _current_airport else ""):
                            await _set_airport(airport)

                    # X-Plane PTT edge detection (only when dataref is configured)
                    if _source == "xplane" and config.XPLANE_PTT_DATAREF:
                        ptt = state.ptt_active
                        if ptt and not _prev_ptt:
                            await _broadcast("ptt_start")
                        elif not ptt and _prev_ptt:
                            await _broadcast("ptt_end")
                        _prev_ptt = ptt

            except Exception as e:
                log.debug(f"State poll error: {e}")

        await asyncio.sleep(0.5)   # 2 Hz UI updates


async def _heartbeat_loop():
    while True:
        await _broadcast("backend_status",
                         uptime_s=int(time.time() - _start_time),
                         source=_source,
                         airport_loaded=_current_airport is not None,
                         audio_ready=_AUDIO_READY)
        await asyncio.sleep(5.0)


# ------------------------------------------------------------------ #
# Entry point

def _find_apt_dat() -> Path:
    for p in config.APT_DAT_PATHS:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"apt.dat not found. Set XPLANE_PATH.\nChecked: {config.APT_DAT_PATHS}"
    )


async def run():
    global _airport_db, _driver, _start_time, _tx_lock
    _start_time = time.time()
    _tx_lock    = asyncio.Lock()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    log.info("Loading airport database...")
    apt_dat = _find_apt_dat()
    airports = parse_apt_dat(apt_dat)
    _airport_db = AirportDB(airports)
    acdb.load()
    log.info(f"{len(airports):,} airports ready")

    # Pre-load Whisper model so the first PTT press isn't delayed by a 3 GB download
    if _AUDIO_READY:
        await asyncio.to_thread(_audio_stt.preload)

    # Default: simulated with empty state until a scenario is loaded
    _driver = ScenarioSimulator()

    # Auto-load the default scenario if present
    default = Path(__file__).parent.parent / "scenarios" / "eddv_departure.json"
    if default.exists():
        try:
            scenario = Scenario.from_file(default)
            await _load_scenario(scenario.to_dict())
            log.info(f"Auto-loaded default scenario: {scenario.name}")
        except Exception as e:
            log.warning(f"Could not auto-load default scenario: {e}")

    log.info("Backend listening on ws://localhost:8765")
    async with websockets.serve(_client_handler, "localhost", 8765):
        await asyncio.gather(
            _state_poll_loop(),
            _heartbeat_loop(),
        )


if __name__ == "__main__":
    asyncio.run(run())
