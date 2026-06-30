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
  ambient_noise    audio (base64 WAV), kind ("squelch"|"static"|"chatter"|
                   "recording") — background radio atmosphere between
                   transmissions (no text). "recording" = a real LiveATC clip
  liveatc_status   enabled, icao, status ("off"|"idle"|"searching"|"ready"|
                   "none"|"error"), feeds, clips, message — historical traffic
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
  flightplan_loaded route, summary, total_nm, stage, fis{callsign,freq_mhz},
                   waypoints[{ident,kind,name,lat,lon,controlled?}]  [staged journey]
  flightplan_stage stage ("departure"|"enroute"|"arrival"|"arrival_ground"),
                   station, atc_callsign — the service now working you
  flightplan_cleared (no payload) — the active plan was cleared
  error            message

Client → server message types:
  pilot_transmission  text
  pilot_audio         audio (base64 WAV from mic)             [if AUDIO_ENABLED]
  load_scenario       scenario (dict matching Scenario.to_dict())
  load_flightplan     route ("EDLI OSN EDDG"), controlled_overrides? ({ICAO:bool}),
                      callsign? — stage a journey from the route's departure field
  clear_flightplan    (no payload) — drop the active plan
  set_source          source ("xplane"|"simulated")
  tune_com1/tune_com2 freq_mhz — set a radio. In simulated mode this mutates the
                      simulator so handoff gating + party-line react like X-Plane
  move_aircraft       lat?, lon?, alt_ft?, heading?, on_ground?, gs_kts?, ias_kts?
                      — teleport the simulated aircraft (any subset of fields)
  route_jump          to ("stand"|"departure"|"enroute"|"arrival"|"final"|
                      "landed") — jump to a named stage of the staged journey
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
from atc.session import ATCSession, Station, Phase, _dist_bearing_nm
from traffic.library import (
    load_library, classify_size, render as _render_interaction,
    RenderContext, InteractionLibrary,
)
from traffic.ambient import AmbientPlanner
from traffic.world import TrafficWorld
from traffic import liveatc as _liveatc_mod
from airspace.database import AirspaceDB, Airspace
from airspace import openaip as airspace_openaip
from navigation.navaids import NavaidDB, parse_nav_data
from flightplan.plan import FlightPlan, parse_route, RouteError, field_service_freq
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
_traffic_world: Optional[TrafficWorld] = None   # stateful roster of other aircraft

# ── Historical LiveATC traffic (opt-in background texture) ───────────────────
_liveatc = None                          # liveatc.LiveATCFeed for current airport
_liveatc_icao: Optional[str] = None      # ICAO it was loaded for
_liveatc_loading: bool = False
_channel_lock: Optional[asyncio.Lock] = None   # half-duplex radio: one TX at a time
_user_speaking: bool = False             # pilot is keying the mic → no traffic

# ── Airspace (OpenAIP) ───────────────────────────────────────────────────────
_airspace_db: Optional[AirspaceDB] = None    # airspace for the current country
_airspace_country: Optional[str] = None      # ISO code currently loaded
_airspace_loading: bool = False              # a load is in flight

# ── Flight plan (the staged journey) ─────────────────────────────────────────
_navaid_db: Optional[NavaidDB] = None        # VOR/NDB/fix lookup (lazy)
_navaid_db_loading: bool = False
_flightplan: Optional[FlightPlan] = None     # active plan, or None
# Where we are in the plan: which service is working us. One-way progression
# departure → enroute → arrival → arrival_ground, advanced by live position so a
# transition fires once, not on every poll.
_fp_stage: Optional[str] = None
_fp_rng = random.Random()                    # FIS director event selection

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


def _data_source_live() -> bool:
    """True when a data source is actively feeding flight state — a connected
    X-Plane, or a loaded scenario simulator. Gates the party line, the tuned-
    station read and the interjection beat so the simulator behaves like a live
    sim while debugging offline."""
    if _source == "xplane":
        return _xplane_connected
    return isinstance(_driver, ScenarioSimulator) and _driver.scenario is not None


def _tuned_station() -> Optional[Station]:
    """Which ATC station the live COM1 is tuned to, matched against the airport's
    published frequencies. Falls back to the session's current station when the
    dial doesn't match anything (or we have no live radio)."""
    if _session is None:
        return None
    # In flight-plan mode the staged service wins over the dial: the plan has
    # already handed you departure→FIS→arrival by position, so the party-line
    # must match that — otherwise, still tuned to your departure field, you'd
    # keep hearing its traffic while the controller you're talking to is FIS.
    if _flightplan is not None:
        return _session.current_station
    if _driver is not None:
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
        and _data_source_live()
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
        # Name the en-route service from the flight plan (e.g. "Bremen
        # Information") so ambient traffic calls the same FIS you're working.
        atc_callsign = _flightplan.fis.callsign if _flightplan is not None else "Information"
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


# Airport stations whose traffic is driven by the stateful world (persistent
# aircraft, follow-through). En-route FIS and uncontrolled CTAF keep using the
# library of one-off exchanges.
_WORLD_STATIONS = {"ground", "tower", "approach", "radar"}


def _next_ambient(plan):
    """The next party-line exchange for the current situation. Airport stations
    come from the stateful traffic world so aircraft persist and promises get
    kept; FIS/CTAF fall back to the one-off interaction library."""
    station_name, size, enroute, rules, ctx = plan
    if (not enroute and station_name in _WORLD_STATIONS
            and _traffic_world is not None):
        return _traffic_world.next_exchange(station_name, ctx)
    it = _ambient_lib.pick(station=station_name, size=size, enroute=enroute,
                           rules=rules, rng=_ambient_rng) if _ambient_lib else None
    return _render(it, ctx) if it is not None else None


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


