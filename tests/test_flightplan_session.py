"""Flight-plan journey behaviour in ATCSession: uncontrolled departure (CTAF,
no clearances), en-route FIS with a regional callsign, controlled arrival, and
the position-driven info-station context that ignores the pilot's wording.

The engine is patched with a capturing mock so we can assert what service_kind
and atc_callsign the session hands it."""

from unittest.mock import patch
import pytest

from airport.parser import Airport, Frequency, Runway
from airport.database import AirportDB
from navigation.navaids import Navaid, NavaidDB
from flightplan.plan import parse_route
from atc.session import ATCSession, Phase, Station


def _edli() -> Airport:
    ap = Airport(icao="EDLI", name="Bielefeld", elevation_ft=433, lat=51.962, lon=8.544)
    ap.frequencies = [Frequency(54, "Tower", 118.355, "Bielefeld Info")]
    ap.runways = [Runway("07", "25", 51.96, 8.53, 51.96, 8.56, 30.0, 0.0, 0.0)]
    return ap


def _eddg() -> Airport:
    ap = Airport(icao="EDDG", name="Muenster Osnabrueck", elevation_ft=160, lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(50, "ATIS", 127.18, "ATIS"),
        Frequency(53, "Ground", 121.88, "Ground"),
        Frequency(54, "Tower", 129.805, "Tower"),
        Frequency(55, "Approach", 129.3, "Approach"),
    ]
    ap.runways = [Runway("07", "25", 52.13, 7.66, 52.13, 7.71, 45.0, 0.0, 0.0)]
    return ap


@pytest.fixture
def plan():
    adb = AirportDB({"EDLI": _edli(), "EDDG": _eddg()})
    ndb = NavaidDB({"OSN": [Navaid("OSN", 52.2001, 8.2855, "VOR", "OSNABRUECK VOR", "ED")]})
    return parse_route("EDLI OSN EDDG", adb, ndb)


@pytest.fixture
def conditions():
    return {
        "EDLI": {"qnh": 1015, "wind_dir": 250, "wind_kts": 6, "active_runway": "25"},
        "EDDG": {"qnh": 1018, "wind_dir": 250, "wind_kts": 8, "active_runway": "25"},
    }


def _session(plan, conditions):
    return ATCSession(departure=plan.departure.airport, destination=plan.destination.airport,
                      aircraft=None, callsign="D-EIYD", conditions=conditions, flight_plan=plan)


class _Capture:
    """Patch engine.respond, record kwargs, return a canned reply."""
    def __init__(self, reply="D-EIYD, roger."):
        self.reply = reply
        self.calls = []

    def __enter__(self):
        def _fn(**kwargs):
            self.calls.append(kwargs)
            return self.reply
        self._p = patch("atc.session.atc_engine.respond", side_effect=_fn)
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()

    @property
    def last(self):
        return self.calls[-1]


# ------------------------------------------------------------------ #

def test_uncontrolled_departure_starts_on_ctaf(plan, conditions):
    s = _session(plan, conditions)
    assert s.current_station == Station.CTAF
    with _Capture() as cap:
        s.process("Bielefeld Information, D-EIYD, request departure information runway 25",
                  lat=51.962, lon=8.544, on_ground=True)
    assert cap.last["service_kind"] == "uncontrolled"
    assert cap.last["atc_callsign"] == "Bielefeld Information"


def test_uncontrolled_departure_never_assigns_squawk(plan, conditions):
    s = _session(plan, conditions)
    with _Capture("D-EIYD, runway 25 in use, QNH 1015, no reported traffic."):
        r = s.process("Bielefeld Information, D-EIYD, taxi for runway 25",
                      lat=51.962, lon=8.544, on_ground=True)
    assert r.squawk is None
    assert s.current_station == Station.CTAF   # 'Information' didn't flip to FIS


def test_no_taxi_route_injected_at_uncontrolled_field(plan, conditions):
    s = _session(plan, conditions)
    with _Capture() as cap:
        s.process("Bielefeld Information, D-EIYD, request taxi runway 25",
                  lat=51.962, lon=8.544, on_ground=True)
    extra = cap.last.get("extra_instructions") or ""
    assert "TAXI ROUTE" not in extra and "taxi route" not in extra.lower()


def test_enroute_fis_uses_regional_callsign(plan, conditions):
    s = _session(plan, conditions)
    s.enter_enroute_fis(context_airport=plan.destination.airport)
    assert s.current_station == Station.FIS
    with _Capture("D-EIYD, Bremen Information, basic service, squawk 7000.") as cap:
        s.process("Bremen Information, D-EIYD, request basic service",
                  lat=52.1, lon=8.2, on_ground=False, altitude_ft=2500)
    assert cap.last["service_kind"] == "fis"
    assert cap.last["atc_callsign"] == "Bremen Information"
    # The route note reaches the engine as situational context.
    assert "EDLI" in cap.last["flight_status"] and "EDDG" in cap.last["flight_status"]


def test_arrival_controlled_field_goes_to_tower(plan, conditions):
    s = _session(plan, conditions)
    s.enter_enroute_fis(plan.destination.airport)
    s.enter_arrival_field(plan.destination.airport, controlled=True)
    # VFR arrival is Tower-first (then Ground after landing).
    assert s.current_station == Station.TWR
    assert s.phase == Phase.APPROACH
    with _Capture("D-EIYD, Muenster Tower, runway 25, report final.") as cap:
        s.process("Muenster Tower, D-EIYD, inbound", lat=52.2, lon=7.8, on_ground=False)
    assert cap.last["service_kind"] == "control"


def test_pilot_info_call_does_not_flip_enroute_context(plan, conditions):
    # At the uncontrolled departure, calling "Information" must not switch the
    # session to the en-route FIS — it's the field's AFIS.
    s = _session(plan, conditions)
    with _Capture():
        s.process("Bielefeld Information, D-EIYD", lat=51.962, lon=8.544, on_ground=True)
    assert s.current_station == Station.CTAF
