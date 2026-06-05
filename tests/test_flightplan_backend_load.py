"""End-to-end-ish: feed the backend a route string and confirm it stages a
plan-aware session — uncontrolled departure on CTAF, destination from the plan,
en-route FIS named. The Opus boundary check is mocked; no Claude, no X-Plane."""

import asyncio
from unittest.mock import patch

import pytest

import backend.server as srv
from airport.parser import Airport, Frequency, Runway
from airport.database import AirportDB
from navigation.navaids import Navaid, NavaidDB
from atc.session import Station


def _edli():
    ap = Airport(icao="EDLI", name="Bielefeld", elevation_ft=433, lat=51.962, lon=8.544)
    ap.frequencies = [Frequency(54, "Tower", 118.355, "Bielefeld Info")]
    ap.runways = [Runway("07", "25", 51.96, 8.53, 51.96, 8.56, 30.0, 0.0, 0.0)]
    return ap


def _eddg():
    ap = Airport(icao="EDDG", name="Muenster Osnabrueck", elevation_ft=160, lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(53, "Ground", 121.88, "Ground"),
        Frequency(54, "Tower", 129.805, "Tower"),
        Frequency(55, "Approach", 129.3, "Approach"),
    ]
    ap.runways = [Runway("07", "25", 52.13, 7.66, 52.13, 7.71, 45.0, 0.0, 0.0)]
    return ap


@pytest.fixture
def backend(monkeypatch):
    adb = AirportDB({"EDLI": _edli(), "EDDG": _eddg()})
    ndb = NavaidDB({"OSN": [Navaid("OSN", 52.2001, 8.2855, "VOR", "OSNABRUECK VOR", "ED")]})
    events = []

    async def _fake_broadcast(msg_type, **data):
        events.append((msg_type, data))

    monkeypatch.setattr(srv, "_airport_db", adb)
    monkeypatch.setattr(srv, "_navaid_db", ndb)
    monkeypatch.setattr(srv, "_flightplan", None)
    monkeypatch.setattr(srv, "_fp_stage", None)
    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_driver", None)
    monkeypatch.setattr(srv, "_current_acft", None)
    monkeypatch.setattr(srv, "_current_airport", None)
    monkeypatch.setattr(srv, "_source", "simulated")
    monkeypatch.setattr(srv, "_clients", set())
    monkeypatch.setattr(srv, "_broadcast", _fake_broadcast)
    monkeypatch.setattr(srv.atc_engine, "boundary_check",
                        lambda **kw: {"active_runway": "25",
                                      "atc_callsign": "Bielefeld Information",
                                      "notes": ""})
    # _set_airport_inner kicks off _ensure_airspace as a task; make it a no-op.
    async def _noop_airspace(icao):
        return None
    monkeypatch.setattr(srv, "_ensure_airspace", _noop_airspace)
    return srv, events


def test_load_flightplan_stages_uncontrolled_departure(backend):
    s, events = backend
    asyncio.run(s._load_flightplan("EDLI OSN EDDG"))

    assert s._flightplan is not None
    assert s._flightplan.summary() == "EDLI → OSN → EDDG"
    assert s._fp_stage == "departure"

    sess = s._session
    assert sess is not None
    # Uncontrolled departure → the session opens on CTAF (self-announce), and the
    # destination came from the plan.
    assert sess.current_station == Station.CTAF
    assert sess.destination is not None and sess.destination.icao == "EDDG"
    assert sess.flight_plan is not None

    # The UI was told a plan loaded, with the FIS named.
    loaded = [d for (t, d) in events if t == "flightplan_loaded"]
    assert loaded and loaded[0]["fis"]["callsign"] == "Bremen Information"


def test_clear_flightplan(backend):
    s, events = backend
    asyncio.run(s._load_flightplan("EDLI OSN EDDG"))
    asyncio.run(s._clear_flightplan())
    assert s._flightplan is None and s._fp_stage is None
    assert any(t == "flightplan_cleared" for (t, d) in events)


def test_bad_route_errors_without_crashing(backend):
    s, events = backend
    asyncio.run(s._load_flightplan("ONLYONE"))
    assert s._flightplan is None
    assert any(t == "error" for (t, d) in events)
