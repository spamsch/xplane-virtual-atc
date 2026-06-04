"""
Tests for the airport auto-adoption guard in backend.server._should_adopt_airport.

The position-based detector must not yank the session to a neighbouring field
while the pilot is still climbing out of their departure airport — inside its
control zone, on its frequency, and not yet cleared to leave.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.server as bk
from airport.parser import Airport, Frequency, Runway


def _ap(icao: str, lat: float, lon: float, twr_mhz: float) -> Airport:
    ap = Airport(icao=icao, name=icao, elevation_ft=100, lat=lat, lon=lon)
    ap.frequencies = [Frequency(54, "Tower", twr_mhz, f"{icao} Tower")]
    ap.runways = [Runway("09", "27", lat, lon, lat, lon + 0.01, 45.0, 0.0, 0.0)]
    return ap


def _state(on_ground: bool, com1: float, lat: float, lon: float,
           alt_ft: float = 1000.0) -> SimpleNamespace:
    return SimpleNamespace(on_ground=1.0 if on_ground else 0.0,
                           com1_mhz=com1, lat=lat, lon=lon, alt_ind_ft=alt_ft)


CUR = _ap("EDDV", 52.46, 9.68, 118.175)
NEW = _ap("ETNW", 52.46, 9.42, 122.10)   # ~9-10 NM west of EDDV


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(bk, "_current_airport", CUR)
    monkeypatch.setattr(bk, "_source", "xplane")
    # default: a session that has NOT been cleared to leave the frequency
    monkeypatch.setattr(bk, "_session", SimpleNamespace(freq_change_cleared=False))


def test_inside_ctr_blocks(live):
    # ~0.8 NM from EDDV, airborne, on EDDV tower — clearly still in the zone.
    st = _state(False, 118.175, 52.46, 9.66)
    assert bk._should_adopt_airport(NEW, st) is False


def test_on_current_frequency_blocks(live):
    # Beyond the CTR but still tuned to EDDV Tower → stay.
    st = _state(False, 118.175, 52.46, 9.42)
    assert bk._should_adopt_airport(NEW, st) is False


def test_not_released_blocks(live):
    # Beyond CTR, off EDDV frequency, but never cleared to leave → stay.
    st = _state(False, 121.5, 52.46, 9.42)
    assert bk._should_adopt_airport(NEW, st) is False


def test_released_and_clear_adopts(live, monkeypatch):
    monkeypatch.setattr(bk, "_session", SimpleNamespace(freq_change_cleared=True))
    st = _state(False, 121.5, 52.46, 9.42)     # beyond CTR, off freq, cleared
    assert bk._should_adopt_airport(NEW, st) is True


def test_tuned_to_new_field_adopts(live):
    # Dialled the new field's tower in — honour intent even while close.
    st = _state(False, 122.10, 52.46, 9.55)
    assert bk._should_adopt_airport(NEW, st) is True


def test_on_ground_adopts(live):
    st = _state(True, 0.0, 52.46, 9.42)
    assert bk._should_adopt_airport(NEW, st) is True


def test_no_current_airport_adopts(monkeypatch):
    monkeypatch.setattr(bk, "_current_airport", None)
    st = _state(False, 0.0, 52.46, 9.42)
    assert bk._should_adopt_airport(NEW, st) is True


# ── With real airspace data loaded ───────────────────────────────────────────

from airspace.database import AirspaceDB, airspace_from_openaip


def _ctr(name, lon, lat, half=0.08):   # ~5 NM half-width; the two zones don't overlap
    return airspace_from_openaip({
        "name": name, "type": 4, "icaoClass": 3,
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon - half, lat - half], [lon + half, lat - half],
            [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]]},
        "lowerLimit": {"value": 0, "unit": 1, "referenceDatum": 0},
        "upperLimit": {"value": 2500, "unit": 1, "referenceDatum": 1},
    })


@pytest.fixture
def live_airspace(monkeypatch):
    monkeypatch.setattr(bk, "_current_airport", CUR)
    monkeypatch.setattr(bk, "_source", "xplane")
    monkeypatch.setattr(bk, "_session", SimpleNamespace(freq_change_cleared=False))
    db = AirspaceDB([_ctr("CTR EDDV", 9.68, 52.46), _ctr("CTR ETNW", 9.42, 52.46)])
    monkeypatch.setattr(bk, "_airspace_db", db)


def test_inside_current_ctr_keeps(live_airspace):
    # Airborne over EDDV, low — inside the EDDV CTR (real boundary) → stay.
    st = _state(False, 121.5, 52.46, 9.66)
    assert bk._should_adopt_airport(NEW, st) is False


def test_inside_new_ctr_adopts(live_airspace):
    # Now over Wunstorf, inside ETNW's CTR → adopt (arriving).
    st = _state(False, 121.5, 52.46, 9.43)
    assert bk._should_adopt_airport(NEW, st) is True


def test_outside_all_ctrs_released_adopts(live_airspace, monkeypatch):
    # Between the two zones (in neither), off-freq, and cleared to leave → adopt.
    monkeypatch.setattr(bk, "_session", SimpleNamespace(freq_change_cleared=True))
    st = _state(False, 121.5, 52.46, 9.55)
    assert bk._should_adopt_airport(NEW, st) is True


def test_outside_all_ctrs_not_released_keeps(live_airspace):
    # Outside both zones but never cleared to leave the frequency → stay.
    st = _state(False, 121.5, 52.46, 9.55)
    assert bk._should_adopt_airport(NEW, st) is False
