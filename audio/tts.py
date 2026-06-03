"""
TTS synthesis — clean audio only; radio DSP is applied separately in radio.py.

Backends (resolved via TTS_BACKEND in config, or 'auto'):
  openai  — OpenAI TTS API (tts-1-hd); best quality; needs OPENAI_API_KEY
  kokoro  — Kokoro-82M int8 ONNX; pip install kokoro-onnx; good local quality
  piper   — Piper ONNX voices; pip install piper-tts
  say     — macOS `say` built-in; always available, zero deps

'auto' prefers openai (if key set) → kokoro → piper → say.

Public API:
  synthesize(text, *, voice=None, backend=None) -> (np.ndarray[float32], int)
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Optional

import numpy as np

import config
from audio.radio import decode_wav

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".cache" / "xplane-vatc" / "piper"

# rhasspy/piper-voices HuggingFace path fragments per voice
_VOICE_PATHS: dict[str, str] = {
    "en_US-lessac-medium": "en/en_US/lessac/medium",
    "en_US-lessac-high":   "en/en_US/lessac/high",
    "en_US-ryan-medium":   "en/en_US/ryan/medium",
    "en_US-ryan-high":     "en/en_US/ryan/high",
    "en_US-danny-low":     "en/en_US/danny/low",
    "en_US-joe-medium":    "en/en_US/joe/medium",
    "en_GB-alan-low":      "en/en_GB/alan/low",
    "en_GB-alba-medium":   "en/en_GB/alba/medium",
}
_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

_piper_cache: dict[str, object] = {}   # voice_name → PiperVoice

_KOKORO_CACHE_DIR = Path.home() / ".cache" / "xplane-vatc" / "kokoro"
_KOKORO_MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
_KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
_kokoro_instance = None   # lazy singleton


# ─────────────────────────── OpenAI TTS backend ──────────────────────────────

def _openai_request(text: str, voice: str) -> bytes:
    """Make a single OpenAI TTS API call; return raw WAV bytes."""
    body = {
        "model": config.OPENAI_TTS_MODEL,
        "input": text,
        "voice": voice,
        "response_format": "wav",
    }
    # `instructions` is only honored by the gpt-4o-* TTS models; tts-1/tts-1-hd
    # reject it. Pin pronunciation so German/English aren't mixed per word.
    instructions = getattr(config, "OPENAI_TTS_INSTRUCTIONS", "")
    if instructions and config.OPENAI_TTS_MODEL.startswith("gpt-4o"):
        body["instructions"] = instructions
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=payload,
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def check_openai() -> bool:
    """
    Verify the OpenAI TTS key and model are reachable.
    Synthesises a single word; discards the audio.
    Returns True on success, False on any error (logs the reason).
    """
    try:
        log.info(
            f"OpenAI TTS configured — model={config.OPENAI_TTS_MODEL!r} "
            f"voice={config.TTS_VOICE!r}. Verifying key…"
        )
        _openai_request("check", config.TTS_VOICE)
        log.info("OpenAI TTS key verified OK.")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        log.warning(f"OpenAI TTS key check failed ({e.code}): {body[:200]}")
        return False
    except Exception as e:
        log.warning(f"OpenAI TTS key check failed: {e}")
        return False


def _backend_openai(text: str, voice: str) -> tuple[np.ndarray, int]:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return decode_wav(_openai_request(text, voice))


# ─────────────────────────── ElevenLabs TTS backend ──────────────────────────

_ELEVENLABS_SR = 24_000   # pcm_24000 → 16-bit mono PCM at 24 kHz


def _elevenlabs_request(text: str, voice_id: str) -> bytes:
    """Call ElevenLabs TTS; return raw 16-bit mono PCM at 24 kHz.

    pcm_24000 avoids an MP3 decode dependency — the bytes are already linear PCM.
    """
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
           f"?output_format=pcm_{_ELEVENLABS_SR}")
    payload = json.dumps({
        "text": text,
        "model_id": config.ELEVENLABS_TTS_MODEL,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _backend_elevenlabs(text: str, voice_id: str) -> tuple[np.ndarray, int]:
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    pcm = _elevenlabs_request(text, voice_id)
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return samples, _ELEVENLABS_SR


def check_elevenlabs() -> bool:
    """Verify the ElevenLabs key is usable (cheap GET, no character spend)."""
    if not config.ELEVENLABS_API_KEY:
        return False
    try:
        log.info(
            f"ElevenLabs TTS configured — model={config.ELEVENLABS_TTS_MODEL!r} "
            f"voice={config.ELEVENLABS_VOICE_ID!r}. Verifying key…"
        )
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        log.info("ElevenLabs key verified OK.")
        return True
    except Exception as e:
        log.warning(f"ElevenLabs key check failed: {e}")
        return False


# ─────────────────────────── Kokoro helpers ──────────────────────────────────

def _kokoro_available() -> bool:
    try:
        from kokoro_onnx import Kokoro   # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_kokoro_models() -> tuple[Path, Path]:
    """Download Kokoro model + voices from GitHub releases if not cached."""
    _KOKORO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model_path  = _KOKORO_CACHE_DIR / "kokoro-v1.0.int8.onnx"
    voices_path = _KOKORO_CACHE_DIR / "voices-v1.0.bin"
    for dest, url in [
        (model_path,  _KOKORO_MODEL_URL),
        (voices_path, _KOKORO_VOICES_URL),
    ]:
        if not dest.exists():
            log.info(f"Downloading Kokoro model: {url} → {dest.name}")
            urllib.request.urlretrieve(url, dest)
    return model_path, voices_path


def _backend_kokoro(text: str, voice: str) -> tuple[np.ndarray, int]:
    global _kokoro_instance
    from kokoro_onnx import Kokoro

    if _kokoro_instance is None:
        model_path, voices_path = _ensure_kokoro_models()
        _kokoro_instance = Kokoro(str(model_path), str(voices_path))

    samples, sr = _kokoro_instance.create(text, voice=voice, speed=1.0, lang="en-us")
    return samples.astype(np.float32), sr


# ─────────────────────────── Piper helpers ───────────────────────────────────

def _piper_available() -> bool:
    try:
        from piper.voice import PiperVoice   # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_piper_model(voice: str) -> Path:
    """Return path to .onnx file, downloading from HuggingFace if needed."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    onnx = _CACHE_DIR / f"{voice}.onnx"
    json = _CACHE_DIR / f"{voice}.onnx.json"
    if onnx.exists() and json.exists():
        return onnx
    frag = _VOICE_PATHS.get(voice)
    if not frag:
        raise ValueError(
            f"Unknown Piper voice {voice!r}. "
            f"Known voices: {list(_VOICE_PATHS)}"
        )
    base = f"{_HF_BASE}/{frag}/{voice}"
    for dest, url in [(onnx, f"{base}.onnx"), (json, f"{base}.onnx.json")]:
        log.info(f"Downloading Piper model: {url}")
        urllib.request.urlretrieve(url, dest)
    return onnx


