"""
Aviation VHF radio DSP chain.

Chain applied by apply_radio_fx:
  1. Butterworth bandpass 300–3400 Hz  — the critical step; phone/radio sound
  2. tanh soft-clip                    — AM transmitter drive/squash character
  3. Light in-band carrier hiss        — open-squelch feel at -38 dBFS

Correct aviation radio bandpass is 300–3400 Hz.  TTS-Radio's pydub defaults
(HPF 4000, LPF 3000) are inverted and produce a band-reject — don't copy them.
"""

from __future__ import annotations

import io
import wave
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfilt

# DSP constants — all named so re-tuning is a one-line edit
BANDPASS_LOW_HZ  = 300
BANDPASS_HIGH_HZ = 3400
BANDPASS_ORDER   = 4
DRIVE_GAIN       = 3.0    # tanh gain; useful range 2–5
NOISE_DB         = -38.0  # carrier hiss level relative to 0 dBFS


@lru_cache(maxsize=8)
def _bandpass_sos(sr: int) -> np.ndarray:
    """Butterworth bandpass SOS coefficients, cached per sample rate."""
    nyq  = sr / 2.0
    low  = BANDPASS_LOW_HZ  / nyq
    high = BANDPASS_HIGH_HZ / nyq
    return butter(BANDPASS_ORDER, [low, high], btype='band', output='sos')


def apply_radio_fx(samples: np.ndarray, sr: int) -> np.ndarray:
    """
    Apply VHF aviation radio character to float32 mono audio.

    Parameters
    ----------
    samples : float32 1-D mono array
    sr      : sample rate in Hz

    Returns
    -------
    float32 mono array, same length, hard-clipped to ±1.0
    """
    if samples.ndim != 1:
        raise ValueError(f"Expected 1-D mono array, got shape {samples.shape}")

    sos = _bandpass_sos(sr)

    # 1. Bandpass — run in float64 for numerical stability, convert back
    out = sosfilt(sos, samples.astype(np.float64)).astype(np.float32)

    # 2. Soft-clip (tanh AM drive)
    out = np.tanh(DRIVE_GAIN * out).astype(np.float32)

    # 3. In-band carrier hiss
    noise_amp = 10.0 ** (NOISE_DB / 20.0)
    hiss = np.random.default_rng().standard_normal(len(out)).astype(np.float64) * noise_amp
    hiss = sosfilt(sos, hiss).astype(np.float32)
    out  = out + hiss

    # Hard clip — tanh already bounds the voice to (-1,1); hiss can push slightly
    # over on rare peaks. Clip rather than normalize to avoid level pumping.
    return np.clip(out, -1.0, 1.0)


def encode_wav(samples: np.ndarray, sr: int) -> bytes:
    """Encode float32 mono array to 16-bit PCM WAV bytes.

    Scale convention: ×32768 then clamp to [-32768, 32767].
    Matches decode_wav (÷32768). +1.0 encodes to 32767, decodes to 0.99997 —
    the 3 e-5 asymmetry is inherent to int16 PCM; -1.0 roundtrips exactly.
    """
    scaled = np.clip(samples, -1.0, 1.0) * 32768.0
    int16  = np.clip(scaled, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes to float32 mono array + sample rate.

    Multi-channel audio is mixed down to mono by averaging channels.
    """
    buf = io.BytesIO(data)
    with wave.open(buf, 'rb') as wf:
        sr         = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        raw        = wf.readframes(wf.getnframes())

    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    if sampwidth not in dtype_map:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

    scale = float(2 ** (sampwidth * 8 - 1))  # 32768 for int16
    pcm   = np.frombuffer(raw, dtype=dtype_map[sampwidth]).astype(np.float32) / scale

    if n_channels > 1:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1)

    return pcm, sr
