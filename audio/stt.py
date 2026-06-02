"""
STT transcription.

Backends (auto-selected at startup):
  openai — OpenAI Whisper API; needs OPENAI_API_KEY; no local model download
  local  — faster-whisper (large-v3); ~3 GB download on first use

'auto' uses OpenAI if OPENAI_API_KEY is set, otherwise local.
STT_BACKEND env var overrides.

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
    explicit = os.environ.get("STT_BACKEND", "auto")
    if explicit != "auto":
        return explicit
    return "openai" if config.OPENAI_API_KEY else "local"


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
        # Minimal WAV: 44-byte header + 2 bytes of silence = valid but tiny
        silence = (
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80\x3e\x00\x00\x00\x7d\x00\x00"
            b"\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00"
        )
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
    """Call once at server startup. Loads local model or verifies OpenAI key."""
    backend = _active_backend()
    if backend == "openai":
        log.info("STT backend: OpenAI (no local model download needed)")
        check_openai()
    else:
        log.info("STT backend: local faster-whisper")
        _load_local_model()


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
    if _active_backend() == "openai":
        return _transcribe_openai(audio_bytes, callsign).strip()
    return _transcribe_local(audio_bytes, callsign)
