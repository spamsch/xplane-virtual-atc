"""
Aviation VHF radio DSP chain.

Chain applied by apply_radio_fx:
  1. Butterworth bandpass 300–3400 Hz  — the critical step; phone/radio sound
  2. tanh soft-clip                    — AM transmitter drive/squash character
  3. In-band carrier hiss              — open-squelch feel
  4. Crackle (per-profile, optional)   — sparse static clicks + brief dropouts,
                                          the sound of a weaker/more distant set

Correct aviation radio bandpass is 300–3400 Hz.  TTS-Radio's pydub defaults
(HPF 4000, LPF 3000) are inverted and produce a band-reject — don't copy them.

The controller you talk to is the strong, clean local station (DEFAULT_PROFILE:
no crackle). Other aircraft each get a randomised profile — different passband,
drive, hiss, and a dose of crackle — so the party line sounds like a dozen
different radios at a dozen different ranges, not one voice twelve times.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfilt

# DSP constants — all named so re-tuning is a one-line edit
BANDPASS_LOW_HZ  = 300
BANDPASS_HIGH_HZ = 3400
BANDPASS_ORDER   = 4
DRIVE_GAIN       = 3.4    # tanh gain; useful range 2–5. Higher = more AM grit.
NOISE_DB         = -38.0  # carrier hiss level relative to 0 dBFS. Pilots get a
                          # louder, varied floor via random_profile; the
                          # controller (this default) stays the clean station.

# Crackle stage — 0 disables it (the controller). At amount=1.0 the rates below
# give heavy static; pilots draw 0.1–0.6 so most reads are lightly fried.
CRACKLE_CLICK_RATE = 48.0   # static clicks per second at amount=1.0
CRACKLE_DROP_RATE  =  3.0   # signal dropouts per second at amount=1.0


@dataclass(frozen=True)
class RadioProfile:
    """A single radio's character. The controller you talk to is one fixed
    profile (the module defaults). Other aircraft on the frequency each get a
    randomised profile so the party line sounds like a dozen different radios at
    a dozen different ranges — not the same voice twelve times."""
    low_hz:   float = BANDPASS_LOW_HZ
    high_hz:  float = BANDPASS_HIGH_HZ
    drive:    float = DRIVE_GAIN
    noise_db: float = NOISE_DB
    gain:     float = 1.0      # overall level (distant traffic reads quieter)
    crackle:  float = 0.0      # static fry/dropouts, 0–1. 0 = clean (controller).

    @property
    def key(self) -> tuple:
        return (round(self.low_hz), round(self.high_hz),
                self.drive, self.noise_db, self.crackle)


DEFAULT_PROFILE = RadioProfile()


def random_profile(rng: np.random.Generator) -> RadioProfile:
    """A plausibly-different radio: narrower/wider passband, more or less AM
    drive, more hiss, a touch quieter, and its own dose of crackle. The ranges
    are deliberately wide so two aircraft rarely sound alike. Seed the generator
    off a callsign to keep a given aircraft consistent across its lines."""
    return RadioProfile(
        low_hz=float(rng.uniform(240, 380)),
        high_hz=float(rng.uniform(2500, 3500)),
        drive=float(rng.uniform(2.6, 5.6)),
        noise_db=float(rng.uniform(-40.0, -27.0)),
        gain=float(rng.uniform(0.6, 0.95)),    # other traffic sits below your own controller
        crackle=float(rng.uniform(0.12, 0.6)),
    )


@lru_cache(maxsize=64)
def _bandpass_sos_edges(sr: int, low_hz: float, high_hz: float, order: int) -> np.ndarray:
    """Butterworth bandpass SOS coefficients, cached per (sr, edges)."""
    nyq  = sr / 2.0
    low  = max(1.0, low_hz)  / nyq
    high = min(nyq - 1.0, high_hz) / nyq
    return butter(order, [low, high], btype='band', output='sos')


def _bandpass_sos(sr: int) -> np.ndarray:
    """Default-profile bandpass (kept for the existing call sites/tests)."""
    return _bandpass_sos_edges(sr, BANDPASS_LOW_HZ, BANDPASS_HIGH_HZ, BANDPASS_ORDER)


def _apply_crackle(
    out: np.ndarray,
    sr: int,
    sos: np.ndarray,
    amount: float,
    gen: np.random.Generator,
) -> np.ndarray:
    """Static fry + brief signal dropouts — a weaker/more distant VHF set.

    Two independent effects, both scaled by `amount` (0–1) and drawn from `gen`
    so a seeded call is reproducible:
      - sparse clicks, bandpassed into the radio band so they read as 'fry'
        rather than wideband pops;
      - short multiplicative dropouts (the carrier momentarily fading), with a
        smoothed envelope so the dip edges don't themselves click.
    Returns a float32 array the same length as `out`. amount<=0 is a no-op."""
    n = len(out)
    if amount <= 0.0 or n == 0:
        return out
    work = out.astype(np.float64)

    n_clicks = int(CRACKLE_CLICK_RATE * amount * n / sr)
    if n_clicks > 0:
        clicks = np.zeros(n)
        pos = gen.integers(0, n, size=n_clicks)
        amp = gen.uniform(0.2, 0.7, size=n_clicks) * (0.5 + 0.5 * amount)
        clicks[pos] = amp * gen.choice((-1.0, 1.0), size=n_clicks)
        work += sosfilt(sos, clicks)

    n_drops = int(CRACKLE_DROP_RATE * amount * n / sr)
    if n_drops > 0:
        env = np.ones(n)
        starts = gen.integers(0, n, size=n_drops)
        lengths = (gen.uniform(0.015, 0.07, size=n_drops) * sr).astype(int)
        depths = gen.uniform(0.3, 0.85, size=n_drops) * amount
        for start, length, depth in zip(starts, lengths, depths):
            env[start:start + length] *= (1.0 - depth)
        win = max(1, int(0.004 * sr))      # ~4 ms smoothing of dip edges
        env = np.convolve(env, np.ones(win) / win, mode='same')
        work *= env

    return work.astype(np.float32)


def apply_radio_fx(
    samples: np.ndarray,
    sr: int,
    *,
    profile: RadioProfile | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """
    Apply VHF aviation radio character to float32 mono audio.

    Parameters
    ----------
    samples : float32 1-D mono array
    sr      : sample rate in Hz
    profile : radio character to apply. None → the default controller radio
              (identical to the original behaviour). Pass random_profile(...) to
              make a piece of ambient traffic sound like its own distinct radio.
    rng     : optional numpy Generator for the carrier hiss, so a seeded call is
              reproducible. None → fresh entropy (the original behaviour).

    Returns
    -------
    float32 mono array, same length, hard-clipped to ±1.0
    """
    if samples.ndim != 1:
        raise ValueError(f"Expected 1-D mono array, got shape {samples.shape}")

    p = profile or DEFAULT_PROFILE
    sos = _bandpass_sos_edges(sr, p.low_hz, p.high_hz, BANDPASS_ORDER)

    # 1. Bandpass — run in float64 for numerical stability, convert back
    out = sosfilt(sos, samples.astype(np.float64)).astype(np.float32)

    # 2. Soft-clip (tanh AM drive)
    out = np.tanh(p.drive * out).astype(np.float32)

    # 3. In-band carrier hiss
    noise_amp = 10.0 ** (p.noise_db / 20.0)
    gen  = rng if rng is not None else np.random.default_rng()
    hiss = gen.standard_normal(len(out)).astype(np.float64) * noise_amp
    hiss = sosfilt(sos, hiss).astype(np.float32)
    out  = (out * p.gain) + hiss

    # 4. Crackle — static fry + dropouts, for traffic that isn't your local
    #    controller. Default profile has crackle=0, so this is a no-op there.
    if p.crackle > 0.0:
        out = _apply_crackle(out, sr, sos, p.crackle, gen)

    # Hard clip — tanh already bounds the voice to (-1,1); hiss and crackle can
    # push slightly over on peaks. Clip rather than normalize to avoid pumping.
    return np.clip(out, -1.0, 1.0)


def pitch_shift(samples: np.ndarray, semitones: float) -> np.ndarray:
    """Cheap pitch shift by resampling (also nudges duration/formants). Used to
    give ambient voices variety when only one TTS voice is configured. Small
    shifts (±2 semitones) read as 'a different pilot' on a radio without sounding
    obviously sped-up. semitones=0 is a no-op."""
    if not semitones:
        return samples
    factor = 2.0 ** (semitones / 12.0)          # >1 → higher & shorter
    n_out = max(1, int(round(len(samples) / factor)))
    src_idx = np.linspace(0.0, len(samples) - 1, n_out)
    return np.interp(src_idx, np.arange(len(samples)), samples).astype(np.float32)


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
