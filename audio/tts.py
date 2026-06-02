"""
TTS synthesis — clean audio only; radio DSP is applied separately in radio.py.

Backends (resolved via TTS_BACKEND in config, or 'auto'):
  piper   — local ONNX inference via piper-tts; pip install piper-tts
  say     — macOS `say` built-in; always available on macOS, zero deps

'auto' tries piper first, falls back to say.

Public API:
  synthesize(text, *, voice=None, backend=None) -> (np.ndarray[float32], int)
"""

from __future__ import annotations

import io
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
    "en_GB-alba-medium":   "en/en_GB/alba/medium",
    "en_US-ryan-medium":   "en/en_US/ryan/medium",
    "en_US-ryan-high":     "en/en_US/ryan/high",
}
_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

_piper_cache: dict[str, object] = {}   # voice_name → PiperVoice


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
    voice   : Piper voice name (ignored by the `say` backend)
    backend : 'piper', 'say', or 'auto' (default comes from config.TTS_BACKEND)

    Returns
    -------
    (samples, sample_rate) — float32 mono
    """
    resolved_backend = backend or config.TTS_BACKEND
    resolved_voice   = voice   or config.TTS_VOICE

    if resolved_backend == 'auto':
        resolved_backend = 'piper' if _piper_available() else 'say'

    if resolved_backend == 'piper':
        return _backend_piper(text, resolved_voice)
    if resolved_backend == 'say':
        return _backend_say(text)
    raise ValueError(f"Unknown TTS backend: {resolved_backend!r}")
