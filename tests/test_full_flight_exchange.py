"""
Aspirational specification: full VFR flight EDDV → EDDG.

D-EIYD (Cessna 172) departs Hannover, transits via Hannover Radar and
Langen Information, arrives Münster/Osnabrück (~76 nm, westbound).

These tests are SKIPPED until atc/session.py is created, then XFAIL until
the behaviour is complete. Remove pytestmark when all phases pass.

Expected API contract for atc.session:

    from atc.session import ATCSession, Phase, Station

    session = ATCSession(
        departure=<Airport>,
        destination=<Airport>,
        aircraft=<AircraftPerf>,
        callsign="D-EIYD",
        conditions={
            "EDDV": {"qnh": 1018, "wind_dir": 270, "wind_kts": 8, "active_runway": "27L", "atis": "Charlie"},
            "EDDG": {"qnh": 1018, "wind_dir": 250, "wind_kts": 6, "active_runway": "25",  "atis": "Delta"},
        },
    )
    r: ATCResponse = session.process("pilot transmission")

    r.text              str   — full ATC transmission
    r.phase_after       Phase — session phase after this exchange
    r.station_after     Station
    r.squawk            str | None — squawk assigned in THIS response (not carried over)
    r.frequency_change  float | None — MHz to switch to (if handed off in this response)
    r.runway            str | None
    r.qnh               int | None

    session.phase           Phase — current phase (mutable for test setup)
    session.current_station Station — current station (mutable for test setup)
    session.current_airport Airport — current airport (mutable for test setup)
    session.squawk          str | None — currently assigned squawk
    session.squawk_history  list[str] — all squawks ever assigned this session
    session.conditions      dict
    session.aircraft        AircraftPerf
    session.departure       Airport
    session.destination     Airport

Station → parser mapping (atc.parser.parse() emits string codes; session maps them):
    parser "GND"  → Station.GND
    parser "TWR"  → Station.TWR
    parser "APP"  → Station.APP
    parser "DEP"  → Station.DEP
    parser "ATIS" → Station.FIS   (Langen Information calls use "Information" → "ATIS")
    "Radar" in callout → Station.RADAR (parser doesn't know this yet; session must handle)

Phase enum values (minimum required):
    PRE_DEPARTURE, GROUND_DEPARTURE, TAXIING, DEPARTING, EN_ROUTE,
    EN_ROUTE_FIS, APPROACH, CIRCUIT, GROUND_ARRIVAL, PARKED

Station enum values:
    GND, TWR, RADAR, FIS, APP, DEP

Note on Langen Information frequency: the correct Langen FIS sector frequency
depends on routing and is NOT hardcoded. Tests assert a frequency IS given,
not that it equals a specific value. 128.950 appears in the scenario text as
an example only.
"""

import re
import pytest
from unittest.mock import patch

from airport.parser import Airport, Frequency, Runway
from aircraft.database import AircraftPerf
from atc.session import ATCSession, Phase, Station


# ------------------------------------------------------------------ #
# Deterministic ATC mock — no real LLM calls

