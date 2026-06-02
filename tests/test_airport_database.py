"""
Tests for spatial airport queries and runway containment.

No apt.dat needed — Airport and Runway objects are constructed directly.
"""
import pytest
from airport.parser import Airport, Frequency, Runway
from airport.database import AirportDB, haversine_nm, _point_on_runway


# ------------------------------------------------------------------ #
# Shared fixtures

def make_eddv() -> Airport:
    """Hannover: east-west runway, thresholds ~1.36 km apart."""
    ap = Airport(icao="EDDV", name="Hannover", elevation_ft=6,
                 lat=52.461, lon=9.695)
    ap.runways.append(Runway(
        name1="27", name2="09",
        lat1=52.461, lon1=9.685,
        lat2=52.461, lon2=9.705,
        width_m=45.0,
        displaced1_m=0.0, displaced2_m=0.0,
    ))
    ap.frequencies.append(Frequency(53, "Ground", 121.75, "Hannover Ground"))
    return ap


def make_eddf() -> Airport:
    """Frankfurt — placed well away from Hannover (~230 nm)."""
    ap = Airport(icao="EDDF", name="Frankfurt", elevation_ft=364,
                 lat=50.026, lon=8.543)
    return ap


@pytest.fixture
def db():
    airports = {"EDDV": make_eddv(), "EDDF": make_eddf()}
    return AirportDB(airports, margin_nm=5.0)


# ------------------------------------------------------------------ #
# Haversine

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_nm(52.461, 9.685, 52.461, 9.685) == pytest.approx(0.0, abs=0.01)

    def test_known_distance(self):
        # Hannover to Frankfurt: ~150-160 nm
        d = haversine_nm(52.461, 9.685, 50.026, 8.543)
        assert 140 < d < 170

    def test_symmetry(self):
        d1 = haversine_nm(52.0, 9.0, 50.0, 8.0)
        d2 = haversine_nm(50.0, 8.0, 52.0, 9.0)
        assert d1 == pytest.approx(d2, rel=1e-6)


# ------------------------------------------------------------------ #
# Nearest airport

class TestNearest:
    def test_at_airport_position(self, db):
        ap = db.nearest(52.461, 9.695)
        assert ap is not None
        assert ap.icao == "EDDV"

    def test_nearby_within_limit(self, db):
        # 2 nm east of Hannover threshold
        ap = db.nearest(52.461, 9.75)
        assert ap is not None
        assert ap.icao == "EDDV"

    def test_too_far_returns_none(self, db):
        # Somewhere over the North Sea
        ap = db.nearest(55.0, 5.0)
        assert ap is None

    def test_custom_radius_respected(self, db):
        # 3 nm from Hannover — inside default 5nm but asking for 2nm
        ap = db.nearest(52.461, 9.75, max_dist_nm=2.0)
        assert ap is None

    def test_get_by_icao(self, db):
        ap = db.get("EDDV")
        assert ap is not None and ap.icao == "EDDV"

    def test_get_by_icao_case_insensitive(self, db):
        ap = db.get("eddv")
        assert ap is not None

    def test_get_unknown_icao(self, db):
        assert db.get("ZZZZ") is None


# ------------------------------------------------------------------ #
# Runway containment

class TestRunwayContainment:
    @pytest.fixture
    def rwy(self):
        return make_eddv().runways[0]

    def test_midpoint_is_on_runway(self, rwy):
        # Exact centerline midpoint between the two thresholds
        assert _point_on_runway(rwy, 52.461, 9.695, margin_m=40.0)

    def test_threshold_1_is_on_runway(self, rwy):
        assert _point_on_runway(rwy, 52.461, 9.685, margin_m=40.0)

    def test_threshold_2_is_on_runway(self, rwy):
        assert _point_on_runway(rwy, 52.461, 9.705, margin_m=40.0)

    def test_point_north_of_runway_is_off(self, rwy):
        # ~1 km north of centerline — well outside 45m width + 40m margin
        assert not _point_on_runway(rwy, 52.470, 9.695, margin_m=40.0)

    def test_point_south_of_runway_is_off(self, rwy):
        assert not _point_on_runway(rwy, 52.452, 9.695, margin_m=40.0)

    def test_beyond_end_with_margin_is_on(self, rwy):
        # 25 m past the western threshold — within 40m margin
        # At lat 52.461: 1° lon ≈ 67,876 m, so 25 m ≈ 0.000368°
        assert _point_on_runway(rwy, 52.461, 9.6846, margin_m=40.0)

    def test_far_beyond_end_is_off(self, rwy):
        # Way west of runway
        assert not _point_on_runway(rwy, 52.461, 9.600, margin_m=40.0)

    def test_which_runway_via_db(self, db):
        # Midpoint of EDDV runway 27/09
        rwy = db.which_runway(make_eddv(), 52.461, 9.695)
        assert rwy is not None
        assert rwy.name1 == "27"

    def test_not_on_any_runway_returns_none(self, db):
        rwy = db.which_runway(make_eddv(), 52.470, 9.695)
        assert rwy is None
