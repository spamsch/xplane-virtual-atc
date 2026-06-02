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

_STEAM_BASE = Path.home() / "Library/Application Support/Steam/steamapps/common/X-Plane 12"
XPLANE_BASE = Path(os.environ.get("XPLANE_PATH", str(_STEAM_BASE)))

APT_DAT_PATHS = [
    XPLANE_BASE / "Global Scenery" / "Global Airports" / "Earth nav data" / "apt.dat",
    XPLANE_BASE / "Custom Scenery" / "Global Airports" / "Earth nav data" / "apt.dat",
    XPLANE_BASE / "Resources" / "default scenery" / "default apt dat" / "Earth nav data" / "apt.dat",
]

AIRPORT_DETECTION_RADIUS_NM = 5.0
RUNWAY_DETECTION_MARGIN_M = 40.0

# LLM models
MODEL_ROUTINE = "claude-sonnet-4-6"    # routine ATC exchanges
MODEL_BOUNDARY = "claude-opus-4-8"     # first call, complex clearances, context setup

# ── Audio (optional feature; requires pip install faster-whisper piper-tts) ──
AUDIO_ENABLED = os.environ.get("AUDIO_ENABLED", "true").lower() == "true"

# STT — faster-whisper model name or HuggingFace CTranslate2 model ID.
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

# TTS — backend: 'auto' | 'piper' | 'say'
# 'auto' uses piper if installed, else macOS say
TTS_BACKEND = os.environ.get("TTS_BACKEND", "auto")   # auto | openai | kokoro | piper | say
# Voice name — meaning depends on backend:
#   openai:  onyx (deep male), echo, alloy, fable, nova, shimmer
#   kokoro:  am_adam, am_michael, am_echo (male); af_sarah, af_nicole (female)
#   piper:   en_US-lessac-high, en_US-ryan-high, en_US-joe-medium
TTS_VOICE          = os.environ.get("TTS_VOICE",          "onyx")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY",     "")
OPENAI_TTS_MODEL   = os.environ.get("OPENAI_TTS_MODEL",   "tts-1-hd")
# STT: 'whisper-1' is the stable choice; 'gpt-4o-transcribe' is newer/better
OPENAI_STT_MODEL   = os.environ.get("OPENAI_STT_MODEL",   "whisper-1")

# X-Plane PTT DataRef — set to your PTT joystick button DataRef, e.g.:
#   XPLANE_PTT_DATAREF=sim/joystick/joystick_button_array[32]
# Use X-Plane's DataRef browser (DataRefTool plugin) to find the right index.
# Leave empty to disable X-Plane PTT detection; use the UI button instead.
XPLANE_PTT_DATAREF = os.environ.get("XPLANE_PTT_DATAREF", "")

# HuggingFace token — loaded from .env; used for authenticated model downloads
HF_TOKEN = os.environ.get("HF_TOKEN", "")