def _liveatc_status_dict() -> dict:
    fb = _liveatc
    return {
        "enabled": bool(config.LIVEATC_ENABLED),
        "icao": _liveatc_icao,
        "status": fb.status if fb is not None else ("idle" if config.LIVEATC_ENABLED else "off"),
        "feeds": len(fb.feeds) if fb is not None else 0,
        "clips": len(fb.clips) if fb is not None else 0,
        "message": fb.message if fb is not None else "",
    }


async def _broadcast_liveatc_status():
    await _broadcast("liveatc_status", **_liveatc_status_dict())


async def _ensure_liveatc(icao: str):
    """Fetch historical LiveATC clips for the airport in the background. Best-
    effort and frequently empty (no feed / gated archive); never fatal. Mirrors
    _ensure_airspace's lazy-load shape."""
    global _liveatc, _liveatc_icao, _liveatc_loading
    if not config.LIVEATC_ENABLED:
        return
    if _liveatc_loading or (_liveatc_icao == icao and _liveatc is not None
                            and _liveatc.status in ("ready", "none")):
        return
    _liveatc_loading = True
    _liveatc_icao = icao
    await _broadcast("liveatc_status", enabled=True, icao=icao,
                     status="searching", feeds=0, clips=0,
                     message=f"Searching LiveATC for {icao}…")
    try:
        fb = await asyncio.to_thread(
            _liveatc_mod.fetch_clips, icao,
            cookie=config.LIVEATC_COOKIE,
            lookback_blocks=config.LIVEATC_LOOKBACK_BLOCKS,
            target_sr=_audio_radio.BACKGROUND_SR if _AUDIO_READY else 16_000)
        _liveatc = fb
        log.info(f"LiveATC {icao}: {fb.status} — {fb.message}")
    except Exception as e:
        log.warning(f"LiveATC load failed for {icao}: {e}")
        _liveatc = None
    finally:
        _liveatc_loading = False
    await _broadcast_liveatc_status()


async def _play_liveatc_segment() -> bool:
    """Play one real recorded clip as background texture on the half-duplex
    channel — no text, no bubble. Returns True if a clip played."""
    fb = _liveatc
    if not _AUDIO_READY or fb is None or not fb.clips or _user_speaking:
        return False
    clip = fb.random_clip(_ambient_rng)
    if clip is None:
        return False
    try:
        # Light radio FX so the recording sits in the same timbre as the synthetic
        # traffic (bandpass + a touch of crackle); it's already real radio audio.
        samples = await asyncio.to_thread(_audio_radio.apply_radio_fx, clip, fb.sr)
    except Exception as e:
        log.debug(f"LiveATC FX failed: {e}")
        samples = clip
    if _user_speaking:
        return False
    await _play_on_channel(samples, fb.sr, event="ambient_noise", kind="recording")
    return True


def _liveatc_available() -> bool:
    return (config.LIVEATC_ENABLED and _liveatc is not None
            and bool(_liveatc.clips))


async def _maybe_interject():
    """After the pilot transmits, sometimes work one other aircraft before
    turning back to them. Called inside the transmission lock, before the real
    reply is broadcast, so the chat reads: pilot → other traffic → your reply."""
    if not _AUDIO_READY or _ambient_planner is None or not _ambient_planner.enabled:
        return
    if not _data_source_live() or _session is None:
        return
    if not _ambient_planner.should_interject():
        return
    plan = _ambient_plan()
    if not plan:
        return
    rendered = _next_ambient(plan)
    if rendered and rendered.lines:
        log.info(f"Ambient interjection before reply: "
                 f"{rendered.interaction.id} ({rendered.callsign})")
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
            # When historical LiveATC clips are loaded, some of those beats are a
            # real recorded burst instead of synthetic squelch/static.
            if _ambient_planner.should_emit_atmosphere(1.0):
                if _liveatc_available() and _ambient_rng.random() < config.LIVEATC_MIX:
                    if not await _play_liveatc_segment():
                        await _play_background(_ambient_planner.pick_atmosphere())
                else:
                    await _play_background(_ambient_planner.pick_atmosphere())
        if not _ambient_active():
            continue
        plan = _ambient_plan()
        if not plan:
            continue
        rendered = _next_ambient(plan)
        if not rendered or not rendered.lines or not _ambient_active():
            continue
        log.debug(f"Ambient traffic: {rendered.interaction.id} "
                  f"on {plan[0]} ({rendered.callsign})")
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
    Station.CTAF:  "ctaf",   # uncontrolled aerodrome — self-announce blind calls
}

