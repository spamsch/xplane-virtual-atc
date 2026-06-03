"""
Taxi network parsing + routing + the session's taxi instruction.

Uses a tiny synthetic apt.dat so the route is known exactly:

    stand GA1 ── A ── (0)──A──(1)──B──(2)──C──(3)──runway──(4)
                                        └── edge (2)-(3) is tagged
                                            1204 "09,27"  → hold short

So from node 0, the route to hold short of runway 27 is "via A, B".
"""
import pytest

from airport.parser import parse_apt_dat
from airport import taxi
from atc.session import ATCSession

_APT = """\
I
1100 test fixture

1 6 0 0 EDDT Test Airport
53 12175 Test Ground
100 45.00 1 0 0.25 2 3 0 27 52.46010 9.69010 0 0 3 0 0 0 09 52.46010 9.69200 0 0 3 0 0 0
1300 52.460105 9.690105 90.0 tie_down props GA1
1201 52.46010 9.69010 both 0
1201 52.46010 9.69060 both 1
1201 52.46010 9.69110 both 2
1201 52.46010 9.69160 both 3
1201 52.46010 9.69210 both 4
1202 0 1 twoway taxiway_A
1202 1 2 twoway taxiway_B
1202 2 3 twoway taxiway_C
1204 departure 09,27
1202 3 4 twoway runway

99
"""


@pytest.fixture
def airport(tmp_path):
    apt = tmp_path / "apt.dat"
    apt.write_text(_APT)
    db = parse_apt_dat(apt, cache_path=tmp_path / "c.pkl")
    return db["EDDT"]


# ─────────────────────────── parsing ──────────────────────────────

class TestTaxiParsing:
    def test_nodes_parsed(self, airport):
        assert len(airport.taxi_nodes) == 5
        assert airport.taxi_nodes[0].lat == pytest.approx(52.46010)

    def test_edges_parsed_with_names(self, airport):
        names = [e.name for e in airport.taxi_edges if not e.is_runway]
        assert names == ["A", "B", "C"]

    def test_runway_edge_flagged(self, airport):
        rwy = [e for e in airport.taxi_edges if e.is_runway]
        assert len(rwy) == 1 and rwy[0].name == ""

    def test_active_zone_attaches_to_preceding_edge(self, airport):
        c_edge = next(e for e in airport.taxi_edges if e.name == "C")
        assert c_edge.active_zone == "09,27"

    def test_ramp_start_parsed(self, airport):
        assert [r.name for r in airport.ramp_starts] == ["GA1"]
        assert airport.has_taxi_network()


# ─────────────────────────── routing ──────────────────────────────

class TestRouting:
    def test_nearest_stand(self, airport):
        assert taxi.nearest_stand(airport, 52.46010, 9.69010) == "GA1"

    def test_nearest_stand_far_away_is_none(self, airport):
        assert taxi.nearest_stand(airport, 52.50, 9.80) is None

    def test_route_to_active_runway(self, airport):
        r = taxi.compute_route(airport, 52.46010, 9.69010, "27")
        assert r is not None
        assert r.taxiways == ["A", "B"]
        assert r.runway == "27"
        assert r.stand == "GA1"
        assert "taxi via A, B to hold short of runway 27" == r.describe()

    def test_route_accepts_combined_runway_id(self, airport):
        # "09/27" should match the same 1204 zone.
        r = taxi.compute_route(airport, 52.46010, 9.69010, "09/27")
        assert r is not None and r.taxiways == ["A", "B"]

    def test_unknown_runway_returns_none(self, airport):
        assert taxi.compute_route(airport, 52.46010, 9.69010, "16") is None

    def test_no_taxi_network_returns_none(self, airport):
        airport.taxi_nodes.clear()
        airport.taxi_edges.clear()
        assert taxi.compute_route(airport, 52.46010, 9.69010, "27") is None


# ─────────────────────── session integration ──────────────────────

class TestSessionTaxiInstruction:
    def _session(self, airport):
        return ATCSession(departure=airport, destination=None, aircraft=None,
                          callsign="D-EIYD",
                          conditions={airport.icao: {"active_runway": "27"}})

    def test_computed_route_is_handed_to_controller(self, airport):
        s = self._session(airport)
        instr = s._taxi_instruction(52.46010, 9.69010)
        assert "via A, B to hold short of runway 27" in instr
        assert "GA1" in instr
        assert "do NOT invent" in instr

    def test_no_position_forbids_invention(self, airport):
        s = self._session(airport)
        instr = s._taxi_instruction(None, None)
        assert "No published taxi route" in instr
        assert "do NOT invent" in instr
