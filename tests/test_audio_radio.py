"""
Tests for audio.radio — no audio hardware or network required.
All assertions operate on numpy arrays or WAV bytes.
"""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from audio.radio import (
    BANDPASS_LOW_HZ,
    BANDPASS_HIGH_HZ,
    NOISE_DB,
    DEFAULT_PROFILE,
    RadioProfile,
    apply_radio_fx,
    decode_wav,
    encode_wav,
)

SR = 16_000   # typical STT/radio sample rate


def _sine(freq_hz: float, amp: float = 0.5, sr: int = SR, secs: float = 1.0) -> np.ndarray:
    t = np.arange(int(sr * secs)) / sr
    return (amp * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


# ─────────────────────────── apply_radio_fx ──────────────────────────────────

class TestApplyRadioFx:

    def test_output_dtype_is_float32(self):
        out = apply_radio_fx(np.zeros(SR, dtype=np.float32), SR)
        assert out.dtype == np.float32

    def test_output_same_length(self):
        sig = np.random.randn(SR).astype(np.float32)
        out = apply_radio_fx(sig, SR)
        assert len(out) == len(sig)

    def test_bandpass_kills_50hz(self):
        # 50 Hz is well below the 300 Hz cutoff.
        # Use 3 s and check only the last second — the high-Q filter has a
        # slow-decaying onset transient at very low frequencies.
        sig = _sine(50, amp=1.0, secs=3.0)
        out = apply_radio_fx(sig, SR)
        assert np.max(np.abs(out[-SR:])) < 0.05

    def test_bandpass_kills_8khz(self):
        # 8 kHz is well above the 3400 Hz cutoff
        sig = _sine(8_000, amp=1.0)
        out = apply_radio_fx(sig, SR)
        assert np.max(np.abs(out)) < 0.05

    def test_bandpass_passes_1khz(self):
        # 1 kHz sits squarely in the aviation voice band
        sig = _sine(1_000, amp=0.5)
        out = apply_radio_fx(sig, SR)
        assert np.max(np.abs(out)) > 0.1

    def test_bandpass_passes_400hz(self):
        # 400 Hz is just above the 300 Hz cutoff
        sig = _sine(400, amp=0.5)
        out = apply_radio_fx(sig, SR)
        assert np.max(np.abs(out)) > 0.05

    def test_peak_within_unity(self):
        # Very loud input must still stay inside ±1.0
        sig = (np.random.randn(SR) * 5.0).astype(np.float32)
        out = apply_radio_fx(sig, SR)
        assert np.max(np.abs(out)) <= 1.0

    def test_silence_produces_only_hiss(self):
        # All-zero input → only carrier hiss, nowhere near full scale
        out = apply_radio_fx(np.zeros(SR, dtype=np.float32), SR)
        hiss_amp = 10.0 ** (NOISE_DB / 20.0)
        assert np.max(np.abs(out)) < hiss_amp * 20   # hiss + filter ringing

    def test_different_sample_rates(self):
        for sr in (8_000, 16_000, 22_050, 44_100):
            sig = _sine(1_000, amp=0.5, sr=sr)
            out = apply_radio_fx(sig, sr)
            assert len(out) == len(sig)
            assert out.dtype == np.float32


class TestCrackle:

    def test_default_profile_has_no_crackle(self):
        assert DEFAULT_PROFILE.crackle == 0.0

    def test_crackle_changes_the_signal(self):
        # Same voice, same hiss seed — only crackle differs → output must differ.
        sig = _sine(1_000, amp=0.5)
        clean = RadioProfile(crackle=0.0)
        fried = RadioProfile(crackle=0.5)
        a = apply_radio_fx(sig, SR, profile=clean, rng=np.random.default_rng(7))
        b = apply_radio_fx(sig, SR, profile=fried, rng=np.random.default_rng(7))
        assert not np.array_equal(a, b)

    def test_crackle_stays_within_unity(self):
        sig = (np.random.randn(SR) * 3).astype(np.float32)
        out = apply_radio_fx(sig, SR, profile=RadioProfile(crackle=1.0),
                             rng=np.random.default_rng(1))
        assert np.max(np.abs(out)) <= 1.0

    def test_crackle_is_reproducible_when_seeded(self):
        sig = _sine(1_000, amp=0.5)
        p = RadioProfile(crackle=0.6)
        a = apply_radio_fx(sig, SR, profile=p, rng=np.random.default_rng(42))
        b = apply_radio_fx(sig, SR, profile=p, rng=np.random.default_rng(42))
        np.testing.assert_array_equal(a, b)


# ─────────────────────────────── encode_wav ──────────────────────────────────

class TestEncodeWav:

    def test_returns_bytes(self):
        data = encode_wav(np.zeros(SR, dtype=np.float32), SR)
        assert isinstance(data, bytes)

    def test_valid_riff_wave_header(self):
        data = encode_wav(np.zeros(SR, dtype=np.float32), SR)
        assert data[:4] == b'RIFF'
        assert data[8:12] == b'WAVE'

    def test_correct_sample_rate_in_header(self):
        data = encode_wav(np.zeros(SR, dtype=np.float32), SR)
        with wave.open(io.BytesIO(data)) as wf:
            assert wf.getframerate() == SR

    def test_mono_channel(self):
        data = encode_wav(np.zeros(SR, dtype=np.float32), SR)
        with wave.open(io.BytesIO(data)) as wf:
            assert wf.getnchannels() == 1

    def test_16bit_samples(self):
        data = encode_wav(np.zeros(SR, dtype=np.float32), SR)
        with wave.open(io.BytesIO(data)) as wf:
            assert wf.getsampwidth() == 2

    def test_clips_positive_overflow(self):
        # +2.0 → int16 32767 → float 32767/32768 (≈0.99997); not 1.0 exactly
        samples = np.array([2.0], dtype=np.float32)
        decoded, _ = decode_wav(encode_wav(samples, SR))
        assert decoded[0] == pytest.approx(32767 / 32768, abs=1e-5)

    def test_clips_negative_overflow(self):
        # -2.0 → int16 -32768 → float -32768/32768 = -1.0 exactly
        samples = np.array([-2.0], dtype=np.float32)
        decoded, _ = decode_wav(encode_wav(samples, SR))
        assert decoded[0] == pytest.approx(-1.0, abs=1e-5)


# ─────────────────────────────── decode_wav ──────────────────────────────────

class TestDecodeWav:

    def test_roundtrip_silence(self):
        original = np.zeros(SR, dtype=np.float32)
        decoded, out_sr = decode_wav(encode_wav(original, SR))
        assert out_sr == SR
        np.testing.assert_allclose(decoded, original, atol=1e-4)

    def test_roundtrip_sine_440hz(self):
        original = _sine(440, amp=0.5)
        decoded, _ = decode_wav(encode_wav(original, SR))
        np.testing.assert_allclose(decoded, original, atol=1e-4)

    def test_sample_rate_preserved(self):
        data = encode_wav(np.zeros(100, dtype=np.float32), 22_050)
        _, out_sr = decode_wav(data)
        assert out_sr == 22_050

    def test_stereo_mixed_to_mono(self):
        val_int16 = 10_000
        frames = np.array([[val_int16, val_int16]] * 100, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(frames.tobytes())
        decoded, _ = decode_wav(buf.getvalue())
        expected = val_int16 / 32768.0   # decode scale is /32768
        assert len(decoded) == 100
        np.testing.assert_allclose(decoded, np.full(100, expected), atol=1e-4)

    def test_multichannel_mixed_to_mono(self):
        # 4-channel WAV: all channels at same value → mean should equal that value
        val_int16 = 8_000
        n_ch = 4
        frames = np.full((50, n_ch), val_int16, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(n_ch)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(frames.tobytes())
        decoded, _ = decode_wav(buf.getvalue())
        assert len(decoded) == 50
        np.testing.assert_allclose(decoded, np.full(50, val_int16 / 32768.0), atol=1e-4)

    def test_non_mono_input_raises(self):
        with pytest.raises(ValueError, match="1-D mono"):
            apply_radio_fx(np.zeros((100, 2), dtype=np.float32), SR)

    def test_rejects_unsupported_sample_width(self):
        # 3-byte samples aren't in our dtype_map
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(SR)
            wf.writeframes(b'\x00' * 300)
        with pytest.raises(ValueError, match="Unsupported"):
            decode_wav(buf.getvalue())
