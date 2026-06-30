import os
from pathlib import Path

# Load .env from the project root (if present) before reading any env vars.
# Simple parser: KEY=VALUE lines, ignores comments and blanks. No extra deps.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

XPLANE_IP = os.environ.get("XPLANE_IP", "127.0.0.1")
XPLANE_UDP_PORT = int(os.environ.get("XPLANE_PORT", "49000"))
LOCAL_RECV_PORT = int(os.environ.get("LOCAL_PORT", "49001"))
# REST API (X-Plane 12.1.0+) — enable in Network & Sound → "Enable local network access"
XPLANE_REST_PORT = int(os.environ.get("XPLANE_REST_PORT", "8086"))

# When true, the backend applies a "VFR day" once at startup as soon as X-Plane
# is connected and a flight is loaded: real weather frozen (wind/pressure/temp
# stay real), a few scattered clouds, visibility > 5 sm, and local noon. Same as
# clicking the VFR Day button, done automatically.
VFR_WEATHER_ON_START = os.environ.get("VFR_WEATHER_ON_START", "false").lower() in ("1", "true", "yes")

_STEAM_BASE = Path.home() / "Library/Application Support/Steam/steamapps/common/X-Plane 12"
XPLANE_BASE = Path(os.environ.get("XPLANE_PATH", str(_STEAM_BASE)))


def _apt_dat_paths(base: Path) -> list:
    """Candidate apt.dat locations under an X-Plane install."""
    return [
        base / "Global Scenery" / "Global Airports" / "Earth nav data" / "apt.dat",
        base / "Custom Scenery" / "Global Airports" / "Earth nav data" / "apt.dat",
        base / "Resources" / "default scenery" / "default apt dat" / "Earth nav data" / "apt.dat",
    ]


APT_DAT_PATHS = _apt_dat_paths(XPLANE_BASE)


def _nav_data_paths(base: Path) -> dict:
    """Candidate earth_nav.dat / earth_fix.dat locations under an X-Plane install.
    'Custom Data' (a user nav-data update, e.g. Navigraph) wins over the shipped
    'Resources/default data' when present."""
    return {
        "nav": [
            base / "Custom Data" / "earth_nav.dat",
            base / "Resources" / "default data" / "earth_nav.dat",
        ],
        "fix": [
            base / "Custom Data" / "earth_fix.dat",
            base / "Resources" / "default data" / "earth_fix.dat",
        ],
    }


NAV_DATA_PATHS = _nav_data_paths(XPLANE_BASE)


def first_existing(paths: list):
    """First path in the list that exists, or None."""
    for p in paths:
        if p.exists():
            return p
    return None


def set_xplane_path(path: str) -> None:
    """Point the airport + nav lookups at a new X-Plane install (from Settings)."""
    global XPLANE_BASE, APT_DAT_PATHS, NAV_DATA_PATHS
    XPLANE_BASE = Path(path)
    APT_DAT_PATHS = _apt_dat_paths(XPLANE_BASE)
    NAV_DATA_PATHS = _nav_data_paths(XPLANE_BASE)


def set_env(key: str, value: str) -> None:
    """Persist KEY=value to .env and update the live process + this module.

    Consumers read config.<KEY> at call time, so updating the module global takes
    effect immediately. Other .env lines are preserved; the file is chmod 600.
    """
    value = value.strip()
    globals()[key] = value          # update e.g. config.ELEVENLABS_API_KEY
    os.environ[key] = value
    lines, found = [], False
    if _env_file.exists():
        for line in _env_file.read_text().splitlines():
            stripped = line.strip()
            if (stripped and not stripped.startswith("#")
                    and stripped.split("=", 1)[0].strip() == key):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    _env_file.write_text("\n".join(lines) + "\n")
    try:
        _env_file.chmod(0o600)
    except OSError:
        pass

AIRPORT_DETECTION_RADIUS_NM = 5.0
RUNWAY_DETECTION_MARGIN_M = 40.0

