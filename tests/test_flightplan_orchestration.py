"""Drive the backend's position-keyed flight-plan stage machine through a whole
journey — departure → en route → arrival → ground — asserting the service in
charge advances correctly. No X-Plane, no LLM, no audio: the driver state and
engine.proactive are stubbed."""

import asyncio
from unittest.mock import patch

import pytest

import backend.server as srv
from airport.parser import Airport, Frequency, Runway
from airport.database import AirportDB
from navigation.navaids import Navaid, NavaidDB
from flightplan.plan import parse_route
from atc.session import ATCSession, Station, Phase
from xplane.connector import FlightState


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


class _FakeDriver:
    def __init__(self):
        self._s = FlightState()
    def set(self, lat, lon, on_ground, alt_ft=2500, gs_kts=110):
        self._s.lat, self._s.lon = lat, lon
        self._s.on_ground = 0.0 if not on_ground else 1.0
        self._s.alt_ind_ft = alt_ft
        self._s.groundspeed_ms = gs_kts / 1.94384
    @property
    def state(self):
        return self._s


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
    driver = _FakeDriver()

    events = []
    async def _fake_broadcast(msg_type, **data):
        events.append((msg_type, data))

    monkeypatch.setattr(srv, "_flightplan", fp)
    monkeypatch.setattr(srv, "_fp_stage", "departure")
    monkeypatch.setattr(srv, "_session", session)
    monkeypatch.setattr(srv, "_driver", driver)
    monkeypatch.setattr(srv, "_current_acft", None)
    monkeypatch.setattr(srv, "_source", "xplane")
    monkeypatch.setattr(srv, "_AUDIO_READY", False)
    monkeypatch.setattr(srv, "_tx_lock", asyncio.Lock())
    monkeypatch.setattr(srv, "_clients", set())
    monkeypatch.setattr(srv, "_broadcast", _fake_broadcast)
    monkeypatch.setattr(srv, "_airspace_note", lambda st: None)
    monkeypatch.setattr(srv.atc_engine, "proactive",
                        lambda **kw: "D-EIYD, basic service terminated, squawk 7000, "
                                     "contact Muenster Tower 129.805, good day.")
    return srv, fp, session, driver, events


def _stage():
    return srv._fp_stage


def test_full_journey_stage_progression(wired):
    s, fp, session, driver, events = wired

    # 1) On the ground at EDLI → uncontrolled CTAF, stays in 'departure'.
    assert session.current_station == Station.CTAF
    driver.set(51.962, 8.544, on_ground=True, alt_ft=450, gs_kts=0)
    asyncio.run(s._flightplan_progress(driver.state))
    assert _stage() == "departure"

    # 2) Airborne, climbed out >5 NM from EDLI → en route FIS.
    driver.set(52.15, 8.35, on_ground=False, alt_ft=3000)
    asyncio.run(s._flightplan_progress(driver.state))
    assert _stage() == "enroute"
    assert session.current_station == Station.FIS
    assert session.phase == Phase.EN_ROUTE_FIS
    assert session._atc_callsign() == "Bremen Information"

    # 3) Within ~13 NM of EDDG → arrival; controlled field → Approach, and the
    #    FIS handoff line was emitted.
    driver.set(52.134, 7.90, on_ground=False, alt_ft=2500)
    asyncio.run(s._flightplan_progress(driver.state))
    assert _stage() == "arrival"
    assert session.current_station == Station.TWR
    assert session.phase == Phase.APPROACH
    atc_texts = [d.get("text", "") for (t, d) in events if t == "atc_message"]
    assert any("terminated" in x.lower() for x in atc_texts)

    # 4) Landed and slow at EDDG → ground.
    driver.set(52.134, 7.690, on_ground=True, alt_ft=170, gs_kts=12)
    asyncio.run(s._flightplan_progress(driver.state))
    assert _stage() == "arrival_ground"
    assert session.current_station == Station.GND
    assert session.phase == Phase.GROUND_ARRIVAL


def test_no_premature_enroute_on_ground(wired):
    s, fp, session, driver, events = wired
    # Still on the ground a little down the field — must not go en route.
    driver.set(51.98, 8.50, on_ground=True, alt_ft=450, gs_kts=15)
    asyncio.run(s._flightplan_progress(driver.state))
    assert _stage() == "departure"


def test_fis_directive_is_grounded(wired):
    s, fp, session, driver, events = wired
    driver.set(52.15, 8.30, on_ground=False, alt_ft=3000)
    # Just exercise that a directive string is produced without error.
    d = s._fis_directive(driver.state)
    assert isinstance(d, str) and len(d) > 0
