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
  ambient_audio    audio (base64 WAV), speaker ("pilot"|"atc"), text, callsign,
                   kind ("ambient"|"interjection")             [ambient party-line traffic]
  ambient_noise    audio (base64 WAV), kind ("squelch"|"static"|"chatter")
                   — background radio atmosphere between transmissions (no text)
  ambient_stop     (no payload) — cut any in-flight ambient audio now (you keyed up)
  transcription    text                                        [if AUDIO_ENABLED]
  ptt_start        (no payload) — X-Plane PTT button pressed  [if XPLANE_PTT_DATAREF set]
  ptt_end          (no payload) — X-Plane PTT button released [if XPLANE_PTT_DATAREF set]
  thinking         thinking (bool)
  phase_change     phase, station
  source_change    source ("xplane"|"simulated"), scenario_name
  xplane_status    connected (bool) — live X-Plane REST link state
  loading          active (bool), label — session setup (boundary check) in progress
  config_status    checks{...}, configured (bool), current{...} — the setup "doctor"
  error            message

Client → server message types:
  pilot_transmission  text
  pilot_audio         audio (base64 WAV from mic)             [if AUDIO_ENABLED]
  load_scenario       scenario (dict matching Scenario.to_dict())
  set_source          source ("xplane"|"simulated")
  set_config          config {elevenlabs_api_key?, openai_api_key?, xplane_path?, xplane_ptt_dataref?}
  set_ambient         level ("off"|"light"|"medium"|"heavy"), rules? (["VFR","IFR"])
  mic_open            (no payload) — pilot keyed the mic (suppress ambient)
  mic_close           (no payload) — pilot released the mic
  get_config_status   (no payload) — request a fresh config_status
"""

import asyncio
import base64
import hashlib
import json
import logging
import math
import random
import shutil
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
from atc.session import ATCSession, Station, _dist_bearing_nm
from traffic.library import (
    load_library, classify_size, render as _render_interaction,
    RenderContext, InteractionLibrary,
)
from traffic.ambient import AmbientPlanner
from airspace.database import AirspaceDB, Airspace
from airspace import openaip as airspace_openaip
from xplane.connector import FlightState
from xplane.rest_connector import XPlaneRestConnector, encode_fixed_string
from xplane.ptt_listener import PTTListener
from xplane.simulator import ScenarioSimulator, Scenario

log = logging.getLogger(__name__)

VERSION = "0.1.9"
HOST    = "localhost"
PORT    = 8765


def _startup_banner() -> None:
    """Print an avionics-style boxed banner. Colors only on a real TTY so
    piped logs (journald, files) stay clean."""
    tty = sys.stdout.isatty()

    def paint(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if tty else s

    cyan  = lambda s: paint("38;5;44", s)
    mint  = lambda s: paint("38;5;48", s)
    amber = lambda s: paint("38;5;179", s)
    dim   = lambda s: paint("2", s)

    w = 46  # inner width between the side margins

    def row(plain: str, colored: str) -> str:
        pad = " " * (w - len(plain))
        return cyan("  │") + " " + colored + pad + " " + cyan("│")

    top   = cyan("  ┌" + "─" * (w + 2) + "┐")
    bot   = cyan("  └" + "─" * (w + 2) + "┘")
    blank = row("", "")
    hero  = row(
        "((o))   X-PLANE · VIRTUAL ATC",
        mint("((o))") + "   " + cyan("X-PLANE") + dim(" · ") + cyan("VIRTUAL ATC"),
    )
    sub   = row(
        "VFR ground · tower · approach control",
        dim("VFR ground · tower · approach control"),
    )

    lines = [
        "",
        top, blank, hero, sub, blank, bot,
        "    " + dim("listening") + "   " + amber(f"ws://{HOST}:{PORT}"),
        "    " + dim("version")   + "     " + f"{VERSION}",
        "",
    ]
    print("\n".join(lines), flush=True)


# ── Optional audio modules (require pip install -r requirements-audio.txt) ───

_AUDIO_READY = False
if config.AUDIO_ENABLED:
    try:
        from audio import radio as _audio_radio
        from audio import tts   as _audio_tts
        from audio import stt   as _audio_stt
        from audio import radio_text as _radio_text
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
_prev_ptt: bool = False          # last broadcast PTT state (de-dupe spurious ends)
_ptt_listener: Optional[PTTListener] = None   # WebSocket PTT edge listener
_tx_lock: Optional[asyncio.Lock] = None   # serialise concurrent LLM calls
_loop: Optional[asyncio.AbstractEventLoop] = None   # main loop (for thread-safe scheduling)
_xplane_connected: bool = False  # is the X-Plane REST API actually reachable right now
_airport_db_loading: bool = False   # apt.dat parse in progress (deferred until configured)
_startup_vfr_done: bool = False     # VFR_WEATHER_ON_START applied once this run

# ── Ambient traffic ("party line") state ─────────────────────────────────────
_ambient_lib: Optional[InteractionLibrary] = None   # the interaction library
_ambient_planner: Optional[AmbientPlanner] = None    # level → timing/probability
_ambient_size: Optional[str] = None      # current airport size (small|medium|large)
_ambient_rng = random.Random()           # selection + callsign variety
_channel_lock: Optional[asyncio.Lock] = None   # half-duplex radio: one TX at a time
_user_speaking: bool = False             # pilot is keying the mic → no traffic

# ── Airspace (OpenAIP) ───────────────────────────────────────────────────────
_airspace_db: Optional[AirspaceDB] = None    # airspace for the current country
_airspace_country: Optional[str] = None      # ISO code currently loaded
_airspace_loading: bool = False              # a load is in flight

MAX_AUDIO_BYTES = 2 * 1024 * 1024   # 2 MB ≈ 62 s at 16 kHz 16-bit mono
ACF_TAILNUM_BYTES = 40              # sim/aircraft/view/acf_tailnum is char[40]


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
        "qnh_hpa": s.qnh_hpa,
        "wind_dir": int(s.wind_dir_deg),
        "wind_kts": int(s.wind_speed_kts),
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
# Ambient traffic — the party line
#
# A VHF frequency is half-duplex: one aircraft transmits at a time. We model
# that with a single channel lock through which BOTH the real controller reply
# and the ambient traffic play, so nothing ever keys over anything else. The
# ambient loop schedules other-aircraft exchanges at a cadence set by the level
# (light/medium/heavy), matched to the frequency type you're tuned to and the
# airport's size, and goes silent the instant you key the mic.

async def _play_on_channel(samples, sr: int, *, event: str, **meta):
    """Encode + broadcast one radio clip, holding the (half-duplex) channel for
    its duration so the next transmission waits its turn. Used by both the real
    controller reply and the ambient traffic."""
    wav = _audio_radio.encode_wav(samples, sr)
    duration = len(samples) / float(sr) if sr else 0.0
    assert _channel_lock is not None
    async with _channel_lock:
        await _broadcast(event, audio=base64.b64encode(wav).decode(), **meta)
        # Hold the frequency for the clip length (+ a beat of squelch tail).
        await asyncio.sleep(duration + 0.15)


def _tuned_station() -> Optional[Station]:
    """Which ATC station the live COM1 is tuned to, matched against the airport's
    published frequencies. Falls back to the session's current station when the
    dial doesn't match anything (or we have no live radio)."""
    if _session is None:
        return None
    if _source == "xplane" and _driver is not None:
        try:
            com1 = _driver.state.com1_mhz
        except Exception:
            com1 = 0.0
        if com1:
            # COM1 is readable: trust the dial. A match gives the station; no
            # match returns None — which, when airborne, reads as "en route"
            # (you've tuned away from this airport's controllers).
            return _session._station_from_freq(com1)
    # COM1 unreadable → fall back to where the session thinks we are.
    return _session.current_station