def _canned_response(pilot_message: str, atc_callsign: str,
                     extra_instructions: str | None) -> str:
    msg = pilot_message.lower()
    cs = atc_callsign.lower()

    # Extract pre-assigned squawk from engine extra_instructions
    sq = '0472'
    if extra_instructions:
        m = re.search(r'assign squawk (\d{4})', extra_instructions, re.IGNORECASE)
        if m:
            sq = m.group(1)

    # CTR exit instruction (injected by session._should_issue_ctr_exit)
    if extra_instructions and 'squawk 7000' in extra_instructions.lower():
        return ('D-EIYD, squawk 7000, frequency change approved. '
                'Contact Hannover Radar on 120.150.')

    # Ground at EDDV
    if 'ground' in cs and 'hannover' in cs:
        if 'startup' in msg or 'request startup' in msg:
            return ('D-EIYD, Hannover Ground, startup approved. QNH 1018, '
                    'wind 270/08, runway 27L. Taxi to holding point Alpha via Bravo. '
                    'Hold short runway 27L.')
        return ('D-EIYD, Hannover Ground, hold position. '
                'Contact Hannover Tower on 118.175. Good day.')

    # Tower at EDDV
    if 'tower' in cs and 'hannover' in cs:
        if 'ready for departure' in msg or 'holding' in msg or ('ready' in msg and 'alpha' in msg):
            return (f'D-EIYD, Hannover Tower, squawk {sq}. '
                    f'Line up and wait runway 27L.')
        if 'line' in msg or 'squawk' in msg:
            return ('D-EIYD, runway 27L, wind 270/08, cleared for takeoff. '
                    'After departure heading 200, not above 1500 feet until leaving the zone.')
        if 'heading' in msg or 'airborne' in msg or 'climbing' in msg:
            return 'D-EIYD, radar contact. Climb not above 2500 feet. Report CTR boundary.'
        if 'ctr' in msg or 'boundary' in msg:
            return ('D-EIYD, squawk 7000, frequency change approved. '
                    'Contact Hannover Radar on 120.150.')
        return 'D-EIYD, roger.'

    # Hannover Radar / Departure
    if 'radar' in cs or 'departure' in cs:
        return ('D-EIYD, Hannover Radar, identified. '
                'VFR to Münster/Osnabrück. Contact Langen Information on 128.950. Good day.')

    # FIS / Langen Information
    if 'information' in cs:
        if 'approaching' in msg or 'frequency change' in msg or ('request' in msg and 'zone' in msg):
            return ('D-EIYD, Langen Information, frequency change approved. '
                    'Contact Münster Approach on 121.250. Good day.')
        return ('D-EIYD, Langen Information, roger. QNH 1018. '
                'No known traffic on your route. Report approaching Münster control zone.')

    # Approach at EDDG
    if 'approach' in cs and 'münster' in cs:
        if 'reporting' in msg:
            return ('D-EIYD, Münster Approach, join right base runway 25. '
                    'Contact Münster Tower on 118.700.')
        # CTR entry (inbound, request entry, control zone)
        return (f'D-EIYD, Münster Approach, squawk {sq}. '
                f'Cleared to enter control zone. Not below 1500 feet. '
                f'Runway 25. Proceed to Tango.')

    # Tower at EDDG
    if 'tower' in cs and 'münster' in cs:
        if 'landed' in msg:
            return 'D-EIYD, vacate left, contact Münster Ground on 121.800.'
        if 'vacating' in msg or '121.800' in msg:
            return 'D-EIYD, Münster Tower, good day.'
        return ('D-EIYD, Münster Tower, runway 25, wind 250/06, cleared to land. '
                'Vacate left after landing.')

    # Ground at EDDG
    if 'ground' in cs and 'münster' in cs:
        if 'request taxi' in msg or 'vacated' in msg or 'vacating' in msg:
            return ('D-EIYD, Münster Ground, taxi to General Aviation via Bravo. '
                    'QNH 1018.')
        return 'D-EIYD, readback correct. Have a good day.'

    return 'D-EIYD, roger.'


@pytest.fixture(autouse=True)
def _mock_engine(monkeypatch):
    def _respond(pilot_message, airport, acft, callsign, conditions,
                 atc_callsign, history=None, model=None, extra_instructions=None):
        return _canned_response(pilot_message, atc_callsign, extra_instructions)
    monkeypatch.setattr('atc.engine.respond', _respond)


# ------------------------------------------------------------------ #
# Helpers

def _is_valid_squawk(code: str) -> bool:
    return bool(re.fullmatch(r'[0-7]{4}', str(code)))


def _mentions_freq(text: str, freq_mhz: float) -> bool:
    """Accept NNN.NNN or NNN.NN formats."""
    return f"{freq_mhz:.3f}" in text or f"{freq_mhz:.2f}" in text


def _make_session(eddv, eddg, c172, conditions) -> "ATCSession":
    return ATCSession(
        departure=eddv,
        destination=eddg,
        aircraft=c172,
        callsign="D-EIYD",
        conditions=conditions,
    )


# ------------------------------------------------------------------ #
# Airport fixtures (no apt.dat needed — constructed from known data)