# Station enum → spoken controller suffix (for the ambient controller callsign).
_AMBIENT_STATION_LABEL: dict = {
    Station.GND:   "Ground",
    Station.TWR:   "Tower",
    Station.APP:   "Approach",
    Station.DEP:   "Departure",
    Station.RADAR: "Radar",
    Station.FIS:   "Information",
    Station.CTAF:  "Traffic",   # blind calls address "<field> Traffic", no controller
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
    await _send_to(ws, "liveatc_status", **_liveatc_status_dict())
    if _driver:
        await _send_to(ws, "state_update", **_state_dict(_driver.state))
    if _current_airport:
        await _send_to(ws, "airport_detected", **_airport_dict(_current_airport))
    if _flightplan is not None:
        await _send_to(ws, "flightplan_loaded", **_flightplan_dict(_flightplan))
        if _session is not None:
            await _send_to(ws, "flightplan_stage", stage=_fp_stage,
                           station=_session.current_station.value,
                           atc_callsign=_session._atc_callsign(),
                           expected_freq=_fp_expected_freq())
    history = _session._history if _session else []
    for entry in history:
        if entry.get("pilot"):     # proactive FIS calls carry no pilot line
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
    elif t == "load_flightplan":
        await _load_flightplan(msg.get("route", ""),
                               msg.get("controlled_overrides"),
                               msg.get("callsign"))
    elif t == "clear_flightplan":
        await _clear_flightplan()
    elif t == "set_source":
        await _set_source(msg["source"])
    elif t == "set_callsign":
        await _set_callsign(msg.get("callsign", ""))
    elif t == "tune_com1":
        await _tune_com1(float(msg["freq_mhz"]))
    elif t == "tune_com2":
        await _tune_com2(float(msg["freq_mhz"]))
    elif t == "move_aircraft":
        await _move_aircraft(msg)
    elif t == "route_jump":
        await _route_jump(msg.get("to"))
    elif t == "new_flight":
        await _new_flight()
    elif t == "set_config":
        await _set_config(msg.get("config", {}))
    elif t == "preview_voice":
        await _preview_voice()
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
                # COM1 gates handoffs (a handed-off station won't answer until
                # you've actually dialled it). Honour it for the simulator too,
                # but only once a frequency has been set — an untuned radio (0)
                # passes None so handoffs stay lenient, as on the CLI.
                com1 = st.com1_mhz or None
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
    global _flightplan, _fp_stage

    try:
        scenario = Scenario.from_dict(data)
    except (KeyError, TypeError) as e:
        await _broadcast("error", message=f"Invalid scenario: {e}")
        return

    _current_airport = None
    _session = None
    if _flightplan is not None:
        _flightplan = None
        _fp_stage = None
        await _broadcast("flightplan_cleared")

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
    global _flightplan, _fp_stage, _traffic_world, _liveatc, _liveatc_icao
    log.info("New flight — resetting ATC session")
    _session = None
    _current_airport = None
    _current_acft = None
    _ambient_size = None
    _traffic_world = None
    _liveatc = None
    _liveatc_icao = None
    _user_speaking = False
    if _flightplan is not None:
        _flightplan = None
        _fp_stage = None
        await _broadcast("flightplan_cleared")
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


async def _tune_com(com: int, freq_mhz: float):
    """Tune COM1 (com=1) or COM2 (com=2). In live mode this writes the X-Plane
    dataref; in simulated mode it mutates the simulator's FlightState so the
    handoff gating and party-line react exactly as they would against X-Plane."""
    raw = int(round(freq_mhz * 100))
    dataref = 'sim/cockpit/radios/com2_freq_hz' if com == 2 else 'sim/cockpit/radios/com1_freq_hz'
    log.info(f"Tuning COM{com} → {freq_mhz:.3f} MHz")
    if _source == "xplane" and isinstance(_driver, XPlaneRestConnector):
        ok = await asyncio.to_thread(_driver.write_dataref, dataref, raw)
        if not ok:
            await _broadcast("error", message=f"Could not tune COM{com} in X-Plane")
    elif isinstance(_driver, ScenarioSimulator):
        _driver.tune(com, freq_mhz)
        await _broadcast("state_update", **_state_dict(_driver.state))


async def _tune_com1(freq_mhz: float):
    await _tune_com(1, freq_mhz)


async def _tune_com2(freq_mhz: float):
    await _tune_com(2, freq_mhz)


# ------------------------------------------------------------------ #
# Simulated-mode movement — "fly" the aircraft without X-Plane
#
# The simulator's FlightState is the same thing the poll loop reads, so moving
# the aircraft here drives flight-plan progression, airport adoption, handoff
# gating and the party-line exactly as a live X-Plane would. Two entry points:
# move_aircraft (explicit lat/lon/alt) and route_jump (symbolic stage presets
# computed from the loaded plan or the current airport).

_EARTH_R_NM = 3440.065


def _project_nm(lat: float, lon: float, bearing_deg: float, dist_nm: float) -> tuple[float, float]:
    """Point dist_nm along bearing_deg from (lat, lon), on a spherical earth."""
    br = math.radians(bearing_deg)
    d = dist_nm / _EARTH_R_NM
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d)
                     + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat1),
                             math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def _point_along_route(fp: FlightPlan, fraction: float) -> tuple[float, float, float]:
    """(lat, lon, course) at `fraction` of the route's total distance."""
    wps = fp.waypoints
    segs = [_dist_bearing_nm(a.lat, a.lon, b.lat, b.lon)
            for a, b in zip(wps, wps[1:])]
    total = sum(nm for nm, _ in segs)
    target = max(0.0, min(1.0, fraction)) * total
    acc = 0.0
    for (a, b), (nm, brg) in zip(zip(wps, wps[1:]), segs):
        if nm <= 1e-6:
            continue
        if acc + nm >= target:
            lat, lon = _project_nm(a.lat, a.lon, brg, target - acc)
            return lat, lon, brg
        acc += nm
    last = wps[-1]
    return last.lat, last.lon, (segs[-1][1] if segs else 0.0)


