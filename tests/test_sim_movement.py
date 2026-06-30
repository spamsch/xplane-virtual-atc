"""The simulated data source must stand in for X-Plane while debugging: the
aircraft can be moved and the radios tuned, and those mutations drive the same
position-keyed logic (flight-plan progression, handoff gating) a live sim would.

No X-Plane, no LLM, no audio."""

import asyncio
from unittest.mock import patch

import pytest

import backend.server as srv
from airport.parser import Airport, Frequency, Runway
from airport.database import AirportDB
from navigation.navaids import Navaid, NavaidDB
from flightplan.plan import parse_route
from atc.session import ATCSession, Station, Phase
from xplane.simulator import Scenario, ScenarioSimulator


# ── Unit: ScenarioSimulator mutation ─────────────────────────────────────────

def _scn(**over) -> Scenario:
    data = {
        "name": "T", "description": "",
        "aircraft": {"icao": "C172", "callsign": "D-EIYD"},
        "departure_airport": "EDLI",
        "conditions": {"qnh": 1015, "wind_dir": 250, "wind_kts": 8},
        "position": {"lat": 51.962, "lon": 8.544, "alt_ft": 450,
                     "heading": 250, "on_ground": True},
    }
    data.update(over)
    return Scenario.from_dict(data)


def test_simulator_seeds_weather_from_scenario():
    sim = ScenarioSimulator(_scn())
    st = sim.state
    assert st.qnh_hpa == 1015            # round(1015/33.8639 * 33.8639)
    assert int(st.wind_dir_deg) == 250
    assert int(st.wind_speed_kts) == 8


def test_set_position_partial_update_holds_other_axes():
    sim = ScenarioSimulator(_scn())
    sim.set_position(alt_ft=3000, on_ground=False, gs_kts=110)
    st = sim.state
    assert st.lat == pytest.approx(51.962)      # untouched
    assert st.lon == pytest.approx(8.544)       # untouched
    assert st.alt_ind_ft == pytest.approx(3000)
    assert st.elevation_m == pytest.approx(3000 * 0.3048)
    assert st.on_ground < 0.5
    assert st.gs_kts == pytest.approx(110, abs=0.5)


def test_set_position_heading_wraps():
    sim = ScenarioSimulator(_scn())
    sim.set_position(heading=370)
    assert sim.state.heading_mag == pytest.approx(10)


def test_tune_updates_com_in_xplane_units():
    sim = ScenarioSimulator(_scn())
    sim.tune(1, 118.355)
    sim.tune(2, 121.880)
    assert sim.state.com1_mhz == pytest.approx(118.355, abs=0.005)
    assert sim.state.com2_mhz == pytest.approx(121.880, abs=0.005)


# ── Integration: move/route_jump drive the server's stage machine ────────────

def _edli() -> Airport:
    ap = Airport(icao="EDLI", name="Bielefeld", elevation_ft=433, lat=51.962, lon=8.544)
    ap.frequencies = [Frequency(54, "Tower", 118.355, "Bielefeld Info")]
    ap.runways = [Runway("07", "25", 51.96, 8.53, 51.96, 8.56, 30.0, 0.0, 0.0)]
    return ap


def _eddg() -> Airport:
    ap = Airport(icao="EDDG", name="Muenster Osnabrueck", elevation_ft=160, lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(53, "Ground", 121.88, "Ground"),
        Frequency(54, "Tower", 129.805, "Tower"),
        Frequency(55, "Approach", 129.3, "Approach"),
    ]
    ap.runways = [Runway("07", "25", 52.13, 7.66, 52.13, 7.71, 45.0, 0.0, 0.0)]
    return ap


@pytest.fixture
def wired(monkeypatch):
    adb = AirportDB({"EDLI": _edli(), "EDDG": _eddg()})
    ndb = NavaidDB({"OSN": [Navaid("OSN", 52.2001, 8.2855, "VOR", "OSNABRUECK VOR", "ED")]})
    fp = parse_route("EDLI OSN EDDG", adb, ndb)
    session = ATCSession(departure=fp.departure.airport, destination=fp.destination.airport,
                         aircraft=None, callsign="D-EIYD",
                         conditions={"EDLI": {"qnh": 1015, "active_runway": "25"},
                                     "EDDG": {"qnh": 1018, "active_runway": "25"}},
                         flight_plan=fp)
    sim = ScenarioSimulator(_scn())

    events = []
    async def _fake_broadcast(msg_type, **data):
        events.append((msg_type, data))

    monkeypatch.setattr(srv, "_flightplan", fp)
    monkeypatch.setattr(srv, "_fp_stage", "departure")
    monkeypatch.setattr(srv, "_session", session)
    monkeypatch.setattr(srv, "_driver", sim)
    monkeypatch.setattr(srv, "_current_airport", fp.departure.airport)
    monkeypatch.setattr(srv, "_current_acft", None)
    monkeypatch.setattr(srv, "_airport_db", adb)
    monkeypatch.setattr(srv, "_source", "simulated")
    monkeypatch.setattr(srv, "_AUDIO_READY", False)
    monkeypatch.setattr(srv, "_tx_lock", asyncio.Lock())
    monkeypatch.setattr(srv, "_clients", set())
    monkeypatch.setattr(srv, "_broadcast", _fake_broadcast)
    monkeypatch.setattr(srv, "_airspace_note", lambda st: None)
    monkeypatch.setattr(srv.atc_engine, "proactive",
                        lambda **kw: "D-EIYD, basic service terminated, squawk 7000, "
                                     "contact Muenster Tower 129.805, good day.")
    return srv, fp, session, sim, events


def test_route_jump_departure_advances_to_enroute(wired):
    s, fp, session, sim, events = wired
    asyncio.run(s._route_jump("departure"))
    assert sim.state.on_ground < 0.5
    assert sim.state.is_flight_loaded
    assert srv._fp_stage == "enroute"
    assert session.current_station == Station.FIS


def test_route_jump_arrival_reaches_arrival_stage(wired):
    s, fp, session, sim, events = wired
    # Stage machine is one-way; walk it in order the way the UI presets do.
    asyncio.run(s._route_jump("departure"))
    asyncio.run(s._route_jump("arrival"))
    assert srv._fp_stage == "arrival"
    assert session.current_station == Station.TWR
    # arrival placed us inside the arrival range of the destination
    prog = fp.progress(sim.state.lat, sim.state.lon)
    assert prog.dist_to_dest_nm <= srv._FP_ARRIVAL_RANGE_NM


def test_route_jump_landed_taxis_at_destination(wired):
    s, fp, session, sim, events = wired
    for stage in ("departure", "arrival", "final", "landed"):
        asyncio.run(s._route_jump(stage))
    assert srv._fp_stage == "arrival_ground"
    assert session.current_station == Station.GND
    assert session.phase == Phase.GROUND_ARRIVAL
    assert sim.state.on_ground > 0.5


def test_move_aircraft_broadcasts_state(wired):
    s, fp, session, sim, events = wired
    asyncio.run(s._move_aircraft({"lat": 52.0, "lon": 8.4, "alt_ft": 2500, "on_ground": False}))
    assert sim.state.alt_ind_ft == pytest.approx(2500)
    assert any(t == "state_update" for (t, d) in events)


def test_move_rejected_without_simulator(wired, monkeypatch):
    s, fp, session, sim, events = wired
    monkeypatch.setattr(srv, "_driver", object())   # not a ScenarioSimulator
    asyncio.run(s._move_aircraft({"lat": 52.0, "lon": 8.4}))
    errs = [d.get("message", "") for (t, d) in events if t == "error"]
    assert any("simulated mode" in m for m in errs)
