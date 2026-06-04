"""
Tests for the airspace package — geometry, vertical limits, OpenAIP parsing,
the spatial DB, and the country loader. No network: the loader is exercised
against a fixture file with downloads disabled.
"""

from __future__ import annotations

import json

import pytest

from airspace.database import (
    Airspace, AirspaceDB, airspace_from_openaip, _limit_to_ft,
)
from airspace import openaip


# A square CTR around (9.7, 52.45), surface to 2500 ft MSL.
CTR_OBJ = {
    "name": "CTR TESTFIELD", "type": 4, "icaoClass": 3,
    "geometry": {"type": "Polygon", "coordinates": [[
        [9.5, 52.3], [9.9, 52.3], [9.9, 52.6], [9.5, 52.6], [9.5, 52.3]]]},
    "lowerLimit": {"value": 0, "unit": 1, "referenceDatum": 0},
    "upperLimit": {"value": 2500, "unit": 1, "referenceDatum": 1},
}
# A wider TMA, 2500 ft to FL100.
TMA_OBJ = {
    "name": "TMA TEST", "type": 7, "icaoClass": 2,
    "geometry": {"type": "Polygon", "coordinates": [[
        [9.0, 52.0], [10.5, 52.0], [10.5, 53.0], [9.0, 53.0], [9.0, 52.0]]]},
    "lowerLimit": {"value": 2500, "unit": 1, "referenceDatum": 1},
    "upperLimit": {"value": 100, "unit": 6, "referenceDatum": 2},
}


# ─────────────────────────── vertical limits ─────────────────────────────────

class TestLimits:
    def test_feet(self):
        assert _limit_to_ft({"value": 2500, "unit": 1}) == 2500

    def test_flight_level(self):
        assert _limit_to_ft({"value": 100, "unit": 6}) == 10000

    def test_metres(self):
        assert _limit_to_ft({"value": 1000, "unit": 0}) == pytest.approx(3280.84, abs=1)

    def test_missing(self):
        assert _limit_to_ft(None) is None


# ─────────────────────────── parsing ─────────────────────────────────────────

class TestParse:
    def test_from_openaip(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert a.name == "CTR TESTFIELD"
        assert a.is_ctr and a.type_name == "CTR" and a.class_name == "D"
        assert a.upper_ft == 2500 and a.lower_ft == 0
        assert len(a.ring) == 5

    def test_describe(self):
        assert airspace_from_openaip(CTR_OBJ).describe() == "CTR TESTFIELD (Class D, SFC-2500 ft)"

    def test_no_geometry_returns_none(self):
        assert airspace_from_openaip({"name": "x", "type": 4}) is None

    def test_degenerate_ring_returns_none(self):
        bad = {"name": "x", "type": 4,
               "geometry": {"type": "Polygon", "coordinates": [[[9, 52], [9.1, 52]]]}}
        assert airspace_from_openaip(bad) is None


# ─────────────────────────── geometry ────────────────────────────────────────

class TestContainment:
    def test_point_inside(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert a.contains_point(9.7, 52.45)

    def test_point_outside(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert not a.contains_point(8.0, 52.45)
        assert not a.contains_point(9.7, 51.0)

    def test_bbox_reject(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert not a.contains_point(20.0, 60.0)

    def test_vertical_below_ceiling(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert a.contains(9.7, 52.45, alt_ft=1000)

    def test_vertical_above_ceiling(self):
        a = airspace_from_openaip(CTR_OBJ)
        assert not a.contains(9.7, 52.45, alt_ft=5000)

    def test_floor_excludes_low_aircraft(self):
        tma = airspace_from_openaip(TMA_OBJ)   # floor 2500 MSL
        assert not tma.contains(9.7, 52.45, alt_ft=1000)
        assert tma.contains(9.7, 52.45, alt_ft=5000)


# ─────────────────────────── DB queries ──────────────────────────────────────

class TestAirspaceDB:
    def _db(self):
        return AirspaceDB([airspace_from_openaip(CTR_OBJ), airspace_from_openaip(TMA_OBJ)])

    def test_at_low_returns_ctr(self):
        names = [a.name for a in self._db().at(52.45, 9.7, 1000)]
        assert "CTR TESTFIELD" in names and "TMA TEST" not in names

    def test_at_high_returns_tma_not_ctr(self):
        names = [a.name for a in self._db().at(52.45, 9.7, 5000)]
        assert "TMA TEST" in names and "CTR TESTFIELD" not in names

    def test_in_ctr(self):
        assert self._db().in_ctr(52.45, 9.7, 1000).name == "CTR TESTFIELD"
        assert self._db().in_ctr(52.45, 9.7, 5000) is None   # above the CTR

    def test_controlling_prefers_ctr(self):
        assert self._db().controlling(52.45, 9.7, 1000).is_ctr

    def test_nothing_outside(self):
        assert self._db().at(0.0, 0.0) == []
        assert self._db().in_ctr(0.0, 0.0) is None


# ─────────────────────────── loader (no network) ─────────────────────────────

class TestLoader:
    def test_country_for_icao(self):
        assert openaip.country_for_icao("EDDV") == "de"
        assert openaip.country_for_icao("LOWW") == "at"
        assert openaip.country_for_icao("KJFK") is None   # US not mapped

    def test_load_country_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(openaip, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(openaip, "_loaded", {})
        (tmp_path / "de_asp.json").write_text(json.dumps([CTR_OBJ, TMA_OBJ]))
        db = openaip.load_country("de", allow_download=False)
        assert db is not None and len(db) == 2
        assert db.in_ctr(52.45, 9.7, 1000).name == "CTR TESTFIELD"

    def test_load_country_missing_no_download(self, tmp_path, monkeypatch):
        monkeypatch.setattr(openaip, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(openaip, "_loaded", {})
        assert openaip.load_country("de", allow_download=False) is None

    def test_load_for_airport_unknown_country(self, monkeypatch):
        monkeypatch.setattr(openaip, "_loaded", {})
        assert openaip.load_for_airport("KJFK", allow_download=False) is None
