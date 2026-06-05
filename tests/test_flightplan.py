"""Tests for the FlightPlan route parser + staging logic. Synthetic airports and
an in-memory NavaidDB; no X-Plane data or LLM."""

import pytest

from airport.parser import Airport, Frequency
from airport.database import AirportDB
from navigation.navaids import Navaid, NavaidDB
from flightplan.plan import (
    parse_route, is_controlled, fis_station_for, field_service_freq, RouteError,
)


# ------------------------------------------------------------------ #
# Fixtures

def _edli() -> Airport:
    # Bielefeld: AFIS field — only a Tower-coded "Info" frequency, no Ground.
    ap = Airport(icao="EDLI", name="Bielefeld", elevation_ft=433, lat=51.962, lon=8.544)
    ap.frequencies = [Frequency(54, "Tower", 118.355, "Bielefeld Info")]
    return ap


def _eddg() -> Airport:
    # Münster/Osnabrück: real control service — Ground, Tower, Approach, Departure.
    ap = Airport(icao="EDDG", name="Muenster Osnabrueck", elevation_ft=160, lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(50, "ATIS", 127.18, "ATIS"),
        Frequency(53, "Ground", 121.88, "Ground"),
        Frequency(54, "Tower", 129.805, "Tower"),
        Frequency(55, "Approach", 129.3, "Approach"),
        Frequency(56, "Departure", 129.3, "Radar"),
    ]
    return ap


@pytest.fixture
def airport_db() -> AirportDB:
    return AirportDB({"EDLI": _edli(), "EDDG": _eddg()})


@pytest.fixture
def navaid_db() -> NavaidDB:
    return NavaidDB({"OSN": [Navaid("OSN", 52.2001, 8.2855, "VOR", "OSNABRUECK VOR", "ED")]})


# ------------------------------------------------------------------ #
# Control-status classification

def test_controlled_detection():
    assert is_controlled(_eddg()) is True
    # EDLI has only a Tower-coded Info freq → NOT controlled.
    assert is_controlled(_edli()) is False


def test_service_freq_for_uncontrolled_field():
    # Falls back to the (AFIS) Tower frequency for self-announce.
    assert field_service_freq(_edli()) == pytest.approx(118.355)


# ------------------------------------------------------------------ #
# Route parsing

def test_parse_full_route(airport_db, navaid_db):
    fp = parse_route("EDLI OSN EDDG", airport_db, navaid_db)
    assert [w.ident for w in fp.waypoints] == ["EDLI", "OSN", "EDDG"]
    assert fp.departure.ident == "EDLI"
    assert fp.departure.controlled is False
    assert fp.destination.ident == "EDDG"
    assert fp.destination.controlled is True
    assert fp.intermediate[0].kind == "VOR"
    assert fp.total_nm > 0


def test_parse_accepts_arrows_and_dct(airport_db, navaid_db):
    fp = parse_route("EDLI -> OSN -> EDDG", airport_db, navaid_db)
    assert [w.ident for w in fp.waypoints] == ["EDLI", "OSN", "EDDG"]
    fp2 = parse_route("EDLI DCT OSN DCT EDDG", airport_db, navaid_db)
    assert [w.ident for w in fp2.waypoints] == ["EDLI", "OSN", "EDDG"]


def test_unknown_intermediate_is_skipped(airport_db, navaid_db):
    fp = parse_route("EDLI ZZZZZ EDDG", airport_db, navaid_db)
    assert [w.ident for w in fp.waypoints] == ["EDLI", "EDDG"]


def test_unknown_airport_raises(airport_db, navaid_db):
    with pytest.raises(RouteError):
        parse_route("XXXX OSN EDDG", airport_db, navaid_db)


def test_too_short_raises(airport_db, navaid_db):
    with pytest.raises(RouteError):
        parse_route("EDLI", airport_db, navaid_db)


def test_controlled_override(airport_db, navaid_db):
    # Force EDLI controlled (user correction in the popup).
    fp = parse_route("EDLI OSN EDDG", airport_db, navaid_db,
                     controlled_overrides={"EDLI": True})
    assert fp.departure.controlled is True


def test_no_navaid_db_still_stages(airport_db):
    fp = parse_route("EDLI OSN EDDG", airport_db, None)
    assert [w.ident for w in fp.waypoints] == ["EDLI", "EDDG"]


# ------------------------------------------------------------------ #
# FIS station

def test_fis_region_split():
    assert fis_station_for(52.1, 7.7).callsign == "Bremen Information"   # north
    assert fis_station_for(49.0, 9.0).callsign == "Langen Information"   # south
    assert fis_station_for(40.0, -100.0).callsign == "Information"       # outside DE


def test_plan_picks_fis(airport_db, navaid_db):
    fp = parse_route("EDLI OSN EDDG", airport_db, navaid_db)
    assert fp.fis.callsign == "Bremen Information"


# ------------------------------------------------------------------ #
# Progress

def test_progress_advances_along_route(airport_db, navaid_db):
    fp = parse_route("EDLI OSN EDDG", airport_db, navaid_db)
    at_dep = fp.progress(51.962, 8.544)
    assert at_dep.flown_nm == pytest.approx(0.0, abs=2.0)
    assert at_dep.dist_to_dest_nm > at_dep.dist_from_dep_nm

    at_dest = fp.progress(52.134, 7.685)
    assert at_dest.dist_to_dest_nm == pytest.approx(0.0, abs=2.0)
    assert at_dest.remaining_nm < 5.0
    assert at_dest.fraction > 0.8
