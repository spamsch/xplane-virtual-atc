import os
from pathlib import Path

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
TTS_BACKEND = os.environ.get("TTS_BACKEND", "auto")
TTS_VOICE   = os.environ.get("TTS_VOICE",   "en_US-lessac-medium")

# X-Plane PTT DataRef — set to your PTT joystick button DataRef, e.g.:
#   XPLANE_PTT_DATAREF=sim/joystick/joystick_button_array[32]
# Use X-Plane's DataRef browser (DataRefTool plugin) to find the right index.
# Leave empty to disable X-Plane PTT detection; use the UI button instead.
XPLANE_PTT_DATAREF = os.environ.get("XPLANE_PTT_DATAREF", "")
