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


def set_xplane_path(path: str) -> None:
    """Point the airport lookup at a new X-Plane install (from the Settings view)."""
    global XPLANE_BASE, APT_DAT_PATHS
    XPLANE_BASE = Path(path)
    APT_DAT_PATHS = _apt_dat_paths(XPLANE_BASE)


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
# Voice id (not name). Default: "Daniel" — a steady British broadcaster, a good
# controller voice. Browse voices at https://elevenlabs.io/app/voice-library
ELEVENLABS_VOICE_ID  = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
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
