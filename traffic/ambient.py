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


LEVELS: dict[str, Level] = {
    "off":    Level("off",    gap_min=0.0,  gap_max=0.0,  interject_prob=0.0),
    "light":  Level("light",  gap_min=55.0, gap_max=110.0, interject_prob=0.10),
    "medium": Level("medium", gap_min=22.0, gap_max=55.0,  interject_prob=0.28),
    "heavy":  Level("heavy",  gap_min=9.0,  gap_max=24.0,  interject_prob=0.50),
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