def _ambient_active() -> bool:
    """All the gates that must be open for any ambient traffic to play."""
    return (
        _AUDIO_READY
        and _ambient_planner is not None and _ambient_planner.enabled
        and _source == "xplane" and _xplane_connected
        and _session is not None
        and not _user_speaking
        and _thinking_count == 0
    )


def _ambient_plan():
    """Resolve the current situation into a library query + render context.

    Returns (station_name, size, enroute, rules, RenderContext) or None when
    nothing sensible can be said (e.g. no session yet)."""
    if _session is None:
        return None

    st = _tuned_station()
    airborne = False
    if _driver is not None:
        try:
            s = _driver.state
            airborne = s.is_flight_loaded and s.on_ground < 0.5
        except Exception:
            airborne = False

    # En route = airborne and tuned to an information service (or nothing the
    # airport recognises). En route is VFR-only, always, and has no airport.
    # Radar/Departure is a controlled *airport* radar service with its own
    # library, so it is NOT en route — it falls through to the airport branch.
    enroute = airborne and st in (Station.FIS, None)
    if enroute:
        station_name = "fis"
        size = None
        rules = ["VFR"]
        atc_callsign = "Information"
    else:
        if st is None:
            st = _session.current_station
        station_name = _AMBIENT_STATION_NAME.get(st)
        if station_name is None:
            return None
        size = _ambient_size
        rules = list(config.AMBIENT_TRAFFIC_RULES)
        # Name the controller from the station the pilot is actually tuned to —
        # not the session's current station, which can lag a dial change.
        city = _current_airport.name.split()[0] if _current_airport else ""
        atc_callsign = f"{city} {_AMBIENT_STATION_LABEL.get(st, 'Radio')}".strip()

    # Fill the render context with sensible fallbacks so a template never leaves
    # a dangling word ("cleared to land runway ,").
    icao = _current_airport.icao if _current_airport else None
    raw = (_session.conditions.get(icao) or {}) if icao else {}

    runway = str(raw.get("active_runway", "") or "").strip()
    if (not runway or runway.lower() == "unknown") and _current_airport and _current_airport.runways:
        runway = _current_airport.runways[0].name1
    if enroute:
        runway = ""   # no runway en route

    qnh = raw.get("qnh")
    qnh = str(qnh) if qnh not in (None, "", "?") else "1013"

    wd, wk = raw.get("wind_dir"), raw.get("wind_kts")
    wind = f"{wd} degrees {wk} knots" if wd not in (None, "", "?") and wk not in (None, "", "?") else ""

    ctx = RenderContext(
        atc_callsign=atc_callsign,
        runway=runway,
        qnh=qnh,
        wind=wind,
        airport=(_current_airport.name.split()[0] if _current_airport else ""),
    )
    return station_name, size, enroute, rules, ctx


def _render(interaction, ctx):
    return _render_interaction(interaction, ctx, _ambient_rng)


def _pilot_voice_pool() -> list:
    """The voices other traffic draws from. An explicit AMBIENT_PILOT_VOICES wins;
    otherwise pick a default pool that matches the active TTS backend (ElevenLabs
    gets 10 distinct voices, OpenAI its six). Backends without a usable pool
    (say/kokoro/piper) return [] → we vary pitch on the controller voice instead."""
    if config.AMBIENT_PILOT_VOICES:
        return config.AMBIENT_PILOT_VOICES
    backend = _audio_tts.active_backend()
    if backend == "elevenlabs":
        return config.ELEVENLABS_PILOT_POOL
    if backend == "openai":
        return config.OPENAI_PILOT_POOL
    return []


