"""
Pacing for the party line.

The level you pick (light / medium / heavy) is just a knob on two numbers:
how long the frequency stays quiet between interactions, and how often the
controller squeezes in another aircraft right before answering you. Everything
here is pure and RNG-seeded so it can be tested without a clock or a sound card.

The backend owns the actual loop and the audio; this owns the decisions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    name: str
    # Gap between interactions is drawn uniformly from [gap_min, gap_max] seconds.
    gap_min: float
    gap_max: float
    # Chance that, after you transmit, the controller works one other aircraft
    # before turning back to you ("…stand by. D-EKMA, descend…").
    interject_prob: float
    # Background atmosphere (squelch breaks, static, distant chatter) emitted
    # *between* transmissions, in events per minute, to keep the frequency from
    # sounding dead during the quiet.
    atmos_per_min: float = 0.0


LEVELS: dict[str, Level] = {
    "off":    Level("off",    gap_min=0.0,  gap_max=0.0,   interject_prob=0.0,  atmos_per_min=0.0),
    "light":  Level("light",  gap_min=55.0, gap_max=110.0, interject_prob=0.10, atmos_per_min=2.0),
    "medium": Level("medium", gap_min=22.0, gap_max=55.0,  interject_prob=0.28, atmos_per_min=5.0),
    "heavy":  Level("heavy",  gap_min=9.0,  gap_max=24.0,  interject_prob=0.50, atmos_per_min=12.0),
}

DEFAULT_LEVEL = "medium"


def resolve_level(name: str | None) -> Level:
    """Map a level name to its Level, falling back to the default. Unknown or
    empty names resolve to medium so a typo never silently disables traffic."""
    if not name:
        return LEVELS[DEFAULT_LEVEL]
    return LEVELS.get(name.lower().strip(), LEVELS[DEFAULT_LEVEL])


class AmbientPlanner:
    """Stateless-ish helper around a Level. Holds an RNG so a seed makes the
    whole sequence (gaps, coin-flips) reproducible in tests."""

    def __init__(self, level: str | Level = DEFAULT_LEVEL, rng: random.Random | None = None):
        self.level = level if isinstance(level, Level) else resolve_level(level)
        self.rng = rng or random.Random()

    @property
    def enabled(self) -> bool:
        return self.level.name != "off"

    def set_level(self, name: str | Level) -> None:
        self.level = name if isinstance(name, Level) else resolve_level(name)

    def next_gap(self) -> float:
        """Seconds of quiet before the next interaction should start."""
        if not self.enabled:
            return float("inf")
        lo, hi = self.level.gap_min, self.level.gap_max
        return self.rng.uniform(lo, hi)

    def should_interject(self) -> bool:
        """Coin-flip (weighted by level) for the 'work another aircraft before
        answering you' beat. Off when level is off."""
        if not self.enabled:
            return False
        return self.rng.random() < self.level.interject_prob

    def inter_line_gap(self) -> float:
        """Realistic silence between two lines of the same exchange (the pause
        while the other pilot keys up to read back)."""
        return self.rng.uniform(0.5, 1.6)

    def should_emit_atmosphere(self, tick_s: float) -> bool:
        """Whether to drop a background blip during a `tick_s`-second slice of the
        quiet between transmissions. Poisson-style: rate = atmos_per_min/60."""
        if not self.enabled or self.level.atmos_per_min <= 0:
            return False
        return self.rng.random() < (self.level.atmos_per_min / 60.0) * tick_s

    def pick_atmosphere(self) -> str:
        """Choose a background-event kind. Squelch breaks are the everyday sound
        of an open frequency, so they dominate; static and distant chatter are
        the seasoning."""
        return self.rng.choices(
            ("squelch", "static", "chatter"), weights=(0.6, 0.25, 0.15), k=1
        )[0]
