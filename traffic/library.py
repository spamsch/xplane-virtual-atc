"""
The interaction library — what the *other* aircraft on the frequency say.

An Interaction is a short scripted exchange (one or more radio lines) between
the controller and one or two other aircraft. Each carries metadata that lets
the planner pick something that fits the moment:

  station       : which controller it belongs to — ground | tower | approach |
                  radar | fis. This is what keeps Tower traffic off the Ground
                  frequency: the planner only draws from interactions whose
                  `station` matches the frequency you're tuned to.
  flight_rules  : VFR | IFR. Default operation is VFR-only; IFR ships in the
                  library but is filtered out unless you opt a station into a
                  mixed VFR/IFR ruleset.
  sizes         : airport sizes the script fits — small | medium | large.
                  Empty means "any size".
  enroute       : true for FIS / en-route chatter that has no home airport.

Templates use {placeholders}. They are written in the same compact form the
real ATC text uses (e.g. "D-EKMA", "runway 27R", "QNH 1013"), so the existing
radio_text.to_spoken normaliser speaks them correctly at synthesis time. The
display copy and the spoken copy are therefore the same string — one pipeline.

Placeholders filled by render():
  {atc_callsign}  controller callsign, e.g. "Hannover Tower"
  {callsign}      the other aircraft (one, consistent across the exchange)
  {callsign2}     a second aircraft, for traffic-advisory style scripts
  {runway}        active runway designator
  {qnh}           QNH in hPa
  {wind}          wind as "270 degrees 8 knots"
  {airport}       airport city name, e.g. "Hannover"

The library is plain JSON under traffic/interactions/, plus any directory the
user points AMBIENT_LIBRARY_DIR at. Each file is either a list of interactions
or {"interactions": [...]}. Drop in a file, restart, done — that's the
"customisable later" hook.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).parent / "interactions"

VALID_STATIONS = {"ground", "tower", "approach", "radar", "fis"}
VALID_RULES = {"VFR", "IFR"}
VALID_SIZES = {"small", "medium", "large"}


# ─────────────────────────────── schema ──────────────────────────────────────

@dataclass(frozen=True)
class Line:
    """One rendered radio transmission within an interaction."""
    speaker: str   # 'atc' | 'pilot'
    text: str      # compact form, ready for radio_text.to_spoken


@dataclass
class Interaction:
    id: str
    station: str
    flight_rules: str = "VFR"
    sizes: tuple[str, ...] = ()          # empty → any size
    enroute: bool = False
    weight: float = 1.0
    lines: list[dict] = field(default_factory=list)   # [{speaker, text}, ...]

    @staticmethod
    def from_dict(d: dict) -> "Interaction":
        station = str(d["station"]).lower().strip()
        if station not in VALID_STATIONS:
            raise ValueError(
                f"interaction {d.get('id', '?')!r}: station {station!r} not in {sorted(VALID_STATIONS)}"
            )
        rules = str(d.get("flight_rules", "VFR")).upper().strip()
        if rules not in VALID_RULES:
            raise ValueError(
                f"interaction {d.get('id', '?')!r}: flight_rules {rules!r} not in {sorted(VALID_RULES)}"
            )
        sizes = tuple(s.lower().strip() for s in d.get("sizes", ()))
        bad = set(sizes) - VALID_SIZES
        if bad:
            raise ValueError(f"interaction {d.get('id', '?')!r}: unknown sizes {sorted(bad)}")
        lines = d.get("lines") or []
        if not lines:
            raise ValueError(f"interaction {d.get('id', '?')!r}: no lines")
        norm_lines = []
        for ln in lines:
            sp = str(ln["speaker"]).lower().strip()
            if sp not in ("atc", "pilot"):
                raise ValueError(
                    f"interaction {d.get('id', '?')!r}: speaker {sp!r} must be atc|pilot"
                )
            norm_lines.append({"speaker": sp, "text": str(ln["text"])})
        return Interaction(
            id=str(d["id"]),
            station=station,
            flight_rules=rules,
            sizes=sizes,
            enroute=bool(d.get("enroute", False)),
            weight=float(d.get("weight", 1.0)),
            lines=norm_lines,
        )

    def fits_size(self, size: Optional[str]) -> bool:
        if not self.sizes:
            return True
        return size in self.sizes if size else True


# ─────────────────────────── airport size ────────────────────────────────────

def _runway_length_m(rwy) -> float:
    """Great-circle length of a runway from its two end coordinates (metres)."""
    R = 6_371_000.0
    p1, l1, p2, l2 = map(math.radians, (rwy.lat1, rwy.lon1, rwy.lat2, rwy.lon2))
    dphi, dl = p2 - p1, l2 - l1
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def classify_size(airport) -> str:
    """Bucket an airport into small | medium | large for chatter density and
    content. Heuristic, deliberately simple and tunable:

      large  — an approach/radar service, or a runway ≳ 2500 m, or ≥ 3 runways.
               Busy controlled fields with airline traffic.
      medium — has a Tower (controlled), but smaller. Regional / GA-heavy fields.
      small  — uncontrolled or info-only. Sleepy grass/GA strips.
    """
    freq_codes = {f.type_code for f in airport.frequencies}
    has_app = bool(freq_codes & {55, 56})     # Approach / Departure(Radar)
    has_twr = 54 in freq_codes
    n_rwy = len(airport.runways)
    longest = max((_runway_length_m(r) for r in airport.runways), default=0.0)

    if has_app or longest >= 2500.0 or n_rwy >= 3:
        return "large"
    if has_twr or longest >= 1200.0:
        return "medium"
    return "small"


# ─────────────────────────── callsign generation ─────────────────────────────

# German GA registration class letters (D-E single piston, D-I twin, D-G/K
# motor glider/TMG, D-M ultralight). Good enough to read as varied local VFR.
_GA_CLASS = ("E", "I", "G", "K", "M")
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Airline ICAO codes seen at German fields. The telephony name is what gets
# spoken; radio_text.to_spoken expands "DLH472" → "Lufthansa four seven two".
# Only used when a station is opted into IFR — VFR-only never touches these.
AIRLINES = ("DLH", "EWG", "BER", "CFG", "BAW", "AFR", "KLM", "RYR", "EZY", "WZZ")


def random_vfr_callsign(rng: random.Random) -> str:
    """A plausible German VFR registration, e.g. 'D-EKMA'."""
    return "D-" + rng.choice(_GA_CLASS) + "".join(rng.choice(_LETTERS) for _ in range(3))


def random_ifr_callsign(rng: random.Random) -> str:
    """A plausible airline callsign token, e.g. 'DLH472'. Spoken via telephony."""
    return rng.choice(AIRLINES) + str(rng.randint(10, 9999))


def random_callsign(flight_rules: str, rng: random.Random) -> str:
    return random_ifr_callsign(rng) if flight_rules.upper() == "IFR" else random_vfr_callsign(rng)


_RESERVED_SQUAWKS = {"7500", "7600", "7700", "7000", "2000", "0000", "1200"}


def random_squawk(rng: random.Random) -> str:
    """A plausible discrete transponder code: four octal digits, not a reserved
    code and not in the 7xxx block. For ambient radar/departure assignments."""
    while True:
        code = "".join(str(rng.randint(0, 7)) for _ in range(4))
        if code not in _RESERVED_SQUAWKS and not code.startswith("7"):
            return code


# ─────────────────────────── rendering ───────────────────────────────────────

@dataclass
class RenderContext:
    """The live situation an interaction is filled against."""
    atc_callsign: str = "Information"
    runway: str = ""
    qnh: str = ""
    wind: str = ""
    airport: str = ""


def _default_runway(ctx_runway: str) -> str:
    rwy = (ctx_runway or "").strip()
    return rwy if rwy and rwy.lower() != "unknown" else ""


@dataclass
class Rendered:
    """A filled interaction, ready to synthesize. `callsign` is the primary
    other-aircraft on the exchange — the backend seeds that aircraft's voice and
    radio character off it, so it sounds like one consistent station."""
    interaction: Interaction
    callsign: str
    callsign2: str
    lines: list[Line]


def render(interaction: Interaction, ctx: RenderContext, rng: random.Random) -> Rendered:
    """Fill an interaction's templates against the live context.

    One (or two) other-aircraft callsigns are drawn up front and stay constant
    across every line of the exchange — the reply has to name the same aircraft
    the call came from.
    """
    callsign = random_callsign(interaction.flight_rules, rng)
    callsign2 = random_callsign(interaction.flight_rules, rng)
    while callsign2 == callsign:
        callsign2 = random_callsign(interaction.flight_rules, rng)

    fields = {
        "atc_callsign": ctx.atc_callsign or "Information",
        "callsign": callsign,
        "callsign2": callsign2,
        "runway": _default_runway(ctx.runway),
        "qnh": (ctx.qnh or "").strip(),
        "wind": (ctx.wind or "").strip(),
        "airport": (ctx.airport or "").strip(),
        # A discrete transponder code for this exchange — one per interaction so
        # the assignment and its read-back match (mostly used by Radar/Departure).
        "squawk": random_squawk(rng),
    }

    out: list[Line] = []
    for ln in interaction.lines:
        try:
            text = ln["text"].format_map(_SafeDict(fields))
        except Exception as e:   # pragma: no cover — bad template, skip the line
            log.warning(f"render: bad template in {interaction.id!r}: {e}")
            continue
        text = _tidy(text)
        if text:
            out.append(Line(speaker=ln["speaker"], text=text))
    return Rendered(interaction=interaction, callsign=callsign, callsign2=callsign2, lines=out)


class _SafeDict(dict):
    """An unknown {placeholder} renders as empty rather than raising."""
    def __missing__(self, key):  # noqa: D401
        return ""


def _tidy(text: str) -> str:
    """Collapse the gaps an empty placeholder leaves behind ("runway , cleared")."""
    text = " ".join(text.split())
    text = text.replace(" ,", ",").replace(" .", ".")
    text = text.replace("runway ,", "runway").replace("runway .", "runway")
    while ",," in text:
        text = text.replace(",,", ",")
    return text.strip().strip(",").strip()


# ─────────────────────────── library container ───────────────────────────────

class InteractionLibrary:
    def __init__(self, interactions: Iterable[Interaction]):
        self._all: list[Interaction] = list(interactions)

    def __len__(self) -> int:
        return len(self._all)

    @property
    def all(self) -> list[Interaction]:
        return list(self._all)

    def candidates(
        self,
        *,
        station: str,
        size: Optional[str],
        enroute: bool,
        rules: Iterable[str] = ("VFR",),
    ) -> list[Interaction]:
        """Every interaction that fits the frequency, airport size, and ruleset.

        `rules` is the set of flight rules allowed on *this* station right now —
        ("VFR",) by default; ("VFR", "IFR") for a station opted into mixed
        traffic. Enroute scripts and airport scripts are mutually exclusive.
        """
        allow = {r.upper() for r in rules}
        out = []
        for it in self._all:
            if it.station != station:
                continue
            if it.enroute != enroute:
                continue
            if it.flight_rules not in allow:
                continue
            if not it.fits_size(size):
                continue
            out.append(it)
        return out

    def pick(
        self,
        *,
        station: str,
        size: Optional[str],
        enroute: bool,
        rules: Iterable[str] = ("VFR",),
        rng: random.Random,
    ) -> Optional[Interaction]:
        """Weighted-random pick of a fitting interaction, or None if nothing fits."""
        cands = self.candidates(station=station, size=size, enroute=enroute, rules=rules)
        if not cands:
            return None
        weights = [max(0.0, it.weight) for it in cands]
        if sum(weights) <= 0:
            return rng.choice(cands)
        return rng.choices(cands, weights=weights, k=1)[0]


# ─────────────────────────── loading ─────────────────────────────────────────

def _load_file(path: Path) -> list[Interaction]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"traffic library: could not read {path.name}: {e}")
        return []
    raw = data.get("interactions", data) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        log.warning(f"traffic library: {path.name} is not a list of interactions")
        return []
    out: list[Interaction] = []
    for entry in raw:
        try:
            out.append(Interaction.from_dict(entry))
        except Exception as e:
            log.warning(f"traffic library: skipping bad interaction in {path.name}: {e}")
    return out


def load_library(extra_dir: Optional[Path] = None) -> InteractionLibrary:
    """Load the built-in interactions plus anything in `extra_dir` (user library).

    User files are loaded last, so a user interaction with the same id wins by
    being added — duplicates are tolerated, the planner just sees both.
    """
    interactions: list[Interaction] = []
    dirs = [_BUILTIN_DIR]
    if extra_dir:
        dirs.append(Path(extra_dir))
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json")):
            interactions.extend(_load_file(path))
    log.info(f"Traffic library: {len(interactions)} interactions from {len(dirs)} dir(s)")
    return InteractionLibrary(interactions)