async def _after_sim_move():
    """After the simulator moves, broadcast the new state and run the same
    position-driven logic the poll loop would — immediately, so transitions
    fire without waiting for the next 4 s tick."""
    if not isinstance(_driver, ScenarioSimulator):
        return
    state = _driver.state
    await _broadcast("state_update", **_state_dict(state))
    if not state.is_flight_loaded:
        return
    if _flightplan is not None:
        await _flightplan_progress(state)
    elif _airport_db is not None:
        airport = _airport_db.nearest(state.lat, state.lon)
        if (airport
                and airport.icao != (_current_airport.icao if _current_airport else "")
                and _should_adopt_airport(airport, state)):
            log.info(f"Sim move → adopting {airport.icao} ({airport.name})")
            await _set_airport(airport)


async def _move_aircraft(msg: dict):
    """Explicit position set from the UI. Any subset of fields; the rest hold."""
    if not isinstance(_driver, ScenarioSimulator):
        await _broadcast("error", message="Move only works in simulated mode — load a scenario first.")
        return
    _driver.set_position(
        lat=msg.get("lat"), lon=msg.get("lon"), alt_ft=msg.get("alt_ft"),
        heading=msg.get("heading"), on_ground=msg.get("on_ground"),
        gs_kts=msg.get("gs_kts"), ias_kts=msg.get("ias_kts"))
    await _after_sim_move()


def _airport_elev_ft(w_or_ap) -> float:
    """Field elevation from a Waypoint (its .airport) or an Airport, else 0."""
    ap = getattr(w_or_ap, "airport", w_or_ap)
    return float(getattr(ap, "elevation_ft", 0) or 0)


async def _route_jump(target: Optional[str]):
    """Teleport the simulated aircraft to a named stage of the journey. With a
    flight plan loaded these map onto the staged services (stand → departure
    climb-out → en route → arrival → short final → landed); without one they're
    relative to the current airport."""
    if not isinstance(_driver, ScenarioSimulator):
        await _broadcast("error", message="Route jump only works in simulated mode.")
        return
    target = (target or "").strip().lower()

    if _flightplan is not None:
        fp = _flightplan
        dep, dest = fp.departure, fp.destination
        after_dep = fp.waypoints[1] if len(fp.waypoints) > 1 else dest
        before_dest = fp.waypoints[-2] if len(fp.waypoints) > 1 else dep
        dep_elev = _airport_elev_ft(dep)
        dest_elev = _airport_elev_ft(dest)
        _, out_brg = _dist_bearing_nm(dep.lat, dep.lon, after_dep.lat, after_dep.lon)
        in_nm, in_brg = _dist_bearing_nm(before_dest.lat, before_dest.lon, dest.lat, dest.lon)
        back_brg = (in_brg + 180.0) % 360.0   # from destination, back up the approach

        if target == "stand":
            pos = dict(lat=dep.lat, lon=dep.lon, alt_ft=dep_elev,
                       heading=out_brg, on_ground=True, gs_kts=0.0)
        elif target == "departure":
            lat, lon = _project_nm(dep.lat, dep.lon, out_brg, _FP_DEPARTURE_CLEAR_NM + 1.5)
            pos = dict(lat=lat, lon=lon, alt_ft=dep_elev + 2000,
                       heading=out_brg, on_ground=False, gs_kts=95.0)
        elif target == "enroute":
            lat, lon, crs = _point_along_route(fp, 0.5)
            _, crs = _dist_bearing_nm(lat, lon, dest.lat, dest.lon)
            cruise = max(dep_elev, dest_elev) + 2500
            pos = dict(lat=lat, lon=lon, alt_ft=cruise,
                       heading=crs, on_ground=False, gs_kts=110.0)
        elif target == "arrival":
            d = max(2.0, _FP_ARRIVAL_RANGE_NM - 1.0)
            lat, lon = _project_nm(dest.lat, dest.lon, back_brg, d)
            pos = dict(lat=lat, lon=lon, alt_ft=dest_elev + 2500,
                       heading=in_brg, on_ground=False, gs_kts=100.0)
        elif target == "final":
            lat, lon = _project_nm(dest.lat, dest.lon, back_brg, 2.0)
            pos = dict(lat=lat, lon=lon, alt_ft=dest_elev + 700,
                       heading=in_brg, on_ground=False, gs_kts=70.0)
        elif target == "landed":
            pos = dict(lat=dest.lat, lon=dest.lon, alt_ft=dest_elev,
                       heading=in_brg, on_ground=True, gs_kts=12.0)
        else:
            await _broadcast("error", message=f"Unknown route target '{target}'.")
            return
    elif _current_airport is not None:
        ap = _current_airport
        elev = float(ap.elevation_ft or 0)
        if target in ("stand", "landed"):
            pos = dict(lat=ap.lat, lon=ap.lon, alt_ft=elev,
                       heading=0.0, on_ground=True,
                       gs_kts=(12.0 if target == "landed" else 0.0))
        elif target in ("departure", "enroute", "arrival"):
            # Place 8 NM east, airborne — enough to read as "out of the circuit".
            lat, lon = _project_nm(ap.lat, ap.lon, 90.0, 8.0)
            _, crs = _dist_bearing_nm(lat, lon, ap.lat, ap.lon)
            pos = dict(lat=lat, lon=lon, alt_ft=elev + 2500,
                       heading=crs, on_ground=False, gs_kts=100.0)
        elif target == "final":
            lat, lon = _project_nm(ap.lat, ap.lon, 90.0, 3.0)
            _, crs = _dist_bearing_nm(lat, lon, ap.lat, ap.lon)
            pos = dict(lat=lat, lon=lon, alt_ft=elev + 900,
                       heading=crs, on_ground=False, gs_kts=70.0)
        else:
            await _broadcast("error", message=f"Unknown route target '{target}'.")
            return
    else:
        await _broadcast("error", message="No flight plan or airport to jump to.")
        return

    log.info(f"Route jump → {target}: {pos['lat']:.4f},{pos['lon']:.4f} "
             f"alt {pos['alt_ft']:.0f} ft {'ground' if pos['on_ground'] else 'airborne'}")
    _driver.set_position(**pos)
    await _after_sim_move()


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

    global _ambient_size, _traffic_world
    _current_airport = airport
    _ambient_size = classify_size(airport)
    # Fresh roster for the new field — its traffic shouldn't inherit the last
    # airfield's aircraft. Seeded off the shared ambient RNG for reproducibility.
    _traffic_world = TrafficWorld(_ambient_rng)
    log.info(f"Ambient: {airport.icao} classified as a {_ambient_size} field")
    # Load this country's airspace in the background (cached after first run).
    asyncio.create_task(_ensure_airspace(airport.icao))
    # And, if opted in, historical LiveATC traffic for the field (best-effort).
    if config.LIVEATC_ENABLED:
        asyncio.create_task(_ensure_liveatc(airport.icao))
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

    # Look up destination airport — from the active flight plan first, else the
    # scenario. The flight plan owns the journey when present.
    destination: Optional[Airport] = None
    if _flightplan is not None and _flightplan.destination.airport is not None:
        destination = _flightplan.destination.airport
        dico = destination.icao
        if dico not in session_conditions:
            session_conditions[dico] = {
                'qnh': 1013, 'wind_dir': 0, 'wind_kts': 0, 'visibility_km': 10,
                'active_runway': '',
            }
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
        flight_plan=_flightplan,
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


