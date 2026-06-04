"""
Tests for the ambient pacing engine (traffic.ambient) and the per-aircraft
audio variation (audio.radio). Deterministic via seeded RNGs; no clock, no
sound card.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from traffic.ambient import AmbientPlanner, LEVELS, resolve_level


# ─────────────────────────── level resolution ────────────────────────────────

class TestLevels:
    def test_known_levels(self):
        for name in ("off", "light", "medium", "heavy"):
            assert resolve_level(name).name == name

    def test_unknown_falls_back_to_medium(self):
        assert resolve_level("bogus").name == "medium"
        assert resolve_level("").name == "medium"
        assert resolve_level(None).name == "medium"

    def test_density_ordering(self):
        # Heavier levels mean shorter gaps and more interjections.
        light, medium, heavy = LEVELS["light"], LEVELS["medium"], LEVELS["heavy"]
        assert heavy.gap_max < medium.gap_max < light.gap_max
        assert heavy.interject_prob > medium.interject_prob > light.interject_prob


# ─────────────────────────── planner behaviour ───────────────────────────────

class TestPlanner:
    def test_off_is_disabled(self):
        p = AmbientPlanner("off")
        assert not p.enabled
        assert p.next_gap() == float("inf")
        assert p.should_interject() is False

    def test_enabled_levels(self):
        assert AmbientPlanner("medium").enabled
        assert AmbientPlanner("heavy").enabled

    def test_gap_within_band(self):
        p = AmbientPlanner("medium", rng=random.Random(1))
        lvl = LEVELS["medium"]
        for _ in range(200):
            g = p.next_gap()
            assert lvl.gap_min <= g <= lvl.gap_max

    def test_seeded_sequence_reproducible(self):
        a = AmbientPlanner("heavy", rng=random.Random(7))
        b = AmbientPlanner("heavy", rng=random.Random(7))
        assert [a.next_gap() for _ in range(5)] == [b.next_gap() for _ in range(5)]

    def test_set_level_switches_band(self):
        p = AmbientPlanner("light")
        p.set_level("heavy")
        assert p.level.name == "heavy"
        assert p.enabled
        p.set_level("off")
        assert not p.enabled

    def test_interject_rate_tracks_probability(self):
        p = AmbientPlanner("heavy", rng=random.Random(3))
        hits = sum(p.should_interject() for _ in range(2000))
        # Heavy is 0.5; allow a generous band for sampling noise.
        assert 0.4 < hits / 2000 < 0.6

    def test_inter_line_gap_is_short(self):
        p = AmbientPlanner("medium", rng=random.Random(2))
        for _ in range(100):
            assert 0.4 <= p.inter_line_gap() <= 1.7


# ─────────────────────────── audio variation ─────────────────────────────────

class TestRadioVariation:
    def test_random_profile_differs_from_default(self):
        from audio.radio import random_profile, DEFAULT_PROFILE
        p = random_profile(np.random.default_rng(5))
        assert p.key != DEFAULT_PROFILE.key
        assert 0.0 < p.gain <= 1.0

    def test_profiled_fx_stays_bounded(self):
        from audio.radio import apply_radio_fx, random_profile
        sig = (np.random.randn(16_000) * 3).astype(np.float32)
        p = random_profile(np.random.default_rng(9))
        out = apply_radio_fx(sig, 16_000, profile=p, rng=np.random.default_rng(0))
        assert out.dtype == np.float32
        assert np.max(np.abs(out)) <= 1.0
        assert len(out) == len(sig)

    def test_seeded_fx_is_reproducible(self):
        from audio.radio import apply_radio_fx, random_profile
        sig = (0.4 * np.sin(2 * np.pi * 1000 * np.arange(8000) / 8000)).astype(np.float32)
        p = random_profile(np.random.default_rng(2))
        a = apply_radio_fx(sig, 16_000, profile=p, rng=np.random.default_rng(123))
        b = apply_radio_fx(sig, 16_000, profile=p, rng=np.random.default_rng(123))
        np.testing.assert_array_equal(a, b)

    def test_default_call_unchanged(self):
        # The original two-arg signature must still work (controller voice path).
        from audio.radio import apply_radio_fx
        out = apply_radio_fx(np.zeros(8000, dtype=np.float32), 16_000)
        assert out.dtype == np.float32 and len(out) == 8000

    def test_pitch_shift_changes_length_and_is_noop_at_zero(self):
        from audio.radio import pitch_shift
        sig = np.linspace(-1, 1, 1000).astype(np.float32)
        up = pitch_shift(sig, 2.0)
        assert len(up) < len(sig)            # higher pitch → shorter
        down = pitch_shift(sig, -2.0)
        assert len(down) > len(sig)
        same = pitch_shift(sig, 0.0)
        np.testing.assert_array_equal(same, sig)


# ─────────────────────────── airline telephony ───────────────────────────────

class TestAirlineTelephony:
    def test_known_airline_expands(self):
        from audio.radio_text import to_spoken
        assert to_spoken("DLH472, cleared to land.").startswith("Lufthansa four seven two")

    def test_unknown_token_untouched(self):
        from audio.radio_text import to_spoken
        # Not a known ICAO → left exactly as-is (don't mangle stray tokens).
        assert "XYZ99" in to_spoken("report XYZ99 inbound")

    def test_vfr_reg_still_spelled(self):
        from audio.radio_text import to_spoken
        assert "Delta Echo India Yankee Delta" in to_spoken("D-EIYD, pass your message.")
