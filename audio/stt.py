"""
STT transcription.

Backends:
  elevenlabs — ElevenLabs Scribe; needs ELEVENLABS_API_KEY; no local download
  openai     — OpenAI Whisper API; needs OPENAI_API_KEY; no local download
  local      — faster-whisper (large-v3); ~3 GB download. Opt-in only.

'auto' picks a cloud backend (ElevenLabs, then OpenAI) and never falls back to
the local model. Offline Whisper is opt-in: set STT_BACKEND=local explicitly.
That way an unconfigured server never downloads a multi-GB model.

Public API:
  preload()                              — call once at server startup
  check_openai() -> bool                — verify OpenAI key (logs result)
  transcribe(audio_bytes, *, callsign)  -> str
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

# NATO phonetic alphabet + ATC vocabulary — biases the Whisper decoder toward
# aviation terminology at zero cost (works for both local and OpenAI backend).
_ATC_PROMPT = (
    "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet "
    "Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango "
    "Uniform Victor Whiskey X-ray Yankee Zulu "
    "zero one two three four five six seven eight niner "
    "squawk cleared departure arrival approach tower ground radar "
    "runway heading altitude VFR IFR flight level contact frequency "
    "QNH QFE QTE affirm negative roger wilco"
)

_model = None   # faster_whisper.WhisperModel, only loaded when using local backend


# ─────────────────────────── backend selection ───────────────────────────────

def _active_backend() -> str:
    """Resolve the STT backend. 'auto' only ever picks a configured cloud
    provider — local faster-whisper is opt-in (STT_BACKEND=local), so an
    unconfigured server never downloads a model. Returns "none" when nothing
    is configured."""
    explicit = os.environ.get("STT_BACKEND", "auto")
    if explicit != "auto":
        return explicit
    if config.ELEVENLABS_API_KEY:
        return "elevenlabs"
    if config.OPENAI_API_KEY:
        return "openai"
    return "none"


# ─────────────────────────── OpenAI backend ──────────────────────────────────

def _multipart(audio_bytes: bytes, model: str, prompt: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body for the OpenAI transcription endpoint."""
    boundary = b"VATCBoundary7f3a"

    def part(name: str, value: bytes, filename: str = "", content_type: str = "") -> bytes:
        header = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            header += f'; filename="{filename}"'
        lines = [b"--" + boundary, header.encode()]
        if content_type:
            lines.append(f"Content-Type: {content_type}".encode())
        lines += [b"", value]
        return b"\r\n".join(lines)

    body = b"\r\n".join([
        part("file",     audio_bytes, filename="audio.wav", content_type="audio/wav"),
        part("model",    model.encode()),
        part("language", b"en"),
        part("prompt",   prompt.encode()),
        b"--" + boundary + b"--",
    ])
    content_type = f"multipart/form-data; boundary={boundary.decode()}"
    return body, content_type


def _transcribe_openai(audio_bytes: bytes, callsign: Optional[str]) -> str:
    prompt = f"{callsign} {_ATC_PROMPT}" if callsign else _ATC_PROMPT
    body, content_type = _multipart(audio_bytes, config.OPENAI_STT_MODEL, prompt)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": content_type,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read()).get("text", "").strip()


def check_openai() -> bool:
    """Verify the OpenAI STT key and model at startup. Returns True on success."""
    try:
        log.info(
            f"OpenAI STT configured — model={config.OPENAI_STT_MODEL!r}. Verifying key…"
        )
        # 0.2 s of silence at 16 kHz — above the API's 0.1 s minimum
        from audio.radio import encode_wav
        import numpy as np
        silence = encode_wav(np.zeros(3200, dtype=np.float32), 16_000)
        _transcribe_openai(silence, callsign=None)
        log.info("OpenAI STT key verified OK.")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        log.warning(f"OpenAI STT key check failed ({e.code}): {body[:200]}")
        return False
    except Exception as e:
        log.warning(f"OpenAI STT key check failed: {e}")
        return False


# ─────────────────────────── ElevenLabs (Scribe) backend ─────────────────────

