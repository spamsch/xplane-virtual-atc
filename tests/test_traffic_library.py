"""
Tests for the ambient traffic library — schema, filtering, rendering, airport
size, callsigns. Pure stdlib; no audio, no network, no X-Plane.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from traffic.library import (
    Interaction,
    InteractionLibrary,
    RenderContext,
    classify_size,
    load_library,
    random_vfr_callsign,
    random_ifr_callsign,
    render,
)


# ─────────────────────────── fixtures / helpers ──────────────────────────────

def _mk(id_, station, rules="VFR", sizes=(), enroute=False, weight=1.0,
        lines=None) -> Interaction:
    return Interaction(
        id=id_, station=station, flight_rules=rules, sizes=tuple(sizes),
        enroute=enroute, weight=weight,
        lines=lines or [{"speaker": "pilot", "text": "{atc_callsign}, {callsign}, hello."}],
    )


@dataclass
class _Rwy:
    # Default end coords span ~0.012° of longitude at 52°N ≈ 820 m — a short strip.
    name1: str = "09"
    name2: str = "27"
    lat1: float = 52.46
    lon1: float = 9.680
    lat2: float = 52.46
    lon2: float = 9.692
    width_m: float = 45.0
    displaced1_m: float = 0.0
    displaced2_m: float = 0.0


@dataclass
class _Freq:
    type_code: int
    type_name: str = "x"
    freq_mhz: float = 120.0
    name: str = "x"


@dataclass
class _Airport:
    icao: str = "TEST"
    name: str = "Test Field"
    elevation_ft: int = 100
    lat: float = 52.46
    lon: float = 9.70
    frequencies: list = None
    runways: list = None

    def __post_init__(self):
        if self.frequencies is None:
            self.frequencies = []
        if self.runways is None:
            self.runways = []


# ─────────────────────────── schema validation ───────────────────────────────

class TestSchema:
    def test_from_dict_minimal(self):
        it = Interaction.from_dict({
            "id": "x", "station": "ground",
            "lines": [{"speaker": "atc", "text": "hi"}],
        })
        assert it.station == "ground"
        assert it.flight_rules == "VFR"
        assert it.sizes == ()

    def test_bad_station_raises(self):
        with pytest.raises(ValueError, match="station"):
            Interaction.from_dict({"id": "x", "station": "clearance",
                                   "lines": [{"speaker": "atc", "text": "hi"}]})

    def test_bad_rules_raises(self):
        with pytest.raises(ValueError, match="flight_rules"):
            Interaction.from_dict({"id": "x", "station": "ground", "flight_rules": "SVFR",
                                   "lines": [{"speaker": "atc", "text": "hi"}]})

    def test_bad_speaker_raises(self):
        with pytest.raises(ValueError, match="speaker"):
            Interaction.from_dict({"id": "x", "station": "ground",
                                   "lines": [{"speaker": "controller", "text": "hi"}]})

    def test_no_lines_raises(self):
        with pytest.raises(ValueError, match="no lines"):
            Interaction.from_dict({"id": "x", "station": "ground", "lines": []})

    def test_station_and_sizes_normalised(self):
        it = Interaction.from_dict({
            "id": "x", "station": "GROUND", "sizes": ["LARGE", "Small"],
            "lines": [{"speaker": "ATC", "text": "hi"}],
        })
        assert it.station == "ground"
        assert set(it.sizes) == {"large", "small"}
        assert it.lines[0]["speaker"] == "atc"


# ─────────────────────────── filtering / selection ───────────────────────────

class TestSelection:
    def _lib(self):
        return InteractionLibrary([
            _mk("g1", "ground", sizes=("small", "medium", "large")),
            _mk("t1", "tower", sizes=("medium", "large")),
            _mk("t_ifr", "tower", rules="IFR", sizes=("large",)),
            _mk("f1", "fis", enroute=True),
            _mk("a1", "approach", sizes=("large",)),
        ])

    def test_station_filter(self):
        cands = self._lib().candidates(station="ground", size="small", enroute=False)
        assert {c.id for c in cands} == {"g1"}

    def test_no_tower_on_ground(self):
        # The headline requirement: Ground frequency never surfaces Tower scripts.
        cands = self._lib().candidates(station="ground", size="large", enroute=False)
        assert all(c.station == "ground" for c in cands)

    def test_size_filter_excludes_small_for_tower(self):
        cands = self._lib().candidates(station="tower", size="small", enroute=False)
        assert cands == []   # t1 needs medium/large

    def test_vfr_only_excludes_ifr(self):
        cands = self._lib().candidates(station="tower", size="large", enroute=False, rules=("VFR",))
        assert {c.id for c in cands} == {"t1"}

    def test_mixed_rules_includes_ifr(self):
        cands = self._lib().candidates(station="tower", size="large", enroute=False,
                                       rules=("VFR", "IFR"))
        assert {c.id for c in cands} == {"t1", "t_ifr"}

    def test_enroute_separated_from_airport(self):
        assert self._lib().candidates(station="fis", size=None, enroute=False) == []
        cands = self._lib().candidates(station="fis", size=None, enroute=True)
        assert {c.id for c in cands} == {"f1"}

    def test_pick_returns_none_when_empty(self):
        assert self._lib().pick(station="radar", size=None, enroute=False,
                                rng=random.Random(1)) is None

    def test_pick_is_deterministic_with_seed(self):
        lib = self._lib()
        a = lib.pick(station="tower", size="large", enroute=False,
                     rules=("VFR", "IFR"), rng=random.Random(42))
        b = lib.pick(station="tower", size="large", enroute=False,
                     rules=("VFR", "IFR"), rng=random.Random(42))
        assert a.id == b.id

    def test_empty_sizes_matches_any(self):
        lib = InteractionLibrary([_mk("any", "ground", sizes=())])
        for size in ("small", "medium", "large", None):
            assert lib.candidates(station="ground", size=size, enroute=False)


# ─────────────────────────── rendering ───────────────────────────────────────

class TestRender:
    def test_callsign_consistent_across_lines(self):
        it = _mk("x", "tower", lines=[
            {"speaker": "pilot", "text": "{atc_callsign}, {callsign}, ready."},
            {"speaker": "atc", "text": "{callsign}, cleared."},
            {"speaker": "pilot", "text": "Cleared, {callsign}."},
        ])
        r = render(it, RenderContext(atc_callsign="Hannover Tower"), random.Random(3))
        # Same aircraft named in every line.
        assert all(r.callsign in ln.text for ln in r.lines)

    def test_placeholders_filled(self):
        it = _mk("x", "tower", lines=[
            {"speaker": "atc", "text": "{callsign}, runway {runway}, QNH {qnh}, wind {wind}."},
        ])
        r = render(it, RenderContext(runway="27R", qnh="1013",
                                     wind="270 degrees 8 knots"), random.Random(1))
        txt = r.lines[0].text
        assert "27R" in txt and "1013" in txt and "270 degrees 8 knots" in txt
        assert "{" not in txt

    def test_missing_placeholder_tidied(self):
        # An empty runway must not leave "runway ," dangling.
        it = _mk("x", "tower", lines=[
            {"speaker": "atc", "text": "{callsign}, runway {runway}, cleared to land."},
        ])
        r = render(it, RenderContext(runway=""), random.Random(1))
        txt = r.lines[0].text
        assert "runway ," not in txt
        assert "  " not in txt

    def test_vfr_callsign_shape(self):
        cs = random_vfr_callsign(random.Random(0))
        assert cs.startswith("D-") and len(cs) == 6

    def test_ifr_callsign_shape(self):
        cs = random_ifr_callsign(random.Random(0))
        assert cs[:3].isalpha() and cs[3:].isdigit()


# ─────────────────────────── airport size ────────────────────────────────────

class TestClassifySize:
    def test_uncontrolled_is_small(self):
        ap = _Airport(frequencies=[_Freq(50)], runways=[_Rwy()])   # info only, short rwy
        assert classify_size(ap) == "small"

    def test_tower_is_at_least_medium(self):
        ap = _Airport(frequencies=[_Freq(54)], runways=[_Rwy()])
        assert classify_size(ap) == "medium"

    def test_approach_is_large(self):
        ap = _Airport(frequencies=[_Freq(54), _Freq(55)], runways=[_Rwy()])
        assert classify_size(ap) == "large"

    def test_many_runways_is_large(self):
        ap = _Airport(frequencies=[_Freq(54)], runways=[_Rwy(), _Rwy(), _Rwy()])
        assert classify_size(ap) == "large"

    def test_long_runway_is_large(self):
        # ~3 km runway via well-separated end coordinates.
        long_rwy = _Rwy(lat1=52.40, lon1=9.70, lat2=52.427, lon2=9.70)
        ap = _Airport(frequencies=[_Freq(54)], runways=[long_rwy])
        assert classify_size(ap) == "large"


# ─────────────────────────── built-in library ────────────────────────────────

class TestBuiltinLibrary:
    def test_loads_and_validates(self):
        lib = load_library()
        assert len(lib) > 0
        for it in lib.all:
            assert it.station in {"ground", "tower", "approach", "radar", "fis"}
            assert it.flight_rules in {"VFR", "IFR"}
            assert it.lines

    def test_has_vfr_for_every_core_station(self):
        lib = load_library()
        for station, enroute in [("ground", False), ("tower", False),
                                 ("approach", False), ("fis", True)]:
            cands = lib.candidates(station=station, size="large", enroute=enroute)
            assert cands, f"no VFR interactions for {station}"

    def test_enroute_is_vfr_only(self):
        lib = load_library()
        enroute = [it for it in lib.all if it.enroute]
        assert enroute
        assert all(it.flight_rules == "VFR" for it in enroute)

    def test_radar_library_exists_and_is_airport_based(self):
        lib = load_library()
        radar = [it for it in lib.all if it.station == "radar"]
        assert radar, "no radar interactions"
        # Radar/Departure is an airport service, not en route, and VFR by default.
        assert all(not it.enroute for it in radar)
        assert all(it.flight_rules == "VFR" for it in radar)
        # Reachable at a large field.
        assert lib.candidates(station="radar", size="large", enroute=False)

    def test_fis_has_wide_variety(self):
        lib = load_library()
        fis = [it for it in lib.all if it.station == "fis"]
        assert len(fis) >= 10

    def test_squawk_placeholder_filled_and_consistent(self):
        import re
        lib = load_library()
        ctx = RenderContext(atc_callsign="Hannover Radar", runway="27R", qnh="1013")
        it = next(i for i in lib.all if i.id == "rad_identify")
        r = render(it, ctx, random.Random(9))
        codes = re.findall(r"squawk (\d{4})", " ".join(ln.text for ln in r.lines))
        assert codes, "no squawk rendered"
        assert len(set(codes)) == 1                 # assignment and read-back match
        assert not codes[0].startswith("7")          # discrete code, not a 7xxx

    def test_builtin_renders_without_braces(self):
        lib = load_library()
        ctx = RenderContext(atc_callsign="Hannover Tower", runway="27R",
                            qnh="1013", wind="270 degrees 8 knots", airport="Hannover")
        rng = random.Random(11)
        for it in lib.all:
            r = render(it, ctx, rng)
            for ln in r.lines:
                assert "{" not in ln.text and "}" not in ln.text
                assert ln.text.strip()