@pytest.fixture(scope="module")
def eddv() -> Airport:
    ap = Airport(icao="EDDV", name="Hannover", elevation_ft=183,
                 lat=52.461, lon=9.685)
    ap.frequencies = [
        Frequency(50, "ATIS",      126.850, "Hannover Information"),
        Frequency(53, "Ground",    121.900, "Hannover Ground"),
        Frequency(54, "Tower",     118.175, "Hannover Tower"),
        Frequency(55, "Approach",  120.800, "Hannover Approach"),
        Frequency(56, "Departure", 120.150, "Hannover Radar"),
    ]
    ap.runways = [
        Runway("27L", "09R", 52.4610, 9.6730, 52.4610, 9.7030, 45.0, 0.0, 0.0),
        Runway("27R", "09L", 52.4575, 9.6730, 52.4575, 9.7030, 45.0, 0.0, 0.0),
    ]
    return ap


@pytest.fixture(scope="module")
def eddg() -> Airport:
    ap = Airport(icao="EDDG", name="Münster/Osnabrück", elevation_ft=160,
                 lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(50, "ATIS",     126.975, "Münster Information"),
        Frequency(53, "Ground",   121.800, "Münster Ground"),
        Frequency(54, "Tower",    118.700, "Münster Tower"),
        Frequency(55, "Approach", 121.250, "Münster Approach"),
    ]
    ap.runways = [
        Runway("07", "25", 52.1340, 7.6700, 52.1340, 7.7050, 45.0, 0.0, 0.0),
    ]
    return ap


@pytest.fixture(scope="module")
def c172() -> AircraftPerf:
    import aircraft.database as acdb
    acdb.load()
    return acdb.lookup("C172")


@pytest.fixture(scope="module")
def conditions() -> dict:
    return {
        "EDDV": {"qnh": 1018, "wind_dir": 270, "wind_kts": 8,
                 "active_runway": "27L", "atis": "Charlie"},
        "EDDG": {"qnh": 1018, "wind_dir": 250, "wind_kts": 6,
                 "active_runway": "25",  "atis": "Delta"},
    }


# ------------------------------------------------------------------ #
# Phase 1 — EDDV Ground (121.900)

class TestPhase1_EDDVGround:
    STARTUP = (
        "Hannover Ground, D-EIYD, Cessna 172, General Aviation Apron, "
        "information Charlie, request startup and taxi, VFR to Münster/Osnabrück."
    )
    READY = "D-EIYD, ready at holding point Alpha, runway 27L."

    @pytest.fixture
    def fresh(self, eddv, eddg, c172, conditions):
        return _make_session(eddv, eddg, c172, conditions)

    def test_initial_phase_is_pre_departure(self, fresh):
        assert fresh.phase == Phase.PRE_DEPARTURE

    def test_startup_response_approves(self, fresh):
        # Some controllers say "startup approved", others acknowledge via taxi clearance.
        r = fresh.process(self.STARTUP)
        text = r.text.lower()
        assert "startup" in text or "taxi" in text or "approved" in text

    def test_startup_gives_qnh_1018(self, fresh):
        r = fresh.process(self.STARTUP)
        assert "1018" in r.text
        assert r.qnh == 1018

    def test_startup_assigns_runway_27(self, fresh):
        r = fresh.process(self.STARTUP)
        assert r.runway in ("27L", "27R", "27")
        assert r.runway in r.text

    def test_startup_includes_taxi_and_hold_short(self, fresh):
        r = fresh.process(self.STARTUP)
        text = r.text.lower()
        assert "taxi" in text or "taxy" in text
        assert "hold short" in text or "holding point" in text

    def test_startup_addresses_callsign(self, fresh):
        r = fresh.process(self.STARTUP)
        assert "D-EIYD" in r.text

    def test_startup_phase_transitions_to_ground_departure(self, fresh):
        r = fresh.process(self.STARTUP)
        assert r.phase_after in (Phase.GROUND_DEPARTURE, Phase.TAXIING)
        assert r.station_after == Station.GND

    def test_ready_hands_off_to_tower_118_175(self, fresh):
        fresh.process(self.STARTUP)
        r = fresh.process(self.READY)
        assert r.frequency_change is not None
        assert abs(r.frequency_change - 118.175) < 0.01

    def test_ready_mentions_tower_frequency(self, fresh):
        fresh.process(self.STARTUP)
        r = fresh.process(self.READY)
        assert _mentions_freq(r.text, 118.175)

    def test_ready_transitions_station_to_twr(self, fresh):
        fresh.process(self.STARTUP)
        r = fresh.process(self.READY)
        assert r.station_after == Station.TWR