# ------------------------------------------------------------------ #
# Flight plan — the staged journey
#
# A plan stages the flight the way real VFR ops run it: an uncontrolled
# departure self-announces on CTAF/AFIS, an en-route FIS works the leg and
# follows your position, and a controlled arrival is Tower→Ground. The service
# in charge progresses with your position (not the nearest-airport detector),
# and a director keeps the FIS lively — traffic, situations, and the handoff.

# How far from the departure field counts as "clear of the circuit, now en route".
# Kept short for an uncontrolled field — there's no zone to clear, so FIS picks
# you up soon after you climb out.
_FP_DEPARTURE_CLEAR_NM = 3.0
# How close to the destination flips the en-route FIS to the arrival service.
_FP_ARRIVAL_RANGE_NM = 13.0
# FIS director cadence (seconds between proactive calls), jittered.
_FIS_GAP_MIN, _FIS_GAP_MAX = 70.0, 115.0


def _waypoint_dict(w) -> dict:
    d = {"ident": w.ident, "kind": w.kind, "name": w.name,
         "lat": w.lat, "lon": w.lon}
    if w.is_airport:
        d["controlled"] = bool(w.controlled)
    return d


def _field_service(w) -> tuple[str, Optional[float]]:
    """(callsign, freq_mhz) for the contact at a route endpoint field. A
    controlled field hands its first-contact service (Ground, else Tower); an
    uncontrolled field its self-announce frequency (AFIS 'Information', else
    'Traffic')."""
    ap = w.airport
    if ap is None:
        return (w.ident, None)
    city = ap.name.split()[0]
    if w.controlled:
        gnd, twr, app = ap.freq(53), ap.freq(54), ap.freq(55)
        f = gnd or twr or app
        word = "Ground" if gnd else "Tower" if twr else "Approach"
        return (f"{city} {word}", f.freq_mhz if f else None)
    word = "Information" if ap.frequencies else "Traffic"
    return (f"{city} {word}", field_service_freq(ap))


def _flightplan_services(fp: FlightPlan) -> list:
    """The frequencies for the whole staged journey, in order — what the pilot
    needs at each leg. departure field → en-route FIS → arrival field."""
    dep_cs, dep_freq = _field_service(fp.departure)
    arr_cs, arr_freq = _field_service(fp.destination)
    return [
        {"stage": "departure", "label": dep_cs,        "freq_mhz": dep_freq},
        {"stage": "enroute",   "label": fp.fis.callsign, "freq_mhz": fp.fis.freq_mhz},
        {"stage": "arrival",   "label": arr_cs,        "freq_mhz": arr_freq},
    ]


def _flightplan_dict(fp: FlightPlan) -> dict:
    return {
        "route": fp.route,
        "summary": fp.summary(),
        "total_nm": round(fp.total_nm, 1),
        "stage": _fp_stage,
        "fis": {"callsign": fp.fis.callsign, "freq_mhz": fp.fis.freq_mhz},
        "services": _flightplan_services(fp),
        "waypoints": [_waypoint_dict(w) for w in fp.waypoints],
    }


async def _load_flightplan(route: str, overrides=None, callsign=None):
    """Parse a route string and stage the journey from its departure field."""
    global _flightplan, _fp_stage

    if not route or not route.strip():
        await _broadcast("error", message="Enter a route, e.g. EDLI OSN EDDG.")
        return
    if not await _ensure_airport_db():
        await _broadcast("error",
                         message="No airport database — set your X-Plane path in Settings first.")
        return
    navaid_db = await _ensure_navaid_db()
    overrides = {str(k).upper(): bool(v) for k, v in (overrides or {}).items()}

    try:
        fp = parse_route(route, _airport_db, navaid_db, overrides)
    except RouteError as e:
        await _broadcast("error", message=str(e))
        return

    _flightplan = fp
    _fp_stage = "departure"
    log.info(f"Flight plan loaded: {fp.summary()} ({fp.total_nm:.0f} NM), "
             f"en route {fp.fis.callsign}")
    if callsign:
        await _set_callsign(callsign)

    await _broadcast("flightplan_loaded", **_flightplan_dict(fp))

    # Stage from the departure field. _set_airport_inner reads _flightplan, so the
    # session it builds is plan-aware (CTAF start if the departure is uncontrolled,
    # destination set from the plan).
    await _set_airport(fp.departure.airport)
    # Announce the opening service + the frequency to be on, so the UI can nudge
    # COM1 onto the departure field's frequency from the start.
    await _fp_broadcast_service()