def _transcribe_elevenlabs(audio_bytes: bytes, callsign: Optional[str]) -> str:
    """Transcribe via ElevenLabs Scribe. callsign is unused — Scribe has no
    prompt-biasing parameter, but its accuracy on ATC audio is high."""
    boundary = b"VATCBoundaryEL7f3a"

    def part(name: str, value: bytes, filename: str = "", content_type: str = "") -> bytes:
        header = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            header += f'; filename="{filename}"'
        lines = [b"--" + boundary, header.encode()]
        if content_type:
            lines.append(f"Content-Type: {content_type}".encode())
        lines += [b"", value]
        return b"\r\n".join(lines)

    body = b"\r\n".join([
        part("file", audio_bytes, filename="audio.wav", content_type="audio/wav"),
        part("model_id", config.ELEVENLABS_STT_MODEL.encode()),
        part("language_code", b"eng"),       # force English — ATC phraseology
        part("tag_audio_events", b"false"),  # no "(static)" / "(beep)" junk
        part("diarize", b"false"),
        b"--" + boundary + b"--",
    ])
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text",
        data=body,
        headers={
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read()).get("text", "").strip()


def check_elevenlabs() -> bool:
    """Verify the ElevenLabs key at startup (cheap GET, no transcription spend)."""
    if not config.ELEVENLABS_API_KEY:
        return False
    try:
        log.info(
            f"ElevenLabs STT configured — model={config.ELEVENLABS_STT_MODEL!r}. "
            f"Verifying key…"
        )
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        log.info("ElevenLabs STT key verified OK.")
        return True
    except Exception as e:
        log.warning(f"ElevenLabs STT key check failed: {e}")
        return False


# ─────────────────────────── local backend ───────────────────────────────────

def _load_local_model():
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed. Run: pip install faster-whisper"
        ) from None
    if config.HF_TOKEN:
        os.environ.setdefault("HF_TOKEN", config.HF_TOKEN)
    log.info(f"Loading Whisper model {config.STT_MODEL!r} (first use — may download)…")
    _model = WhisperModel(config.STT_MODEL, device="cpu", compute_type="int8")
    log.info("Whisper model ready.")
    return _model


def _transcribe_local(audio_bytes: bytes, callsign: Optional[str]) -> str:
    model = _load_local_model()
    prompt = f"{callsign} {_ATC_PROMPT}" if callsign else _ATC_PROMPT
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(audio_bytes)
        tmp = Path(f.name)
    try:
        try:
            segments, _info = model.transcribe(str(tmp), language="en",
                                               initial_prompt=prompt)
            # Consume the lazy generator while the temp file still exists.
            return " ".join(seg.text for seg in segments).strip()
        except RuntimeError as e:
            if "model.bin" in str(e):
                raise RuntimeError(
                    f"Model {config.STT_MODEL!r} is not in CTranslate2 format. "
                    "Use a standard size name (e.g. 'large-v3') or convert with "
                    "ct2-transformers-converter. Original error: {e}"
                ) from e
            raise
    finally:
        tmp.unlink(missing_ok=True)


# ─────────────────────────── public API ──────────────────────────────────────

def preload():
    """Call once at server startup. Verifies the cloud key, or — only when the
    user has explicitly set STT_BACKEND=local — loads the offline model. Never
    downloads a local model just because no cloud key is set yet (the default is
    ElevenLabs; the key arrives via the Settings view). Never raises."""
    backend = _active_backend()
    if backend == "elevenlabs":
        log.info("STT backend: ElevenLabs Scribe (no local model download needed)")
        check_elevenlabs()
    elif backend == "openai":
        log.info("STT backend: OpenAI (no local model download needed)")
        check_openai()
    elif backend == "local":
        log.info("STT backend: local faster-whisper (STT_BACKEND=local)")
        try:
            _load_local_model()
        except Exception as e:
            log.warning(f"Local STT model unavailable: {e}")
    else:  # "none"
        log.info(
            "STT not configured — no ElevenLabs or OpenAI key set. Add a key in "
            "the app's Settings to enable voice input. (Offline STT is opt-in: "
            "install faster-whisper and set STT_BACKEND=local.)"
        )


def transcribe(audio_bytes: bytes, *, callsign: Optional[str] = None) -> str:
    """
    Transcribe pilot radio audio to text.

    Parameters
    ----------
    audio_bytes : WAV bytes (16-bit PCM)
    callsign    : aircraft callsign injected into the prompt (e.g. 'D-EIYD')

    Returns
    -------
    Transcribed text, whitespace-stripped.
    """
    backend = _active_backend()
    if backend == "elevenlabs":
        return _transcribe_elevenlabs(audio_bytes, callsign).strip()
    if backend == "openai":
        return _transcribe_openai(audio_bytes, callsign).strip()
    if backend == "local":
        return _transcribe_local(audio_bytes, callsign)
    raise RuntimeError(
        "No speech-to-text backend configured. Add an ElevenLabs key in Settings, "
        "or set STT_BACKEND=local for offline Whisper."
    )