# LLM models
MODEL_ROUTINE = "claude-sonnet-4-6"    # routine ATC exchanges
MODEL_BOUNDARY = "claude-opus-4-8"     # first call, complex clearances, context setup

# ── Audio (optional feature; requires pip install faster-whisper piper-tts) ──
AUDIO_ENABLED = os.environ.get("AUDIO_ENABLED", "true").lower() == "true"

# ── Ambient radio traffic — the "party line" of other aircraft on your
# frequency, injected into the audio when X-Plane is connected. ────────────────
# Density of the chatter. One knob: off | light | medium | heavy. Default medium.
# Matched to the frequency type you're tuned to (no Tower traffic on Ground) and
# the current airport's size; suppressed while you're transmitting.
AMBIENT_TRAFFIC_LEVEL = os.environ.get("AMBIENT_TRAFFIC_LEVEL", "medium").lower()
# Which flight rules other traffic flies. VFR-only by default; the engine is
# ready to mix in IFR airliners at a field — set "VFR,IFR". En-route is always
# VFR regardless of this. Comma-separated; values: VFR, IFR.
AMBIENT_TRAFFIC_RULES = [
    r.strip().upper() for r in os.environ.get("AMBIENT_TRAFFIC_RULES", "VFR").split(",")
    if r.strip()
] or ["VFR"]
# The *other* pilots on the frequency are each given a voice drawn from a pool,
# seeded by their callsign so one aircraft sounds consistent. By default the pool
# is chosen to match the active TTS backend (see the pools below): with
# ElevenLabs you get 10 distinct premade voices. The controller's own ambient
# lines always use your configured controller voice — it's the same controller.
#
# Override the pool with AMBIENT_PILOT_VOICES (comma-separated): ElevenLabs voice
# ids or names from ELEVENLABS_VOICE_LIBRARY, or OpenAI/say voice names. Empty =
# use the backend default pool below.
AMBIENT_PILOT_VOICES = [
    resolve_elevenlabs_voice(v.strip())
    for v in os.environ.get("AMBIENT_PILOT_VOICES", "").split(",") if v.strip()
]
# Default 10-voice ElevenLabs pool for other traffic — a varied mix of accents,
# ages and genders, deliberately excluding the controller's Daniel so the pilots
# never sound like the controller. Used when AMBIENT_PILOT_VOICES is empty and
# the active TTS backend is ElevenLabs.
ELEVENLABS_PILOT_POOL = [
    "nPczCjzI2devNBz1zQrb",  # Brian   — American · deep
    "IKne3meq5aSn9XLyUdCD",  # Charlie — Australian · conversational
    "TX3LPaxmHKxFdv7VOQHJ",  # Liam    — American · articulate
    "JBFqnCBsd6RMkjVDRZzb",  # George  — British · warm
    "ErXwobaYiN019PkySvjV",  # Antoni  — American · well-rounded
    "TxGEqnHWrfWFTfGW9XjX",  # Josh    — American · deep · young
    "yoZ06aMxZJJ28mfd3POQ",  # Sam     — American · raspy
    "VR6AewLTigWG4xSOukaG",  # Arnold  — American · crisp
    "Xb7hH8MSUJpSbSDYk0k2",  # Alice   — British · female
    "XrExE9yKIg1WjnnlVkGX",  # Matilda — American · female
]
# Default pool when the active TTS backend is OpenAI (its six built-in voices).
OPENAI_PILOT_POOL = ["onyx", "echo", "alloy", "fable", "nova", "shimmer"]
# Optional directory of extra interaction *.json files, merged on top of the
# built-in library (traffic/interactions/). This is the "customise it" hook.
AMBIENT_LIBRARY_DIR = os.environ.get("AMBIENT_LIBRARY_DIR", "")