async def _clear_flightplan():
    global _flightplan, _fp_stage
    if _flightplan is None:
        return
    log.info("Flight plan cleared")
    _flightplan = None
    _fp_stage = None
    await _broadcast("flightplan_cleared")


def _fp_active_runway(icao: str) -> str:
    raw = (_session.conditions.get(icao) or {}) if _session else {}
    rwy = str(raw.get("active_runway", "") or "").strip()
    return "" if rwy.lower() in ("", "unknown") else rwy


# Station → apt.dat frequency type code, for resolving the frequency a controlled
# service is on at the current field.
_STATION_FREQ_TYPE = {Station.GND: 53, Station.TWR: 54, Station.APP: 55,
                      Station.RADAR: 56, Station.DEP: 56}


def _fp_expected_freq() -> Optional[float]:
    """The COM1 frequency the pilot should be on for the service now working
    them: the field's CTAF/AFIS at an uncontrolled stop, the plan's FIS frequency
    en route, or the controlled field's Ground/Tower/Approach frequency. None if
    it can't be resolved (then the UI shows no nudge)."""
    if _session is None:
        return None
    ap = _session.current_airport
    st = _session.current_station
    if st == Station.FIS:
        return _flightplan.fis.freq_mhz if _flightplan is not None else None
    if st == Station.CTAF:
        return field_service_freq(ap) if ap is not None else None
    tc = _STATION_FREQ_TYPE.get(st)
    f = ap.freq(tc) if (ap is not None and tc) else None
    return f.freq_mhz if f else None


async def _fp_broadcast_service():
    """Tell the UI which service is now working us (station, callsign, phase).
    Also flips the airport panel to the field the session is now referenced to —
    en route that's the destination, so the panel stops showing the field you
    departed and starts showing where you're going (and the freqs you'll need)."""
    global _current_airport
    if _session is None:
        return
    ap = _session.current_airport
    if ap is not None and (_current_airport is None or ap.icao != _current_airport.icao):
        _current_airport = ap
        await _broadcast("airport_detected", **_airport_dict(ap))
    await _broadcast(
        "phase_change",
        phase=_session.phase.value,
        station=_session.current_station.value,
        atc_callsign=_session._atc_callsign(),
        active_runway=_fp_active_runway(_session.current_airport.icao),
        notes="")
    if _flightplan is not None:
        await _broadcast("flightplan_stage", stage=_fp_stage,
                         station=_session.current_station.value,
                         atc_callsign=_session._atc_callsign(),
                         expected_freq=_fp_expected_freq())


async def _emit_controller_line(text: str, *, model: Optional[str] = None,
                                station: Optional[str] = None):
    """Broadcast a controller-initiated transmission and (if audio is on) speak
    it on the half-duplex channel. Used for proactive FIS calls — it appears in
    the chat as an ATC message and is recorded in session history."""
    if _session is not None:
        _session._history.append({
            "pilot": "", "atc": text, "model": model,
            "station": (station or _session.current_station.value),
        })
    await _broadcast("atc_message", role="atc", text=text, model=model,
                     timestamp=time.time())
    if _AUDIO_READY:
        try:
            spoken = _radio_text.to_spoken(text)
            samples, sr = await asyncio.to_thread(_audio_tts.synthesize, spoken)
            samples = _audio_radio.apply_radio_fx(samples, sr)
            await _play_on_channel(samples, sr, event="atc_audio",
                                   text=text, model=model, timestamp=time.time())
        except Exception as e:
            log.debug(f"FIS TTS failed: {e}")


def _fis_context():
    """(directive-free) context for a proactive FIS call: the kwargs engine.proactive
    needs, drawn from the live session. None if not in a usable state."""
    if _session is None or _flightplan is None or _driver is None:
        return None
    st = _driver.state
    if not st.is_flight_loaded:
        return None
    flight_status = _session._flight_status(
        on_ground=st.on_ground > 0.5, altitude_ft=st.alt_ind_ft,
        gs_kts=st.gs_kts, lat=st.lat, lon=st.lon,
        airspace=_airspace_note(st) if _source == "xplane" else None)
    return dict(
        airport=_session.current_airport, acft=_current_acft,
        callsign=_session.callsign, conditions=_session._flat_conditions(),
        atc_callsign=_session._atc_callsign(), history=_session._history,
        model=config.MODEL_ROUTINE, destination=_session.destination,
        flight_status=flight_status, service_kind="fis",
    )