# ------------------------------------------------------------------ #
# Phase 2 — EDDV Tower (118.175)

class TestPhase2_EDDVTower:
    INITIAL = (
        "Hannover Tower, D-EIYD, Cessna 172, holding point Alpha, runway 27L, "
        "VFR to Münster/Osnabrück, ready for departure."
    )
    LINEUP_READBACK = "Squawk 0472, line up and wait, runway 27L, D-EIYD."
    AIRBORNE       = "Heading 200, climbing 2500 feet, D-EIYD."
    CTR_EXIT       = "Squawk 7000, 120.150, D-EIYD."

    @pytest.fixture
    def at_tower(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.process(
            "Hannover Ground, D-EIYD, Cessna 172, GA Apron, "
            "information Charlie, request startup, VFR to Münster."
        )
        s.process("D-EIYD, ready at Alpha, 27L.")
        return s

    def test_initial_assigns_squawk(self, at_tower):
        r = at_tower.process(self.INITIAL)
        assert r.squawk is not None
        assert _is_valid_squawk(r.squawk)
        assert r.squawk != "7000"

    def test_initial_squawk_in_text(self, at_tower):
        r = at_tower.process(self.INITIAL)
        assert r.squawk in r.text

    def test_initial_gives_lineup_or_clearance(self, at_tower):
        r = at_tower.process(self.INITIAL)
        text = r.text.lower()
        assert ("line up" in text or "lined up" in text
                or "cleared for takeoff" in text
                or "cleared to take off" in text)

    def test_initial_station_stays_twr(self, at_tower):
        r = at_tower.process(self.INITIAL)
        assert r.station_after == Station.TWR

    def test_cleared_for_takeoff_includes_heading(self, at_tower):
        at_tower.process(self.INITIAL)
        r = at_tower.process(self.LINEUP_READBACK)
        text = r.text.lower()
        assert "cleared for takeoff" in text or "cleared to take off" in text
        # Initial turn ~200° — extract a heading token to avoid matching altitudes/QNH
        m = re.search(r'heading\s+(\d{3})', r.text, re.IGNORECASE)
        assert m is not None, "Expected a 'heading XXX' instruction in the clearance"
        assigned_heading = int(m.group(1))
        assert 185 <= assigned_heading <= 215, f"Heading {assigned_heading} not near 200°"

    def test_cleared_includes_altitude_restriction(self, at_tower):
        at_tower.process(self.INITIAL)
        r = at_tower.process(self.LINEUP_READBACK)
        assert "1500" in r.text or "not above" in r.text.lower()

    def test_airborne_gives_radar_contact(self, at_tower):
        at_tower.process(self.INITIAL)
        at_tower.process(self.LINEUP_READBACK)
        r = at_tower.process(self.AIRBORNE)
        assert "radar contact" in r.text.lower()

    def test_airborne_phase_is_departing_or_en_route(self, at_tower):
        at_tower.process(self.INITIAL)
        at_tower.process(self.LINEUP_READBACK)
        r = at_tower.process(self.AIRBORNE)
        assert r.phase_after in (Phase.DEPARTING, Phase.EN_ROUTE)

    def test_ctr_exit_assigns_squawk_7000(self, at_tower):
        at_tower.process(self.INITIAL)
        at_tower.process(self.LINEUP_READBACK)
        at_tower.process(self.AIRBORNE)
        r = at_tower.process("D-EIYD, approaching CTR boundary.")
        assert "7000" in r.text

    def test_ctr_exit_hands_off_to_radar_120_150(self, at_tower):
        at_tower.process(self.INITIAL)
        at_tower.process(self.LINEUP_READBACK)
        at_tower.process(self.AIRBORNE)
        at_tower.process("D-EIYD, approaching CTR boundary.")
        r = at_tower.process(self.CTR_EXIT)
        assert r.phase_after == Phase.EN_ROUTE
        assert r.station_after == Station.RADAR


# ------------------------------------------------------------------ #
# Phase 3 — Hannover Radar / Departure (120.150)

class TestPhase3_HannoverRadar:
    INITIAL   = "Hannover Radar, D-EIYD, Cessna 172, 2500 feet VFR, routing Münster/Osnabrück."
    FIS_RDBACK = "Langen Information, frequency, D-EIYD, goodbye."  # actual freq from r.frequency_change

    @pytest.fixture
    def at_radar(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        # Advance through Ground and Tower phases
        s.process("Hannover Ground, D-EIYD, request startup, VFR Münster.")
        s.process("D-EIYD, ready at Alpha, 27L.")
        s.process("Hannover Tower, D-EIYD, holding Alpha, 27L, ready.")
        s.process("Squawk, line up, D-EIYD.")
        s.process("Heading 200, 2500, D-EIYD.")
        s.process("D-EIYD, approaching CTR boundary.")
        s.process("Squawk 7000, 120.150, D-EIYD.")
        return s

    def test_initial_acknowledges_identification(self, at_radar):
        r = at_radar.process(self.INITIAL)
        text = r.text.lower()
        assert "identified" in text or "radar contact" in text

    def test_initial_station_is_radar(self, at_radar):
        r = at_radar.process(self.INITIAL)
        assert r.station_after == Station.RADAR

    def test_initial_hands_off_to_fis(self, at_radar):
        # Langen FIS frequency is sector-dependent — we only assert a freq is given.
        # 128.950 is typical but not guaranteed for this routing; don't hardcode it.
        r = at_radar.process(self.INITIAL)
        assert r.frequency_change is not None
        assert 118.0 <= r.frequency_change <= 136.0  # VHF COM band

    def test_goodbye_advances_to_en_route_fis(self, at_radar):
        at_radar.process(self.INITIAL)
        r = at_radar.process(self.FIS_RDBACK)
        assert r.phase_after == Phase.EN_ROUTE_FIS
        assert r.station_after == Station.FIS


# ------------------------------------------------------------------ #
# Phase 4 — Langen Information (128.950)

class TestPhase4_LangenInformation:
    CHECKIN = (
        "Langen Information, D-EIYD, Cessna 172, 2500 feet VFR, "
        "from Hannover, routing Münster/Osnabrück, requesting traffic information."
    )
    APPROACHING = (
        "D-EIYD, approaching Münster/Osnabrück control zone, "
        "request frequency change."
    )
    GOODBYE = "Good day, D-EIYD."

    @pytest.fixture
    def at_fis(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.EN_ROUTE_FIS
        s.current_station = Station.FIS
        return s

    def test_checkin_acknowledged(self, at_fis):
        r = at_fis.process(self.CHECKIN)
        assert "D-EIYD" in r.text

    def test_checkin_includes_qnh_or_traffic(self, at_fis):
        r = at_fis.process(self.CHECKIN)
        text = r.text.lower()
        assert "qnh" in text or "1018" in r.text or "traffic" in text

    def test_ctr_approach_approves_freq_change(self, at_fis):
        at_fis.process(self.CHECKIN)
        r = at_fis.process(self.APPROACHING)
        assert "approved" in r.text.lower() or "change" in r.text.lower()

    def test_goodbye_advances_to_approach_phase(self, at_fis):
        at_fis.process(self.CHECKIN)
        at_fis.process(self.APPROACHING)
        r = at_fis.process(self.GOODBYE)
        assert r.phase_after == Phase.APPROACH
        assert r.station_after == Station.APP


# ------------------------------------------------------------------ #
# Phase 5 — EDDG Approach (121.250)

class TestPhase5_EDDGApproach:
    INITIAL = (
        "Münster Approach, D-EIYD, Cessna 172, 2500 feet VFR, "
        "10 nautical miles east, inbound Tango, information Delta, "
        "request entry into control zone."
    )
    TANGO = "D-EIYD, reporting Tango, 1500 feet."
    TWR_RDBACK = "Right base runway 25, descending, 118.700, D-EIYD."

    @pytest.fixture
    def at_approach(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.APPROACH
        s.current_station = Station.APP
        s.current_airport = eddg
        return s

    def test_ctr_entry_assigns_new_squawk(self, at_approach):
        r = at_approach.process(self.INITIAL)
        assert r.squawk is not None
        assert _is_valid_squawk(r.squawk)

    def test_ctr_entry_clearance_text(self, at_approach):
        r = at_approach.process(self.INITIAL)
        text = r.text.lower()
        assert "cleared" in text
        assert "enter" in text or "control zone" in text or "ctr" in text

    def test_ctr_entry_altitude_restriction(self, at_approach):
        r = at_approach.process(self.INITIAL)
        assert "1500" in r.text or "not below" in r.text.lower()

    def test_ctr_entry_expects_runway_25(self, at_approach):
        r = at_approach.process(self.INITIAL)
        assert "25" in r.text

    def test_tango_report_joins_circuit(self, at_approach):
        at_approach.process(self.INITIAL)
        r = at_approach.process(self.TANGO)
        text = r.text.lower()
        assert any(w in text for w in ("base", "final", "join", "circuit", "downwind"))

    def test_tango_report_hands_off_to_tower_118_700(self, at_approach):
        at_approach.process(self.INITIAL)
        r = at_approach.process(self.TANGO)
        assert r.frequency_change is not None
        assert abs(r.frequency_change - 118.700) < 0.01

    def test_tower_readback_transitions_to_twr(self, at_approach):
        at_approach.process(self.INITIAL)
        at_approach.process(self.TANGO)
        r = at_approach.process(self.TWR_RDBACK)
        assert r.station_after == Station.TWR


# ------------------------------------------------------------------ #
# Phase 6 — EDDG Tower (118.700)

class TestPhase6_EDDGTower:
    INITIAL   = "Münster Tower, D-EIYD, Cessna 172, right base runway 25, 1000 feet."
    LANDED    = "D-EIYD, landed, vacating runway 25 to the left."
    VACATING  = "Vacating left, 121.800, D-EIYD."

    @pytest.fixture
    def at_eddg_tower(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.CIRCUIT
        s.current_station = Station.TWR
        s.current_airport = eddg
        return s

    def test_initial_clears_to_land(self, at_eddg_tower):
        r = at_eddg_tower.process(self.INITIAL)
        text = r.text.lower()
        assert "cleared to land" in text or "cleared for landing" in text

    def test_initial_mentions_runway_25(self, at_eddg_tower):
        r = at_eddg_tower.process(self.INITIAL)
        assert "25" in r.text

    def test_initial_gives_wind(self, at_eddg_tower):
        r = at_eddg_tower.process(self.INITIAL)
        # Wind is 250°/6 kt — match direction or speed with enough context to avoid false positives
        assert ("250" in r.text
                or re.search(r'\b6\s*knot', r.text, re.IGNORECASE)
                or re.search(r'wind\b.*\b6\b', r.text, re.IGNORECASE))

    def test_post_landing_instructs_vacate_and_ground(self, at_eddg_tower):
        at_eddg_tower.process(self.INITIAL)
        r = at_eddg_tower.process(self.LANDED)
        text = r.text.lower()
        assert "ground" in text or "121.800" in r.text

    def test_vacating_readback_transitions_to_ground_arrival(self, at_eddg_tower):
        at_eddg_tower.process(self.INITIAL)
        at_eddg_tower.process(self.LANDED)
        r = at_eddg_tower.process(self.VACATING)
        assert r.station_after == Station.GND
        assert r.phase_after == Phase.GROUND_ARRIVAL


# ------------------------------------------------------------------ #
# Phase 7 — EDDG Ground (121.800)

class TestPhase7_EDDGGround:
    INITIAL  = "Münster Ground, D-EIYD, vacated runway 25, request taxi to General Aviation."
    READBACK = "Via Bravo to General Aviation, QNH 1018, D-EIYD."

    @pytest.fixture
    def at_eddg_ground(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.GROUND_ARRIVAL
        s.current_station = Station.GND
        s.current_airport = eddg
        return s

    def test_taxi_instruction_given(self, at_eddg_ground):
        r = at_eddg_ground.process(self.INITIAL)
        assert "taxi" in r.text.lower() or "taxy" in r.text.lower()

    def test_destination_is_general_aviation(self, at_eddg_ground):
        r = at_eddg_ground.process(self.INITIAL)
        text = r.text.lower()
        assert "general aviation" in text or "apron" in text

    def test_qnh_given(self, at_eddg_ground):
        r = at_eddg_ground.process(self.INITIAL)
        assert r.qnh == 1018

    def test_callsign_in_response(self, at_eddg_ground):
        r = at_eddg_ground.process(self.INITIAL)
        assert "D-EIYD" in r.text

    def test_readback_completes_session(self, at_eddg_ground):
        at_eddg_ground.process(self.INITIAL)
        r = at_eddg_ground.process(self.READBACK)
        assert r.phase_after == Phase.PARKED

    def test_session_records_full_route(self, at_eddg_ground):
        at_eddg_ground.process(self.INITIAL)
        at_eddg_ground.process(self.READBACK)
        assert at_eddg_ground.departure.icao == "EDDV"
        assert at_eddg_ground.destination.icao == "EDDG"
        assert at_eddg_ground.phase == Phase.PARKED


# ------------------------------------------------------------------ #
# Squawk lifecycle (end-to-end)

class TestSquawkLifecycle:
    def test_squawk_none_before_tower(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        assert s.squawk is None

    def test_squawk_assigned_by_eddv_tower(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.process("Hannover Ground, D-EIYD, request startup.")
        s.process("D-EIYD, ready at Alpha, 27L.")
        s.process("Hannover Tower, D-EIYD, holding Alpha, 27L, ready.")
        assert s.squawk is not None
        assert _is_valid_squawk(s.squawk)
        assert s.squawk != "7000"

    def test_squawk_7000_issued_on_ctr_exit(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.DEPARTING
        s.current_station = Station.TWR
        s.process("D-EIYD, approaching CTR boundary.")
        assert "7000" in s.squawk_history

    def test_new_squawk_assigned_by_eddg_approach(self, eddv, eddg, c172, conditions):
        s = _make_session(eddv, eddg, c172, conditions)
        s.phase = Phase.APPROACH
        s.current_station = Station.APP
        s.current_airport = eddg
        r = s.process(
            "Münster Approach, D-EIYD, 2500 VFR, 10nm east, request entry."
        )
        assert r.squawk is not None
        assert _is_valid_squawk(r.squawk)
        # EDDG approach squawk must appear in history
        assert r.squawk in s.squawk_history


# ------------------------------------------------------------------ #
# Pilot call parsing — these exercise the existing atc.parser
# against callsigns and stations that appear in this flight.
# No xfail override here: these should pass today once the test file runs.

class TestPilotParsingAcrossAllPhases:
    """Parser must correctly identify station + callsign for each leg."""

    @pytest.mark.parametrize("text,expected_station,expected_callsign", [
        ("Hannover Ground, D-EIYD, request startup",          "GND", "D-EIYD"),
        ("Hannover Ground, D-EIYD, ready at holding point Alpha, runway 27L.", "GND", "D-EIYD"),
        ("Hannover Tower, D-EIYD, holding Alpha, ready.",     "TWR", "D-EIYD"),
        ("Münster Approach, D-EIYD, 2500 feet VFR.",          "APP", "D-EIYD"),
        ("Münster Tower, D-EIYD, right base runway 25.",      "TWR", "D-EIYD"),
        ("Münster Ground, D-EIYD, vacated runway 25.",        "GND", "D-EIYD"),
    ])
    def test_parse_each_leg(self, text, expected_station, expected_callsign):
        from atc.parser import parse
        c = parse(text)
        assert c.callsign == expected_callsign, f"callsign mismatch for: {text}"
        assert c.station == expected_station, f"station mismatch for: {text}"

    @pytest.mark.parametrize("text,expected_callsign", [
        ("Hannover Radar, D-EIYD, 2500 feet VFR.",               "D-EIYD"),
        ("Langen Information, D-EIYD, 2500 feet VFR, from Hannover.", "D-EIYD"),
    ])
    def test_en_route_callsign_parsed(self, text, expected_callsign):
        """En-route stations (Radar, Information) — callsign must be found
        even if station mapping is approximate or None."""
        from atc.parser import parse
        c = parse(text)
        assert c.callsign == expected_callsign