def _backend_piper(text: str, voice: str) -> tuple[np.ndarray, int]:
    from piper.voice import PiperVoice

    if voice not in _piper_cache:
        model_path = _ensure_piper_model(voice)
        _piper_cache[voice] = PiperVoice.load(str(model_path))

    piper_voice = _piper_cache[voice]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        # piper-tts 1.3+ uses synthesize_wav(text, wav); older used synthesize(text, wav)
        if hasattr(piper_voice, 'synthesize_wav'):
            piper_voice.synthesize_wav(text, wf)
        else:
            piper_voice.synthesize(text, wf)
    return decode_wav(buf.getvalue())


# ─────────────────────────── say backend ─────────────────────────────────────

def _backend_say(text: str, sr: int = 22_050) -> tuple[np.ndarray, int]:
    """Synthesize via macOS `say`; returns float32 mono at `sr` Hz."""
    if not shutil.which('say'):
        raise RuntimeError("`say` not found — this backend is macOS-only")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        path = Path(f.name)
    try:
        subprocess.run(
            ['say',
             '--file-format=WAVE',
             f'--data-format=LEI16@{sr}',
             '-o', str(path),
             text],
            check=True, timeout=30, capture_output=True,
        )
        return decode_wav(path.read_bytes())
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────── public API ──────────────────────────────────────

def synthesize(
    text: str,
    *,
    voice: Optional[str] = None,
    backend: Optional[str] = None,
) -> tuple[np.ndarray, int]:
    """
    Synthesize text to clean float32 mono audio (no radio DSP applied).

    Parameters
    ----------
    text    : text to speak
    voice   : voice name/id; meaning depends on backend
              elevenlabs: voice id (defaults to config.ELEVENLABS_VOICE_ID)
              openai: 'onyx', 'echo', 'nova' …
              kokoro: 'am_adam' (default), 'am_michael', 'am_echo', 'af_sarah' …
              piper:  'en_US-lessac-high', 'en_US-ryan-high' …
              say:    ignored
    backend : 'elevenlabs', 'openai', 'kokoro', 'piper', 'say', or 'auto'
              auto order: elevenlabs → openai → kokoro → piper → say

    Returns
    -------
    (samples, sample_rate) — float32 mono
    """
    resolved_backend = backend or config.TTS_BACKEND
    resolved_voice   = voice   or config.TTS_VOICE

    if resolved_backend == 'auto':
        if config.ELEVENLABS_API_KEY:
            resolved_backend = 'elevenlabs'
        elif config.OPENAI_API_KEY:
            resolved_backend = 'openai'
        elif _kokoro_available():
            resolved_backend = 'kokoro'
        elif _piper_available():
            resolved_backend = 'piper'
        else:
            resolved_backend = 'say'

    if resolved_backend == 'elevenlabs':
        # ElevenLabs uses a voice *id*, not the generic TTS_VOICE name.
        return _backend_elevenlabs(text, voice or config.ELEVENLABS_VOICE_ID)
    if resolved_backend == 'openai':
        return _backend_openai(text, resolved_voice)
    if resolved_backend == 'kokoro':
        return _backend_kokoro(text, resolved_voice)
    if resolved_backend == 'piper':
        return _backend_piper(text, resolved_voice)
    if resolved_backend == 'say':
        return _backend_say(text)
    raise ValueError(f"Unknown TTS backend: {resolved_backend!r}")


def active_backend() -> str:
    """Resolve which backend 'auto' would pick (for startup logging/checks)."""
    if config.TTS_BACKEND != 'auto':
        return config.TTS_BACKEND
    if config.ELEVENLABS_API_KEY:
        return 'elevenlabs'
    if config.OPENAI_API_KEY:
        return 'openai'
    if _kokoro_available():
        return 'kokoro'
    if _piper_available():
        return 'piper'
    return 'say'


def check() -> bool:
    """Verify the active backend's credentials at startup. Returns True if OK
    (or if the backend needs no check). Logs the chosen backend."""
    backend = active_backend()
    log.info(f"TTS backend: {backend}")
    if backend == 'elevenlabs':
        return check_elevenlabs()
    if backend == 'openai':
        return check_openai()
    return True
