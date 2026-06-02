"""
STT transcription using faster-whisper.

Primary model: jacktol/whisper-large-v3-finetuned-for-ATC (HuggingFace).
Fine-tuned on ATC corpora; 6.5% WER vs 94% for stock Whisper.

The model is lazy-loaded on first call; subsequent calls reuse the in-process
instance. Load time is ~5–15 s on first use (model download + CT2 init).

Public API:
  transcribe(audio_bytes, *, callsign=None) -> str
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

# NATO phonetic alphabet + ATC vocabulary seed for Whisper's initial_prompt.
# This biases the decoder toward aviation terminology at zero training cost.
_ATC_PROMPT = (
    "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet "
    "Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango "
    "Uniform Victor Whiskey X-ray Yankee Zulu "
    "zero one two three four five six seven eight niner "
    "squawk cleared departure arrival approach tower ground radar "
    "runway heading altitude VFR IFR flight level contact frequency "
    "QNH QFE QTE affirm negative roger wilco"
)

_model = None   # faster_whisper.WhisperModel, loaded at server startup via preload()


def preload():
    """Download and initialise the Whisper model at startup (blocking).
    Call once from the server's run() so the first PTT press isn't delayed."""
    _load_model()


def _load_model():
    global _model
    if _model is not None:
        return _model

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed. "
            "Run: pip install faster-whisper"
        ) from None

    # huggingface_hub reads HF_TOKEN from the environment automatically.
    # Don't pass it to WhisperModel — faster-whisper 1.2.1 incorrectly forwards
    # it to the ctranslate2 constructor, which rejects it.
    if config.HF_TOKEN:
        os.environ.setdefault("HF_TOKEN", config.HF_TOKEN)
    log.info(f"Loading Whisper model {config.STT_MODEL!r} (first use — may download)…")
    _model = WhisperModel(config.STT_MODEL, device="cpu", compute_type="int8")
    log.info("Whisper model ready.")
    return _model


def transcribe(audio_bytes: bytes, *, callsign: Optional[str] = None) -> str:
    """
    Transcribe pilot radio audio to text.

    Parameters
    ----------
    audio_bytes : WAV bytes (any bit depth, sample rate, or channel count)
    callsign    : aircraft callsign (e.g. 'D-EIYD') injected into the prompt
                  to reduce mis-transcription of alphanumeric callsigns

    Returns
    -------
    Transcribed text, whitespace-stripped.
    """
    model = _load_model()

    # Build initial_prompt: callsign first so decoder sees it early
    prompt = (_ATC_PROMPT if not callsign
              else f"{callsign} {_ATC_PROMPT}")

    # faster-whisper accepts a file path; write to a temp file and clean up
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(audio_bytes)
        tmp = Path(f.name)
    try:
        try:
            segments, _info = model.transcribe(
                str(tmp),
                language="en",
                initial_prompt=prompt,
            )
            # Consume the lazy generator fully here, while the temp file still exists.
            # Do NOT return the generator unconsumed — the finally would delete the
            # file before the caller iterates it.
            return " ".join(seg.text for seg in segments).strip()
        except RuntimeError as e:
            if "model.bin" in str(e):
                raise RuntimeError(
                    f"Model {config.STT_MODEL!r} is not in CTranslate2 format. "
                    "Use a standard size name (e.g. 'large-v3') or convert a "
                    "HuggingFace model with ct2-transformers-converter first. "
                    f"Original error: {e}"
                ) from e
            raise
    finally:
        tmp.unlink(missing_ok=True)