def _ambient_voice_and_profile(callsign: str, speaker: str):
    """(voice, radio_profile, pitch_semitones) for one ambient line.

    Controller lines reuse your configured controller voice and the default
    radio — it's the same controller you're talking to. Other pilots get a voice
    drawn from the pool (see _pilot_voice_pool) plus a distinct radio character —
    all seeded off the callsign so a given aircraft sounds like one consistent
    station across its read-backs. With no usable pool, we vary the controller
    voice's pitch instead so traffic still sounds like different people."""
    import numpy as np
    if speaker == "atc":
        return None, _audio_radio.DEFAULT_PROFILE, 0.0
    seed = int(hashlib.sha1(callsign.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    profile = _audio_radio.random_profile(np.random.default_rng(seed))
    pool = _pilot_voice_pool()
    if pool:
        return rng.choice(pool), profile, 0.0
    return None, profile, rng.uniform(-2.2, 2.2)   # vary pitch when no voice pool


def _synth_line(text: str, voice, profile, pitch: float):
    """Blocking: normalise → TTS → (pitch) → radio FX. Run via to_thread."""
    spoken = _radio_text.to_spoken(text)
    samples, sr = _audio_tts.synthesize(spoken, voice=voice)
    if pitch:
        samples = _audio_radio.pitch_shift(samples, pitch)
    samples = _audio_radio.apply_radio_fx(samples, sr, profile=profile)
    return samples, sr


async def _play_rendered(rendered, *, kind: str) -> bool:
    """Synthesize and play each line of a rendered interaction in turn, bailing
    the instant the pilot keys up. Returns True if it ran to completion."""
    for i, line in enumerate(rendered.lines):
        if _user_speaking:
            return False
        voice, profile, pitch = _ambient_voice_and_profile(rendered.callsign, line.speaker)
        try:
            samples, sr = await asyncio.to_thread(_synth_line, line.text, voice, profile, pitch)
        except Exception as e:
            log.debug(f"Ambient synth failed ({rendered.interaction.id}): {e}")
            return False
        if _user_speaking:
            return False
        await _play_on_channel(
            samples, sr, event="ambient_audio",
            speaker=line.speaker, text=line.text,
            callsign=rendered.callsign, kind=kind,
            interaction_id=rendered.interaction.id,
        )
        if i < len(rendered.lines) - 1:
            await asyncio.sleep(_ambient_planner.inter_line_gap())
    return True


async def _play_background(kind: str):
    """Play one short background-radio clip (squelch break / static / distant
    chatter) on the channel — no text, no bubble. Fills the quiet so the
    frequency sounds open and busy between transmissions."""
    if not _AUDIO_READY:
        return
    try:
        samples = await asyncio.to_thread(_audio_radio.background_event, kind)
    except Exception as e:
        log.debug(f"Background atmosphere synth failed ({kind}): {e}")
        return
    if _user_speaking:
        return
    await _play_on_channel(samples, _audio_radio.BACKGROUND_SR,
                           event="ambient_noise", kind=kind)


async def _maybe_interject():
    """After the pilot transmits, sometimes work one other aircraft before
    turning back to them. Called inside the transmission lock, before the real
    reply is broadcast, so the chat reads: pilot → other traffic → your reply."""
    if not _AUDIO_READY or _ambient_planner is None or not _ambient_planner.enabled:
        return
    if _source != "xplane" or not _xplane_connected or _session is None:
        return
    if not _ambient_planner.should_interject():
        return
    plan = _ambient_plan()
    if not plan:
        return
    station_name, size, enroute, rules, ctx = plan
    it = _ambient_lib.pick(station=station_name, size=size, enroute=enroute,
                           rules=rules, rng=_ambient_rng) if _ambient_lib else None
    if it is None:
        return
    rendered = _render(it, ctx)
    if rendered.lines:
        log.info(f"Ambient interjection before reply: {it.id} ({rendered.callsign})")
        await _play_rendered(rendered, kind="interjection")


async def _ambient_loop():
    """Schedule ambient interactions at the level's cadence. Runs forever; every
    gate is re-checked right before anything is said, so toggling the level,
    keying the mic, or losing the X-Plane link takes effect immediately."""
    while True:
        if not _ambient_active():
            await asyncio.sleep(2.0)
            continue
        gap = _ambient_planner.next_gap()
        if not math.isfinite(gap):
            await asyncio.sleep(2.0)
            continue
        deadline = time.monotonic() + gap
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            if not _ambient_active():
                break
            # Sprinkle background atmosphere through the quiet so it isn't dead air.
            if _ambient_planner.should_emit_atmosphere(1.0):
                await _play_background(_ambient_planner.pick_atmosphere())
        if not _ambient_active():
            continue
        plan = _ambient_plan()
        if not plan:
            continue
        station_name, size, enroute, rules, ctx = plan
        it = _ambient_lib.pick(station=station_name, size=size, enroute=enroute,
                               rules=rules, rng=_ambient_rng) if _ambient_lib else None
        if it is None:
            continue
        rendered = _render(it, ctx)
        if not rendered.lines or not _ambient_active():
            continue
        log.debug(f"Ambient traffic: {it.id} on {station_name} ({rendered.callsign})")
        await _play_rendered(rendered, kind="ambient")


async def _set_user_speaking(flag: bool):
    """Gate ambient traffic on the pilot's mic. On key-up we also tell clients to
    cut any party-line audio already playing — a real radio goes quiet the moment
    you transmit."""
    global _user_speaking
    if flag == _user_speaking:
        return
    _user_speaking = flag
    if flag:
        await _broadcast("ambient_stop")


async def _set_ambient(level: Optional[str], rules=None):
    """Apply + persist the ambient level (off|light|medium|heavy) and, optionally,
    the airport ruleset (["VFR"] or ["VFR","IFR"])."""
    if level is not None:
        lvl = str(level).lower().strip()
        config.set_env("AMBIENT_TRAFFIC_LEVEL", lvl)
        if _ambient_planner is not None:
            _ambient_planner.set_level(lvl)
        log.info(f"Ambient traffic level → {lvl}")
    if rules is not None:
        if isinstance(rules, str):
            rlist = [r.strip().upper() for r in rules.split(",")]
        else:
            rlist = [str(r).strip().upper() for r in rules]
        rlist = [r for r in rlist if r in ("VFR", "IFR")] or ["VFR"]
        config.set_env("AMBIENT_TRAFFIC_RULES", ",".join(rlist))
        config.AMBIENT_TRAFFIC_RULES = rlist   # set_env stored the string; keep the list
        log.info(f"Ambient traffic rules → {rlist}")
    await _broadcast_config_status()


# Station enum → library station name
_AMBIENT_STATION_NAME: dict = {
    Station.GND:   "ground",
    Station.TWR:   "tower",
    Station.APP:   "approach",
    Station.DEP:   "radar",
    Station.RADAR: "radar",
    Station.FIS:   "fis",
}

# Station enum → spoken controller suffix (for the ambient controller callsign).
_AMBIENT_STATION_LABEL: dict = {
    Station.GND:   "Ground",
    Station.TWR:   "Tower",
    Station.APP:   "Approach",
    Station.DEP:   "Departure",
    Station.RADAR: "Radar",
    Station.FIS:   "Information",
}


# ------------------------------------------------------------------ #
# Client handler

async def _send_current_state(ws: WebSocketServerProtocol):
    """Snapshot of current state sent to a freshly-connected client."""
    global _driver, _current_airport, _source
    await _send_to(ws, "backend_status",
                   uptime_s=int(time.time() - _start_time),
                   source=_source,
                   airport_loaded=_current_airport is not None,
                   audio_ready=_AUDIO_READY,
                   xplane_connected=_xplane_connected)
    await _send_to(ws, "config_status", **_config_status())
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
    elif t == "set_callsign":
        await _set_callsign(msg.get("callsign", ""))
    elif t == "tune_com1":
        await _tune_com1(float(msg["freq_mhz"]))
    elif t == "tune_com2":
        await _tune_com2(float(msg["freq_mhz"]))
    elif t == "new_flight":
        await _new_flight()
    elif t == "set_config":
        await _set_config(msg.get("config", {}))
    elif t == "set_ambient":
        await _set_ambient(msg.get("level"), msg.get("rules"))
    elif t == "mic_open":
        await _set_user_speaking(True)
    elif t == "mic_close":
        await _set_user_speaking(False)
    elif t == "set_vfr_weather":
        await _set_vfr_weather()
    elif t == "get_config_status":
        await _broadcast_config_status()


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

        # Pass live COM1 (X-Plane only) so a handed-off station won't answer
        # until the pilot has actually tuned to its frequency. Position (any
        # source) lets the session compute a real taxi route on the ground, and
        # the on-ground/altitude/groundspeed give the controller phase awareness.
        com1 = lat = lon = on_ground = altitude_ft = gs_kts = None
        airspace_note = None
        if _driver is not None:
            st = _driver.state
            if st.is_flight_loaded:
                lat, lon = st.lat, st.lon
                on_ground = st.on_ground > 0.5
                altitude_ft = st.alt_ind_ft
                gs_kts = st.gs_kts
                if _source == "xplane":
                    com1 = st.com1_mhz
                    airspace_note = _airspace_note(st)

        # session.process() is blocking (calls claude subprocess)
        r = await asyncio.to_thread(
            _session.process, text, com1, lat, lon, on_ground, altitude_ft, gs_kts,
            airspace_note)

        # Pilot called a station they haven't tuned to — no reply, just a nudge.
        if r.on_wrong_frequency:
            freq = r.expected_frequency
            await _broadcast(
                "error",
                message=f"No reply — set COM1 to {freq:.3f} to reach {r.pending_station}.",
            )
            return

        # Realism beat: the controller may work one other aircraft first, before
        # turning back to you. Plays on the shared channel, so it finishes before
        # your reply keys up.
        try:
            await _maybe_interject()
        except Exception as e:
            log.debug(f"Ambient interjection skipped: {e}")

        await _broadcast("atc_message", role="atc", text=r.text,
                         model=r.model, timestamp=time.time())

        # Synthesize ATC audio (non-fatal if TTS unavailable). The spoken form
        # is normalized (callsigns → NATO, numbers → digits) so any TTS backend
        # reads it like a controller; the displayed text above stays compact.
        # Played through the half-duplex channel so it never overlaps traffic.
        if _AUDIO_READY:
            try:
                spoken = _radio_text.to_spoken(r.text)
                samples, sr = await asyncio.to_thread(_audio_tts.synthesize, spoken)
                samples      = _audio_radio.apply_radio_fx(samples, sr)
                await _play_on_channel(samples, sr, event="atc_audio",
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

    # Leaving the live X-Plane source — tear down its PTT listener + link state.
    await _set_xplane_connected(False)

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


async def _new_flight():
    global _session, _current_airport, _current_acft, _prev_ptt, _ambient_size, _user_speaking
    log.info("New flight — resetting ATC session")
    _session = None
    _current_airport = None
    _current_acft = None
    _ambient_size = None
    _user_speaking = False
    if _prev_ptt:
        await _broadcast("ptt_end")
    _prev_ptt = False
    await _broadcast("flight_reset")


async def _set_callsign(callsign: str):
    global _session
    callsign = callsign.strip().upper()
    if not callsign:
        return
    log.info(f"Setting callsign → {callsign}")
    if _session:
        _session.callsign = callsign
    if _source == "xplane" and isinstance(_driver, XPlaneRestConnector):
        # acf_tailnum is a fixed 40-byte char array; the write must clear the
        # whole array or the previous tail's trailing chars bleed through.
        b64 = encode_fixed_string(callsign, ACF_TAILNUM_BYTES)
        ok = await asyncio.to_thread(
            _driver.write_dataref, 'sim/aircraft/view/acf_tailnum', b64
        )
        if not ok:
            await _broadcast("error", message=f"Could not write callsign to X-Plane")
    # Broadcast optimistic state update so the UI reflects the change immediately
    if _driver:
        state = _driver.state
        if state.is_flight_loaded:
            d = _state_dict(state)
            d['tail_number'] = callsign
            await _broadcast("state_update", **d)


async def _tune_com1(freq_mhz: float):
    raw = int(round(freq_mhz * 100))
    log.info(f"Tuning COM1 → {freq_mhz:.3f} MHz")
    if _source == "xplane" and isinstance(_driver, XPlaneRestConnector):
        ok = await asyncio.to_thread(
            _driver.write_dataref, 'sim/cockpit/radios/com1_freq_hz', raw
        )
        if not ok:
            await _broadcast("error", message="Could not tune COM1 in X-Plane")


async def _tune_com2(freq_mhz: float):
    raw = int(round(freq_mhz * 100))
    log.info(f"Tuning COM2 → {freq_mhz:.3f} MHz")
    if _source == "xplane" and isinstance(_driver, XPlaneRestConnector):
        ok = await asyncio.to_thread(
            _driver.write_dataref, 'sim/cockpit/radios/com2_freq_hz', raw
        )
        if not ok:
            await _broadcast("error", message="Could not tune COM2 in X-Plane")


async def _on_ptt_change(pressed: bool):
    """Edge callback from the WebSocket PTT listener — broadcast immediately."""
    global _prev_ptt
    if pressed == _prev_ptt:
        return
    _prev_ptt = pressed
    # Gate ambient traffic on the mic too (X-Plane PTT path).
    await _set_user_speaking(pressed)
    if pressed:
        log.info("PTT pressed — recording started")
        await _broadcast("ptt_start")
    else:
        log.info("PTT released — recording stopped")
        await _broadcast("ptt_end")


async def _start_ptt_listener():
    """Start the WebSocket PTT listener if configured and not already running.

    Tied to the X-Plane connection: started on connect, stopped on disconnect,
    so it never spins retrying discovery against a sim that isn't there.
    """
    global _ptt_listener
    if (not config.XPLANE_PTT_DATAREF or _source != "xplane"
            or _ptt_listener is not None):
        return
    # Auto-detects whether XPLANE_PTT_DATAREF names a command
    # (sim/operation/contact_atc_ptt) or a readable dataref (xpilot/ptt).
    _ptt_listener = PTTListener(
        host=config.XPLANE_IP,
        port=config.XPLANE_REST_PORT,
        ptt_source=config.XPLANE_PTT_DATAREF,
        on_change=_on_ptt_change,
    )
    _ptt_listener.start()


async def _stop_ptt_listener():
    global _ptt_listener
    if _ptt_listener is not None:
        await _ptt_listener.stop()
        _ptt_listener = None


def _schedule(coro):
    """Run a coroutine on the main loop from a connector background thread."""
    if _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _loop)
    else:
        coro.close()   # loop gone — drop it without a 'never awaited' warning


async def _set_xplane_connected(connected: bool):
    """Update + broadcast X-Plane link state, and gate the PTT listener on it."""
    global _xplane_connected
    if connected == _xplane_connected:
        return
    _xplane_connected = connected
    await _broadcast("xplane_status", connected=connected)
    await _broadcast_config_status()   # the link is one of the doctor checks
    if connected:
        await _start_ptt_listener()
    else:
        await _stop_ptt_listener()


def _on_xplane_connected():
    """Connector callback (background thread) — X-Plane REST API reachable."""
    log.info("X-Plane connected")
    _schedule(_set_xplane_connected(True))


def _on_xplane_disconnected():
    """Connector callback (background thread) — X-Plane went away."""
    log.info("X-Plane disconnected — stopping PTT listener")
    _schedule(_set_xplane_connected(False))


async def _set_source(source: str):
    global _source, _prev_ptt
    # Reset link + PTT state so we don't emit a spurious ptt_end if the previous
    # source had PTT active at the moment of the switch.
    await _set_xplane_connected(False)
    if _prev_ptt:
        await _broadcast("ptt_end")
    _prev_ptt = False

    if source == "xplane":
        connector = XPlaneRestConnector(
            host=config.XPLANE_IP,
            port=config.XPLANE_REST_PORT,
            on_connected=_on_xplane_connected,
            on_disconnected=_on_xplane_disconnected,
        )
        connector.start()
        global _driver
        _driver = connector
        _source = "xplane"
        # PTT listener is started by _on_xplane_connected once the connector
        # confirms the REST API is reachable, and stopped on disconnect — so it
        # doesn't loop retrying discovery while X-Plane is down.

        await _broadcast("source_change", source="xplane", scenario_name=None)
        log.info(f"Switched to X-Plane REST source ({config.XPLANE_IP}:{config.XPLANE_REST_PORT})")
    else:
        await _broadcast("error", message="Switch to simulated via load_scenario")


async def _set_airport(airport: Airport, scenario: Optional[Scenario] = None):
    global _current_airport, _session

    # The Opus boundary check below takes several seconds. Tell the UI we're
    # loading so it doesn't look ready (and isn't usable) until the session exists.
    await _broadcast("loading", active=True,
                     label=f"Setting up {airport.icao} {airport.name} — running ATC boundary check…")
    try:
        await _set_airport_inner(airport, scenario)
    finally:
        await _broadcast("loading", active=False, label="")


async def _await_live_weather(timeout: float = 12.0, poll: float = 0.5):
    """Wait until X-Plane's weather has actually been read (QNH > 0) before the
    boundary check runs. The flight datarefs can resolve a few seconds after a
    flight loads, and the boundary check picks the active runway from wind/QNH —
    so running it too early gives a wind-blind runway choice. Returns the latest
    FlightState (weather may still be default if X-Plane never supplies it)."""
    deadline = time.monotonic() + timeout
    wx = _driver.state if _driver else None
    while not (wx and wx.qnh_hpa > 0) and time.monotonic() < deadline:
        await asyncio.sleep(poll)
        wx = _driver.state if _driver else None
    return wx


async def _set_airport_inner(airport: Airport, scenario: Optional[Scenario] = None):
    global _current_airport, _session

    global _ambient_size
    _current_airport = airport
    _ambient_size = classify_size(airport)
    log.info(f"Ambient: {airport.icao} classified as a {_ambient_size} field")
    # Load this country's airspace in the background (cached after first run).
    asyncio.create_task(_ensure_airspace(airport.icao))
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
        # Live X-Plane mode — seed conditions from current flight state. Wait for
        # the weather datarefs to be read first (they can lag the position read),
        # so the boundary check below sees real wind/QNH, not defaults.
        if _source == "xplane":
            await _broadcast("loading", active=True,
                             label=f"Reading live weather at {airport.icao}…")
            wx = await _await_live_weather()
            if not (wx and wx.qnh_hpa > 0):
                log.warning("Live weather unavailable after wait — boundary check uses defaults")
        else:
            wx = _driver.state if _driver else None
        session_conditions = {
            airport.icao: {
                'qnh':           wx.qnh_hpa  if wx and wx.qnh_hpa  > 0 else 1013,
                'wind_dir':      int(wx.wind_dir_deg)   if wx else 0,
                'wind_kts':      int(wx.wind_speed_kts) if wx else 0,
                'visibility_km': 10,
            }
        }
        if wx and wx.qnh_hpa > 0:
            log.info(
                f"Live weather: QNH {wx.qnh_hpa} hPa, "
                f"wind {int(wx.wind_dir_deg)}°/{int(wx.wind_speed_kts)} kt"
            )

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
    flat_cond = {
        'wind': f"{session_conditions[airport.icao].get('wind_dir', '?')}° / {session_conditions[airport.icao].get('wind_kts', '?')} kt",
        'qnh':  str(session_conditions[airport.icao].get('qnh', '??')),
        'vis':  f"{session_conditions[airport.icao].get('visibility_km', '?')} km",
        'time': 'check simulator clock',
    }
    log.info(
        f"Boundary check for {airport.icao} "
        f"({airport.name}, {len(airport.runways)} rwys) — "
        f"wind {flat_cond['wind']}, QNH {flat_cond['qnh']}"
    )
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
        atc_callsign  = ctx.get("atc_callsign", "")
        notes         = ctx.get("notes", "")
        log.info(
            f"Boundary check done: runway {active_runway}, "
            f"callsign '{atc_callsign}'"
            + (f", notes: {notes}" if notes else "")
        )
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

async def _xplane_probe_loop():
    """
    Probes the X-Plane REST API every 2 s and auto-activates the xplane source
    when X-Plane is detected. Only runs while source is 'simulated'.
    """
    import json as _json
    from urllib.request import Request as _Req, urlopen as _urlopen

    def _probe() -> str | None:
        """Return X-Plane version string on success, None on failure."""
        base = f'http://{config.XPLANE_IP}:{config.XPLANE_REST_PORT}'
        # /api/capabilities was added in 12.1.4; fall back to datarefs/count for 12.1.1+
        try:
            req = _Req(f'{base}/api/capabilities', headers={'Accept': 'application/json'})
            with _urlopen(req, timeout=1.5) as resp:
                data = _json.loads(resp.read())
                if 'x-plane' in data:
                    return data['x-plane'].get('version', 'unknown')
        except Exception:
            pass
        try:
            req = _Req(f'{base}/api/v1/datarefs/count', headers={'Accept': 'application/json'})
            with _urlopen(req, timeout=1.5) as resp:
                data = _json.loads(resp.read())
                if 'data' in data:
                    return 'unknown (v1 only)'
        except Exception:
            pass
        return None

    log.info(
        f"Probing for X-Plane at "
        f"http://{config.XPLANE_IP}:{config.XPLANE_REST_PORT} every 2 s ..."
    )
    while True:
        if _source != "xplane":
            version = await asyncio.to_thread(_probe)
            if version is not None:
                log.info(f"X-Plane detected (version {version}) — activating live source")
                await _set_source("xplane")
        await asyncio.sleep(2.0)


# Control-zone proxy radius (NM). apt.dat carries no airspace boundaries, so we
# approximate "still inside the control zone" by distance from the field — a CTR
# is typically ~5 NM. Inside this, an airborne aircraft is not handed off.
_CTR_RADIUS_NM = 6.0


def _com1_matches_airport(airport: Airport, com1_mhz: float) -> bool:
    """Is COM1 tuned to one of this airport's published frequencies?"""
    if not com1_mhz:
        return False
    return any(abs(f.freq_mhz - com1_mhz) <= 0.02 for f in airport.frequencies)


async def _ensure_airspace(icao: str):
    """Load the OpenAIP airspace for the airport's country in the background.
    No-ops when disabled, already loaded for this country, or already loading."""
    global _airspace_db, _airspace_country, _airspace_loading
    if not config.AIRSPACE_ENABLED:
        return
    cc = airspace_openaip.country_for_icao(icao)
    if cc is None or cc == _airspace_country or _airspace_loading:
        return
    _airspace_loading = True
    try:
        db = await asyncio.to_thread(airspace_openaip.load_country, cc)
        _airspace_db = db
        _airspace_country = cc if db is not None else None
        if db is not None:
            log.info(f"Airspace ready for {cc.upper()}: {len(db)} volumes")
    except Exception as e:
        log.warning(f"Airspace load failed for {cc}: {e}")
    finally:
        _airspace_loading = False


def _current_ctr(state: FlightState):
    """The CTR the aircraft is currently in, or None (airspace data permitting)."""
    if _airspace_db is None:
        return None
    try:
        return _airspace_db.in_ctr(state.lat, state.lon, state.alt_ind_ft)
    except Exception:
        return None


def _airspace_note(state: FlightState) -> Optional[str]:
    """A short note on the controlling airspace at the aircraft, for the
    controller's situational text — e.g. 'in CTR HANNOVER (Class D, SFC-2500 ft)'."""
    if _airspace_db is None:
        return None
    try:
        a = _airspace_db.controlling(state.lat, state.lon, state.alt_ind_ft)
    except Exception:
        return None
    return f"in {a.describe()}" if a is not None else None


def _ctr_belongs_to(ctr, airport: Airport) -> bool:
    """Does this CTR belong to the given airport? Matched by city name."""
    if ctr is None or airport is None:
        return False
    city = airport.name.split()[0].upper()
    return city in ctr.name.upper()


def _should_adopt_airport(new_airport: Airport, state: FlightState) -> bool:
    """Decide whether to hand the session over to a newly-nearest airport.

    The detector finds the closest field by position — wrong to act on while you
    climb out of your departure field and merely overfly a neighbour: you're
    still in its zone, on its frequency, and haven't been released. Rules:

      - On the ground at the new field → adopt (you taxied in / landed there).
      - Explicitly tuned to the new field's frequency → adopt (clear intent).
      - Inside the NEW field's CTR (real OpenAIP boundary) → adopt (arriving).
      - Otherwise, while airborne, do NOT hand over if any of these hold:
          * still inside the current field's control zone — real CTR boundary
            when airspace data is available, else a distance proxy, or
          * still tuned to one of the current field's frequencies, or
          * the controller hasn't cleared a frequency change / CTR exit yet.
    """
    cur = _current_airport
    if cur is None:
        return True
    if state.on_ground > 0.5:
        return True
    if _source == "xplane" and _com1_matches_airport(new_airport, state.com1_mhz):
        return True   # pilot has dialled the new field in — honour it

    if _airspace_db is not None:
        # Real airspace boundaries.
        ctr = _current_ctr(state)
        if ctr is not None:
            # Inside the new field's zone → adopt (arriving). Inside any other
            # zone (current field's or a third one) → stay; you're still in
            # controlled airspace and shouldn't be yanked off it.
            return _ctr_belongs_to(ctr, new_airport)
        # Outside all CTRs → you've genuinely left; fall through to the freq /
        # release checks below.
    else:
        # No airspace data — approximate the control zone by distance.
        try:
            nm, _ = _dist_bearing_nm(cur.lat, cur.lon, state.lat, state.lon)
        except Exception:
            nm = _CTR_RADIUS_NM + 1
        if nm <= _CTR_RADIUS_NM:
            return False

    if _source == "xplane" and _com1_matches_airport(cur, state.com1_mhz):
        return False  # still working the current station's frequency
    if _session is not None and not _session.freq_change_cleared:
        return False  # not yet cleared to leave the frequency
    return True


async def _state_poll_loop():
    global _startup_vfr_done
    last_airport_check = 0.0

    while True:
        if _driver:
            try:
                state = _driver.state
                if state.is_flight_loaded:
                    await _broadcast("state_update", **_state_dict(state))

                    # VFR_WEATHER_ON_START: once the flight + weather are loaded,
                    # apply the VFR day a single time (in the background so the
                    # poll loop keeps running).
                    if (config.VFR_WEATHER_ON_START and not _startup_vfr_done
                            and _source == "xplane" and _xplane_connected
                            and state.qnh_hpa > 0):
                        _startup_vfr_done = True
                        log.info("VFR_WEATHER_ON_START — applying VFR day at startup")
                        asyncio.create_task(_set_vfr_weather())

                    # Re-check airport every 10 s
                    now = time.time()
                    if _airport_db and now - last_airport_check > 10.0:
                        last_airport_check = now
                        airport = _airport_db.nearest(state.lat, state.lon)
                        if (airport
                                and airport.icao != (_current_airport.icao if _current_airport else "")
                                and _should_adopt_airport(airport, state)):
                            log.info(
                                f"Airport detected: {airport.icao} ({airport.name}) "
                                f"from position {state.lat:.4f},{state.lon:.4f}"
                            )
                            await _set_airport(airport)

                    # PTT is handled out-of-band by the WebSocket PTTListener
                    # (see _on_ptt_change), not polled here — polling at 2 Hz
                    # clipped transmissions and missed quick taps.

            except Exception as e:
                log.debug(f"State poll error: {e}")

        await asyncio.sleep(0.5)   # 2 Hz UI updates


async def _heartbeat_loop():
    while True:
        await _broadcast("backend_status",
                         uptime_s=int(time.time() - _start_time),
                         source=_source,
                         airport_loaded=_current_airport is not None,
                         audio_ready=_AUDIO_READY,
                         xplane_connected=_xplane_connected)
        # Keep the doctor live (e.g. picks up a newly-installed claude CLI).
        await _broadcast_config_status()
        await asyncio.sleep(5.0)


# ------------------------------------------------------------------ #
# Entry point

def _find_apt_dat_or_none() -> Optional[Path]:
    for p in config.APT_DAT_PATHS:
        if p.exists():
            return p
    return None


async def _ensure_airport_db(force: bool = False) -> bool:
    """Parse apt.dat into the airport DB if we can find it. Non-fatal.

    Deferred so the backend can start before the X-Plane path is configured;
    called again from _set_config once the user provides the path.
    """
    global _airport_db, _airport_db_loading
    if _airport_db is not None and not force:
        return True
    if _airport_db_loading:
        return False
    apt = _find_apt_dat_or_none()
    if apt is None:
        return False
    _airport_db_loading = True
    try:
        log.info(f"Loading airport database from {apt} …")
        airports = await asyncio.to_thread(parse_apt_dat, apt)
        _airport_db = AirportDB(airports)
        log.info(f"{len(airports):,} airports ready")
    except Exception as e:
        log.warning(f"Airport DB load failed: {e}")
        return False
    finally:
        _airport_db_loading = False
    await _broadcast_config_status()
    return True


def _config_status() -> dict:
    """The 'doctor' — what's configured and what's missing, as data the GUI renders."""
    claude_ok = shutil.which("claude") is not None
    el_key    = bool(config.ELEVENLABS_API_KEY)
    voice_ok  = el_key or bool(config.OPENAI_API_KEY)
    apt       = _find_apt_dat_or_none()
    apt_ok    = apt is not None

    checks = {
        "claude": {
            "ok": claude_ok, "label": "Claude CLI",
            "detail": "ready" if claude_ok
                      else "not found on PATH — install from claude.ai/code",
        },
        "voice": {
            "ok": voice_ok, "label": "Voice provider",
            "detail": ("ElevenLabs key set" if el_key
                       else "OpenAI key set" if voice_ok
                       else "add your ElevenLabs API key for speech"),
        },
        "xplane_path": {
            "ok": apt_ok, "label": "X-Plane data (apt.dat)",
            "detail": str(apt) if apt_ok
                      else "not found — set your X-Plane install path",
        },
        "xplane_link": {
            "ok": _xplane_connected, "label": "X-Plane connection",
            "detail": "connected" if _xplane_connected
                      else "optional — start a flight in X-Plane, or load a scenario",
        },
    }
    return {
        "checks": checks,
        # Minimum to run: Claude (LLM) + a voice provider + airport data.
        # A live X-Plane link is optional — scenarios work offline.
        "configured": claude_ok and voice_ok and apt_ok,
        "current": {
            "xplane_path":        str(config.XPLANE_BASE),
            "xplane_ptt_dataref": config.XPLANE_PTT_DATAREF,
            "has_elevenlabs":     el_key,
            "has_openai":         bool(config.OPENAI_API_KEY),
            "ambient_level":      config.AMBIENT_TRAFFIC_LEVEL,
            "ambient_rules":      list(config.AMBIENT_TRAFFIC_RULES),
        },
    }


async def _broadcast_config_status():
    await _broadcast("config_status", **_config_status())


async def _set_config(cfg: dict):
    """Persist config from the Settings view (keys, X-Plane path) and reload."""
    if cfg.get("elevenlabs_api_key") is not None:
        config.set_env("ELEVENLABS_API_KEY", cfg["elevenlabs_api_key"])
    if cfg.get("openai_api_key") is not None:
        config.set_env("OPENAI_API_KEY", cfg["openai_api_key"])
    if cfg.get("xplane_ptt_dataref") is not None:
        config.set_env("XPLANE_PTT_DATAREF", cfg["xplane_ptt_dataref"])
    if cfg.get("xplane_path"):
        config.set_env("XPLANE_PATH", cfg["xplane_path"])
        config.set_xplane_path(cfg["xplane_path"])
        await _ensure_airport_db(force=True)
    if cfg.get("ambient_level") is not None or cfg.get("ambient_rules") is not None:
        await _set_ambient(cfg.get("ambient_level"), cfg.get("ambient_rules"))

    # Re-verify voice now that keys may have changed (logs the active backend).
    if _AUDIO_READY:
        await asyncio.to_thread(_audio_tts.check)
    await _broadcast_config_status()
    log.info("Configuration updated via Settings.")


async def _set_vfr_weather():
    """Set a clear-ish VFR day on the live sim: real weather frozen (wind,
    pressure, temperature kept real), a few scattered clouds, visibility above
    5 sm, and the clock at local noon. Only works against a connected X-Plane."""
    if _source != "xplane" or not _xplane_connected:
        await _broadcast("vfr_weather", ok=False,
                         message="Connect to X-Plane first — weather can only be set on the live sim.")
        return
    from xplane import sim_control
    await _broadcast("vfr_weather", ok=None, busy=True,
                     message="Downloading real weather, then setting a VFR day…")
    try:
        result = await asyncio.to_thread(
            sim_control.apply_vfr_day, config.XPLANE_IP, config.XPLANE_REST_PORT)
        log.info(f"VFR weather applied: {result}")
        await _broadcast(
            "vfr_weather", ok=True, busy=False,
            message=(f"VFR day set — {result['clouds']}, visibility "
                     f"{result['visibility_sm']} sm, local noon. "
                     f"Wind, pressure and temperature kept real."))
        # The freeze re-downloaded real weather, which can nudge wind/QNH.
        # Refresh the session's stored conditions (no LLM) and re-run the
        # boundary check only if the wind veered enough to change the runway.
        await _refresh_session_weather()
    except Exception as e:
        log.warning(f"VFR weather failed: {e}")
        await _broadcast("vfr_weather", ok=False, busy=False,
                         message=f"Could not set weather: {e}")


def _angular_diff(a: float, b: float) -> float:
    """Smallest angle (degrees) between two compass bearings."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


async def _refresh_session_weather():
    """After the sim weather changes, update the active session's stored QNH/wind
    from the live state (cheap, no LLM) so the controller reports current values.
    Re-runs the Opus boundary check ONLY when the wind direction shifted enough
    (>30°, non-calm) to plausibly change the active runway. Chat and phase are
    left intact."""
    if _session is None or _current_airport is None or _source != "xplane":
        return
    wx = _driver.state if _driver else None
    if not (wx and wx.qnh_hpa > 0):
        return
    icao = _current_airport.icao
    cond = _session.conditions.get(icao)
    if not cond:
        return

    old_dir = cond.get('wind_dir', 0)
    new_dir = int(wx.wind_dir_deg)
    new_kts = int(wx.wind_speed_kts)
    cond['qnh'] = wx.qnh_hpa
    cond['wind_dir'] = new_dir
    cond['wind_kts'] = new_kts
    log.info(f"Session weather refreshed: QNH {wx.qnh_hpa}, wind {new_dir}°/{new_kts} kt")

    if new_kts < 3 or _angular_diff(old_dir, new_dir) <= 30:
        return   # wind essentially unchanged → keep the current runway

    log.info(f"Wind veered {old_dir}°→{new_dir}° — re-running boundary check")
    flat_cond = {
        'wind': f"{new_dir}° / {new_kts} kt",
        'qnh':  str(wx.qnh_hpa),
        'vis':  '10 km',
        'time': 'check simulator clock',
    }
    try:
        ctx = await asyncio.to_thread(
            atc_engine.boundary_check,
            airport=_current_airport, acft=_current_acft,
            callsign=_session.callsign, conditions=flat_cond,
            model=config.MODEL_BOUNDARY)
        new_runway = ctx.get('active_runway') or cond.get('active_runway', '')
    except Exception as e:
        log.warning(f"Re-run boundary check failed: {e}")
        return

    if new_runway and new_runway != cond.get('active_runway'):
        cond['active_runway'] = new_runway
        log.info(f"Active runway changed to {new_runway} after wind shift")
        await _broadcast("phase_change",
                         phase=_session.phase.value,
                         station=_session.current_station.value,
                         atc_callsign=_session._atc_callsign(),
                         active_runway=new_runway, notes="")


async def run():
    global _airport_db, _driver, _start_time, _tx_lock, _loop
    global _channel_lock, _ambient_lib, _ambient_planner
    _start_time = time.time()
    _tx_lock      = asyncio.Lock()
    _channel_lock = asyncio.Lock()   # half-duplex radio: one transmission at a time
    _loop         = asyncio.get_running_loop()   # for thread-safe scheduling from connector callbacks

    _startup_banner()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    acdb.load()   # small aircraft DB — always available

    # Ambient traffic library + pacing. Loaded even if audio is off, so the
    # doctor can report the level; the loop self-gates on audio + X-Plane.
    extra = Path(config.AMBIENT_LIBRARY_DIR) if config.AMBIENT_LIBRARY_DIR else None
    _ambient_lib     = load_library(extra)
    _ambient_planner = AmbientPlanner(config.AMBIENT_TRAFFIC_LEVEL)
    log.info(
        f"Ambient traffic: level={_ambient_planner.level.name}, "
        f"{len(_ambient_lib)} interactions, rules={config.AMBIENT_TRAFFIC_RULES}"
    )

    # Load airports if we can find apt.dat; otherwise start unconfigured and let
    # the Settings view supply the X-Plane path.
    if not await _ensure_airport_db():
        log.warning(
            "apt.dat not found — starting unconfigured. Set your X-Plane path "
            "in the app's Settings view."
        )

    if _AUDIO_READY:
        await asyncio.to_thread(_audio_stt.preload)   # STT: loads model or verifies cloud key
        await asyncio.to_thread(_audio_tts.check)     # TTS: log backend + verify key

    # Start idle — the probe loop will auto-detect X-Plane within 2 s.
    # Scenarios can still be loaded manually via the UI's scenario drawer.
    _driver = ScenarioSimulator()

    log.info(f"Backend listening on ws://{HOST}:{PORT}")
    async with websockets.serve(_client_handler, HOST, PORT):
        await asyncio.gather(
            _state_poll_loop(),
            _heartbeat_loop(),
            _xplane_probe_loop(),
            _ambient_loop(),
        )


if __name__ == "__main__":
    # Ctrl-C cancels the gathered loops; asyncio.run() then re-raises
    # KeyboardInterrupt. Swallow it so shutdown is one clean line, not a trace.
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped. Clear skies.")