def _fis_directive(state) -> str:
    """Pick what the FIS should proactively say next, grounded in live geometry.
    Weighted across traffic, a 'situation', a position-report request, and an
    information/weather call so the frequency stays lively and varied."""
    prog = _flightplan.progress(state.lat, state.lon)
    alt = int(round((state.alt_ind_ft or 1500) / 100.0) * 100)
    r = _fp_rng.random()

    if r < 0.46:   # traffic information — the staple of a basic service
        clock = _fp_rng.choice([10, 11, 11, 12, 1, 1, 2, 3, 9])
        dist = _fp_rng.randint(2, 7)
        rel = _fp_rng.choice([
            "opposite direction", "crossing left to right",
            "crossing right to left", "same direction", "manoeuvring"])
        typ = _fp_rng.choice([
            "a light aircraft", "a PA28", "a Cessna 172", "a microlight",
            "a glider", "an unknown contact", "a helicopter"])
        lvl = _fp_rng.choice([
            f"indicating {max(500, alt + _fp_rng.choice([-700, -400, 400, 700]))} feet",
            "altitude unknown", "similar altitude", "altitude unverified"])
        return (f"Pass traffic information: traffic, {clock} o'clock, {dist} miles, "
                f"{rel}, {typ}, {lvl}.")

    if r < 0.66:   # a 'situation' — caution-type information
        return _fp_rng.choice([
            "Pass an information/caution: parachute dropping in progress within 5 "
            "miles of your track, up to flight level 100, caution.",
            "Pass an information/caution: intense glider activity reported along "
            "your route up to 4000 feet.",
            "Pass an information/caution: military low-level flying activity "
            "reported in your area at and below 2000 feet AGL.",
            "Pass an information/caution: a temporary segregated area is active "
            "about 8 miles south of your track, recommend you remain clear.",
            "Pass an information: a NATO exercise is increasing traffic on this "
            "frequency today; report any traffic sighted.",
        ])

    if r < 0.82 and prog.next_wp is not None and not prog.next_wp.is_airport:
        return f"Request the pilot report passing {prog.next_wp.ident}."

    # Information / reassurance / weather.
    dest = _flightplan.destination
    qnh = (_session.conditions.get(dest.ident) or {}).get("qnh") if _session else None
    qtxt = f" {dest.ident} QNH {qnh}." if qnh else ""
    return _fp_rng.choice([
        f"Advise no further reported traffic in the pilot's area.{qtxt}",
        f"Give a position/progress check: confirm still routing to {dest.ident}, "
        f"about {prog.dist_to_dest_nm:.0f} miles to run.",
        f"Advise the destination weather is being passed: {dest.ident} VFR, "
        f"no significant change.{qtxt}",
    ])


async def _fis_emit_proactive():
    """Generate one proactive FIS call from live geometry and play it."""
    ctx = _fis_context()
    if ctx is None or _user_speaking or _thinking_count > 0:
        return
    directive = _fis_directive(_driver.state)
    async with _tx_lock:
        if _user_speaking:
            return
        await _thinking_enter()
        try:
            text = await asyncio.to_thread(
                lambda: atc_engine.proactive(directive=directive, **ctx))
        except Exception as e:
            log.debug(f"FIS proactive generation failed: {e}")
            return
        finally:
            await _thinking_exit()
        if text:
            log.info(f"FIS proactive: {text}")
            await _emit_controller_line(text, model=config.MODEL_ROUTINE,
                                        station="fis")


async def _fis_emit_handoff(dest_ap: Airport):
    """FIS terminates the basic service and hands the flight to the arrival
    field's controller (Tower/Approach), with the real frequency."""
    ctx = _fis_context()
    if ctx is None:
        return
    # VFR arrival: hand to Tower (then Ground after landing, by voice). Fall back
    # to Approach only if the field publishes no Tower frequency.
    next_freq = None
    next_label = "Tower"
    twr = dest_ap.freq(54)
    app = dest_ap.freq(55)
    if twr:
        next_freq, next_label = twr.freq_mhz, "Tower"
    elif app:
        next_freq, next_label = app.freq_mhz, "Approach"
    city = dest_ap.name.split()[0]
    freq_txt = f" {next_freq:.3f}" if next_freq else ""
    directive = (f"Terminate the basic service and hand off: squawk 7000, "
                 f"contact {city} {next_label}{freq_txt} for arrival.")
    async with _tx_lock:
        await _thinking_enter()
        try:
            text = await asyncio.to_thread(
                lambda: atc_engine.proactive(directive=directive, **ctx))
        except Exception as e:
            log.debug(f"FIS handoff generation failed: {e}")
            text = (f"{_session.callsign}, basic service terminated, squawk 7000, "
                    f"contact {city} {next_label}{freq_txt}, good day.")
        finally:
            await _thinking_exit()
    await _emit_controller_line(text, model=config.MODEL_ROUTINE, station="fis")


async def _flightplan_progress(state: FlightState):
    """Advance the staged journey from the live position. One-way transitions
    keyed on `_fp_stage`, so each fires exactly once."""
    global _fp_stage
    if _flightplan is None or _session is None or not state.is_flight_loaded:
        return
    fp = _flightplan
    airborne = state.on_ground < 0.5
    prog = fp.progress(state.lat, state.lon)
    dest = fp.destination
    dest_ap = dest.airport

    if _fp_stage == "departure":
        if airborne and prog.dist_from_dep_nm >= _FP_DEPARTURE_CLEAR_NM:
            _fp_stage = "enroute"
            _session.enter_enroute_fis(context_airport=dest_ap)
            log.info(f"Flight plan → en route, {fp.fis.callsign}")
            await _fp_broadcast_service()

    elif _fp_stage == "enroute":
        if prog.dist_to_dest_nm <= _FP_ARRIVAL_RANGE_NM:
            _fp_stage = "arrival"
            if dest.controlled:
                try:
                    await _fis_emit_handoff(dest_ap)
                except Exception as e:
                    log.debug(f"FIS handoff skipped: {e}")
            _session.enter_arrival_field(dest_ap, bool(dest.controlled))
            log.info(f"Flight plan → arrival at {dest.ident} "
                     f"({'controlled' if dest.controlled else 'uncontrolled'})")
            await _fp_broadcast_service()

    elif _fp_stage == "arrival":
        if not airborne and prog.dist_to_dest_nm <= 6.0 and (state.gs_kts or 0) < 40:
            _fp_stage = "arrival_ground"
            if dest.controlled:
                _session.current_station = Station.GND
            _session.phase = Phase.GROUND_ARRIVAL
            log.info(f"Flight plan → landed/taxiing at {dest.ident}")
            await _fp_broadcast_service()