# ── Historical LiveATC traffic (opt-in, off by default) ──────────────────────
# Inject real recorded ATC chatter for the current airport as background texture
# under the synthetic party line. Best-effort: no public API, gated archives,
# thin coverage outside the US — degrades to nothing when there's no feed.
# Automated downloading is against LiveATC's ToS; this is for personal local use.
LIVEATC_ENABLED = os.environ.get("LIVEATC_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Your logged-in liveatc.net session cookie ("name=value; name2=value2"). Without
# it, archive downloads 403 and the layer stays empty.
LIVEATC_COOKIE = os.environ.get("LIVEATC_COOKIE", "")
# How many recent 30-minute archive blocks to try before giving up.
LIVEATC_LOOKBACK_BLOCKS = int(os.environ.get("LIVEATC_LOOKBACK_BLOCKS", "6"))
# When a background-atmosphere beat fires, the chance it plays a real LiveATC clip
# instead of synthetic squelch/static (0 = never, 1 = always). Clamped to [0,1].
LIVEATC_MIX = max(0.0, min(1.0, float(os.environ.get("LIVEATC_MIX", "0.6"))))

# ── Airspace awareness (OpenAIP) ─────────────────────────────────────────────
# X-Plane doesn't expose controlled airspace, so we load it from OpenAIP's free
# per-country export (CC BY-NC 4.0, https://www.openaip.net/). When enabled, the
# controller knows the real control zone you're in (e.g. "Hannover CTR, Class D,
# SFC-2500 ft") and the handover logic uses real CTR boundaries instead of a
# distance guess. The relevant country file (~3 MB) is downloaded once and
# cached; with no network it falls back to the distance proxy. Set to false to
# stay fully offline / skip the download.
AIRSPACE_ENABLED = os.environ.get("AIRSPACE_ENABLED", "true").lower() in ("1", "true", "yes")

# STT — faster-whisper model name or HuggingFace CTranslate2 model ID.
# Only used when offline STT is opted into (STT_BACKEND=local); 'auto' never
# selects local, so this model is not downloaded unless you ask for it.
# Standard sizes (tiny/base/small/medium/large-v3 etc.) are downloaded
# automatically from Systran's HuggingFace org in CTranslate2 format.
#
# To use the ATC fine-tune (6.5% WER vs 94% stock), first convert it:
#   pip install transformers torch
#   ct2-transformers-converter \
#     --model jacktol/whisper-large-v3-finetuned-for-ATC \
#     --output_dir ~/.cache/xplane-vatc/whisper-atc-ct2 \
#     --quantization int8
# Then set: STT_MODEL=~/.cache/xplane-vatc/whisper-atc-ct2
STT_MODEL = os.environ.get("STT_MODEL", "large-v3")

# TTS backend. 'auto' picks a cloud provider only: ElevenLabs → OpenAI → macOS
# `say`. Local backends (kokoro, piper) are opt-in — set TTS_BACKEND explicitly
# so an unconfigured server never downloads a voice model.
TTS_BACKEND = os.environ.get("TTS_BACKEND", "auto")   # auto | elevenlabs | openai | kokoro | piper | say
# Voice name — meaning depends on backend:
#   openai:  onyx (deep male), echo, alloy, fable, nova, shimmer
#   kokoro:  am_adam, am_michael, am_echo (male); af_sarah, af_nicole (female)
#   piper:   en_US-lessac-high, en_US-ryan-high, en_US-joe-medium
TTS_VOICE          = os.environ.get("TTS_VOICE",          "onyx")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY",     "")
# gpt-4o-mini-tts accepts an `instructions` field to steer accent/delivery, which
# the older tts-1/tts-1-hd models do not. Use it so German place names and English
# phraseology aren't pronounced inconsistently. tts-1-hd still works but ignores
# OPENAI_TTS_INSTRUCTIONS.
OPENAI_TTS_MODEL   = os.environ.get("OPENAI_TTS_MODEL",   "gpt-4o-mini-tts")
# Pronunciation/delivery steering, only applied for gpt-4o-* TTS models. Keeps a
# single, consistent accent across a transmission instead of switching per word.
_DEFAULT_TTS_INSTRUCTIONS = (
    "You are a German air traffic controller speaking standard ICAO aviation "
    "radio English. Use one consistent, light German accent across the entire "
    "message — never switch between German and English pronunciation within a "
    "sentence. Read callsigns and any standalone letters using the NATO phonetic "
    "alphabet (A=Alpha, B=Bravo, … Z=Zulu); for example 'D-EIYD' is spoken "
    "'Delta-Echo-India-Yankee-Delta'. Read all numbers digit by digit in clear "
    "aviation English. German airport, place, and waypoint names keep their "
    "natural German pronunciation. Calm, measured, professional radio tone; no "
    "emotion or emphasis."
)
OPENAI_TTS_INSTRUCTIONS = os.environ.get("OPENAI_TTS_INSTRUCTIONS", _DEFAULT_TTS_INSTRUCTIONS)
# STT: 'whisper-1' is the stable choice; 'gpt-4o-transcribe' is newer/better
OPENAI_STT_MODEL   = os.environ.get("OPENAI_STT_MODEL",   "whisper-1")

# ── ElevenLabs — STT (Scribe) + TTS. Low-latency, high-quality voices.
# When ELEVENLABS_API_KEY is set, 'auto' prefers ElevenLabs for both STT and TTS.
ELEVENLABS_API_KEY   = os.environ.get("ELEVENLABS_API_KEY", "")
# TTS: eleven_flash_v2_5 is the fast multilingual model (~75 ms latency) — the
# right default for live ATC. eleven_multilingual_v2 is higher quality but slower;
# eleven_v3 is the most expressive but slowest.
ELEVENLABS_TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
# Controller voice. ELEVENLABS_VOICE_ID accepts EITHER a friendly name from the
# curated list below (case-insensitive) OR any raw voice id from your ElevenLabs
# library. The curated voices are ElevenLabs premade voices that read well as a
# controller — steady, clear, broadcaster/news delivery. Browse more at
# https://elevenlabs.io/app/voice-library
ELEVENLABS_VOICE_LIBRARY = {
    "daniel":  "onwK4e9ZLuTAKqWW03F9",  # British · deep · news presenter  (default)
    "brian":   "nPczCjzI2devNBz1zQrb",  # American · deep · calm narration
    "george":  "JBFqnCBsd6RMkjVDRZzb",  # British · warm
    "bill":    "pqHfZKP75CvOlQylNhV4",  # American · older · documentary
    "adam":    "pNInz6obpgDQGcFmaJgB",  # American · deep · neutral
    "liam":    "TX3LPaxmHKxFdv7VOQHJ",  # American · articulate
    "charlie": "IKne3meq5aSn9XLyUdCD",  # Australian · conversational
    "alice":   "Xb7hH8MSUJpSbSDYk0k2",  # British · female · confident news
    "matilda": "XrExE9yKIg1WjnnlVkGX",  # American · female · warm
    "lily":    "pFZP5JQG7iQjIQuC4Bku",  # British · female · warm
}


def resolve_elevenlabs_voice(value: str) -> str:
    """Map a friendly name from ELEVENLABS_VOICE_LIBRARY (case-insensitive) to its
    voice id; pass any other value through as a raw id. Empty → Daniel."""
    v = (value or "").strip()
    if not v:
        return ELEVENLABS_VOICE_LIBRARY["daniel"]
    return ELEVENLABS_VOICE_LIBRARY.get(v.lower(), v)


# Short descriptors for the curated controller voices, for the Settings dropdown.
ELEVENLABS_VOICE_DESC = {
    "daniel":  "British · deep · news",
    "brian":   "American · deep · calm",
    "george":  "British · warm",
    "bill":    "American · older · documentary",
    "adam":    "American · deep · neutral",
    "liam":    "American · articulate",
    "charlie": "Australian · conversational",
    "alice":   "British · female · news",
    "matilda": "American · female · warm",
    "lily":    "British · female · warm",
}

# The six OpenAI TTS voices (see TTS_VOICE above).
OPENAI_TTS_VOICES = ["onyx", "echo", "alloy", "fable", "nova", "shimmer"]

ELEVENLABS_VOICE_ID = resolve_elevenlabs_voice(os.environ.get("ELEVENLABS_VOICE_ID", "daniel"))


def active_tts_backend() -> str:
    """Which TTS backend is live, without importing the audio stack (mirrors
    audio.tts.active_backend so config_status can report it even when the
    optional audio modules aren't installed)."""
    if TTS_BACKEND != "auto":
        return TTS_BACKEND
    if ELEVENLABS_API_KEY:
        return "elevenlabs"
    if OPENAI_API_KEY:
        return "openai"
    return "say"


def current_voice_name() -> str:
    """The configured controller voice as a friendly name the UI can preselect:
    a curated ElevenLabs name (or raw id if custom), or the OpenAI voice. Empty
    for backends with no selectable voice (say/kokoro/piper)."""
    backend = active_tts_backend()
    if backend == "elevenlabs":
        for name, vid in ELEVENLABS_VOICE_LIBRARY.items():
            if vid == ELEVENLABS_VOICE_ID:
                return name
        return ELEVENLABS_VOICE_ID
    if backend == "openai":
        return TTS_VOICE
    return ""


def voice_options() -> list:
    """Selectable controller voices for the active backend, as
    [{value, label}] — empty when the backend's voice isn't user-selectable."""
    backend = active_tts_backend()
    if backend == "elevenlabs":
        return [{"value": n, "label": f"{n.capitalize()} — {ELEVENLABS_VOICE_DESC.get(n, '')}".strip(" —")}
                for n in ELEVENLABS_VOICE_LIBRARY]
    if backend == "openai":
        return [{"value": v, "label": v} for v in OPENAI_TTS_VOICES]
    return []


def set_voice(name: str) -> None:
    """Persist the controller voice for the active backend. ElevenLabs stores the
    resolved voice *id* (so synthesize works immediately and across restarts);
    OpenAI stores the voice name."""
    name = (name or "").strip()
    if not name:
        return
    backend = active_tts_backend()
    if backend == "elevenlabs":
        set_env("ELEVENLABS_VOICE_ID", resolve_elevenlabs_voice(name))
    elif backend == "openai":
        set_env("TTS_VOICE", name)
# Speech rate. ElevenLabs accepts 0.7–1.2 (1.0 = normal). 1.2 is the max — the
# fast, clipped cadence of a busy controller. Clamped to the valid range.
ELEVENLABS_TTS_SPEED = max(0.7, min(1.2, float(os.environ.get("ELEVENLABS_TTS_SPEED", "1.2"))))
# STT: scribe_v1 (stable) or scribe_v2 (newer). Batch transcription endpoint.
ELEVENLABS_STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1")
# Note: ElevenLabs is used for STT + TTS only. ATC *text* is still generated by
# the claude CLI — ElevenLabs has no single-turn text-completion API, and its
# agent endpoints are realtime-voice / eval-oriented (slow, uncontrollable for
# this state-machine design). See the README for the full rationale.

# X-Plane PTT source — watched over the WebSocket API for instant press/release
# edges (no 2 Hz polling lag). Auto-detects whether the name is a dataref or a
# command. Use a DATAREF that holds 1 while the key is pressed:
#   xpilot/ptt                              # RECOMMENDED — xPilot's PTT dataref.
#                                           # Bind your key to the "xPilot: Radio
#                                           # Push-to-Talk" command; the dataref
#                                           # then holds 1 for the whole press.
#   sim/joystick/joystick_button_array[32]  # a raw joystick button + index
#
# Commands (e.g. sim/operation/contact_atc_ptt) also resolve, but most ATC
# command bindings fire as a one-shot PULSE (press+release in the same instant),
# so hold-to-talk recording starts and stops immediately. Use a dataref instead.
#
# Leave empty to disable; use the UI button or Spacebar instead.
XPLANE_PTT_DATAREF = os.environ.get("XPLANE_PTT_DATAREF", "")

# HuggingFace token — loaded from .env; used for authenticated model downloads
HF_TOKEN = os.environ.get("HF_TOKEN", "")
