import pytest
import aircraft.database as db


@pytest.fixture(autouse=True)
def loaded():
    db.load()


class TestLookup:
    def test_known_type(self):
        a = db.lookup("C172")
        assert a is not None
        assert a.name == "Cessna 172 Skyhawk"

    def test_case_insensitive(self):
        assert db.lookup("c172") is not None
        assert db.lookup("C172") is not None

    def test_unknown_type_returns_none(self):
        assert db.lookup("XXXX") is None

    def test_empty_string_returns_none(self):
        assert db.lookup("") is None


class TestAircraftPerf:
    def test_c172_cruise(self):
        a = db.lookup("C172")
        assert a.cruise_kts == 122

    def test_c172_category(self):
        assert db.lookup("C172").category == "SEP"

    def test_c172_wake(self):
        assert db.lookup("C172").wake_cat == "L"

    def test_b738_is_jet(self):
        a = db.lookup("B738")
        assert a.category == "JET"
        assert a.wake_cat == "M"

    def test_da42_is_mep(self):
        assert db.lookup("DA42").category == "MEP"

    def test_describe_contains_icao(self):
        desc = db.lookup("C172").describe()
        assert "C172" in desc

    def test_describe_contains_cruise(self):
        desc = db.lookup("C172").describe()
        assert "122" in desc

    def test_max_alt_expressed_as_flight_level(self):
        # C172 max 14000 ft → FL140
        desc = db.lookup("C172").describe()
        assert "FL140" in desc