async def _fis_director_loop():
    """Keep the en-route FIS lively: while the flight plan has us en route and
    airborne, emit a proactive position-aware call every minute or so. Self-gates
    on every tick, so a stage change, keyed mic, or lost link stops it at once."""
    while True:
        if (_flightplan is None or _fp_stage != "enroute" or _session is None
                or _driver is None):
            await asyncio.sleep(3.0)
            continue
        try:
            st = _driver.state
            airborne = st.is_flight_loaded and st.on_ground < 0.5
        except Exception:
            airborne = False
        if not airborne:
            await asyncio.sleep(3.0)
            continue

        gap = _fp_rng.uniform(_FIS_GAP_MIN, _FIS_GAP_MAX)
        deadline = time.monotonic() + gap
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            if _flightplan is None or _fp_stage != "enroute":
                break
        if _flightplan is None or _fp_stage != "enroute":
            continue
        try:
            await _fis_emit_proactive()
        except Exception as e:
            log.debug(f"FIS director tick failed: {e}")


async def _state_poll_loop():
    global _startup_vfr_done
    last_airport_check = 0.0
    last_fp_check = 0.0

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

                    now = time.time()
                    if _flightplan is not None:
                        # A plan owns the journey: progress the staged services by
                        # position (every ~4 s) instead of nearest-airport adoption.
                        if now - last_fp_check > 4.0:
                            last_fp_check = now
                            await _flightplan_progress(state)
                    elif _airport_db and now - last_airport_check > 10.0:
                        # Re-check airport every 10 s (no plan active)
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


async def _ensure_navaid_db() -> Optional[NavaidDB]:
    """Parse earth_nav.dat + earth_fix.dat for route-point resolution. Lazy and
    non-fatal: a missing file just means intermediate fixes can't be resolved (a
    plan still stages departure→FIS→destination). Cached after first parse."""
    global _navaid_db, _navaid_db_loading
    if _navaid_db is not None:
        return _navaid_db
    if _navaid_db_loading:
        return None
    nav = config.first_existing(config.NAV_DATA_PATHS["nav"])
    fix = config.first_existing(config.NAV_DATA_PATHS["fix"])
    if nav is None and fix is None:
        log.info("No earth_nav.dat / earth_fix.dat found — route fixes won't resolve")
        return None
    _navaid_db_loading = True
    try:
        _navaid_db = await asyncio.to_thread(parse_nav_data, nav, fix)
        log.info(f"Navaid DB ready: {len(_navaid_db):,} points")
    except Exception as e:
        log.warning(f"Navaid DB load failed: {e}")
        _navaid_db = None
    finally:
        _navaid_db_loading = False
    return _navaid_db


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
            "tts_backend":        config.active_tts_backend(),
            "voice":              config.current_voice_name(),
            "voice_options":      config.voice_options(),
            "liveatc_enabled":    bool(config.LIVEATC_ENABLED),
            "has_liveatc_cookie": bool(config.LIVEATC_COOKIE),
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
    if cfg.get("voice"):
        config.set_voice(cfg["voice"])
        log.info(f"Controller voice → {config.current_voice_name()}")
    liveatc_changed = False
    if cfg.get("liveatc_cookie") is not None:
        config.set_env("LIVEATC_COOKIE", cfg["liveatc_cookie"])
        liveatc_changed = True
    if cfg.get("liveatc_enabled") is not None:
        on = bool(cfg["liveatc_enabled"])
        config.set_env("LIVEATC_ENABLED", "true" if on else "false")
        config.LIVEATC_ENABLED = on   # set_env stored the string; keep the bool
        liveatc_changed = True
    if liveatc_changed:
        global _liveatc, _liveatc_icao
        _liveatc = None
        _liveatc_icao = None
        if config.LIVEATC_ENABLED and _current_airport is not None:
            asyncio.create_task(_ensure_liveatc(_current_airport.icao))
        await _broadcast_liveatc_status()
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


async def _preview_voice():
    """Speak a short sample with the configured controller voice + radio FX, so
    the Settings dropdown can be auditioned. Audio only — no chat bubble."""
    if not _AUDIO_READY:
        await _broadcast("error",
                         message="Voice preview needs the audio modules (requirements-audio.txt).")
        return
    sample = ("Hannover Tower, good day, wind 270 degrees 8 knots, "
              "runway 27 left, cleared for takeoff.")
    try:
        spoken = _radio_text.to_spoken(sample)
        samples, sr = await asyncio.to_thread(_audio_tts.synthesize, spoken)
        samples = _audio_radio.apply_radio_fx(samples, sr)
        await _play_on_channel(samples, sr, event="atc_audio",
                               text=sample, model=None, timestamp=time.time())
    except Exception as e:
        log.warning(f"Voice preview failed: {e}")
        await _broadcast("error", message=f"Voice preview failed: {e}")


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
            _fis_director_loop(),
        )


if __name__ == "__main__":
    # Ctrl-C cancels the gathered loops; asyncio.run() then re-raises
    # KeyboardInterrupt. Swallow it so shutdown is one clean line, not a trace.
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped. Clear skies.")
