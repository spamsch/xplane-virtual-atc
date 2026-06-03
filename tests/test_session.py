"""
Tests for ATCSession — phase/station tracking, squawk lifecycle,
response parsing, and conditions handling.

These tests mock the LLM call so they run without a running Claude process.
"""

import re
from unittest.mock import patch
import pytest

import config
from airport.parser import Airport, Frequency, Runway
from aircraft.database import AircraftPerf
from atc.session import (
    ATCSession, Phase, Station, ATCResponse,
    _parse_response, _generate_squawk,
)


def _is_valid_squawk(code: str) -> bool:
    return bool(re.fullmatch(r'[0-7]{4}', str(code)))


# ------------------------------------------------------------------ #
# Fixtures

@pytest.fixture
def eddv() -> Airport:
    ap = Airport(icao="EDDV", name="Hannover", elevation_ft=183,
                 lat=52.461, lon=9.685)
    ap.frequencies = [
        Frequency(53, "Ground",    121.900, "Hannover Ground"),
        Frequency(54, "Tower",     118.175, "Hannover Tower"),
        Frequency(50, "ATIS",      126.850, "Hannover Information"),
        Frequency(55, "Approach",  120.800, "Hannover Approach"),
        Frequency(56, "Departure", 120.150, "Hannover Radar"),
    ]
    ap.runways = [Runway("27L", "09R", 52.461, 9.673, 52.461, 9.703, 45.0, 0.0, 0.0)]
    return ap


@pytest.fixture
def eddg() -> Airport:
    ap = Airport(icao="EDDG", name="Münster/Osnabrück", elevation_ft=160,
                 lat=52.134, lon=7.685)
    ap.frequencies = [
        Frequency(53, "Ground",   121.800, "Münster Ground"),
        Frequency(54, "Tower",    118.700, "Münster Tower"),
        Frequency(55, "Approach", 121.250, "Münster Approach"),
    ]
    ap.runways = [Runway("07", "25", 52.134, 7.670, 52.134, 7.705, 45.0, 0.0, 0.0)]
    return ap


@pytest.fixture
def c172() -> AircraftPerf:
    import aircraft.database as db
    db.load()
    return db.lookup("C172")


@pytest.fixture
def conditions(eddv, eddg):
    return {
        "EDDV": {"qnh": 1018, "wind_dir": 270, "wind_kts": 8,
                 "visibility_km": 10, "atis": "Charlie", "active_runway": "27L"},
        "EDDG": {"qnh": 1018, "wind_dir": 250, "wind_kts": 6,
                 "visibility_km": 15, "atis": "Delta",   "active_runway": "25"},
    }


def make_session(eddv, eddg, c172, conditions) -> ATCSession:
    return ATCSession(departure=eddv, destination=eddg,
                      aircraft=c172, callsign="D-EIYD",
                      conditions=conditions)


def mock_respond(text: str):
    """Patch atc.engine.respond to return a canned response without calling Claude."""
    return patch("atc.session.atc_engine.respond", return_value=text)


# ------------------------------------------------------------------ #
# _parse_response unit tests (no LLM needed)

class TestParseResponse:
    def test_squawk_extracted(self):
        r = _parse_response("D-EIYD, squawk 0472, line up and wait.")
        assert r['squawk'] == '0472'

    def test_squawk_case_insensitive(self):
        r = _parse_response("SQUAWK 1234")
        assert r['squawk'] == '1234'

    def test_squawk_7000(self):
        r = _parse_response("Squawk 7000, contact Hannover Radar 120.150.")
        assert r['squawk'] == '7000'

    def test_frequency_extracted(self):
        r = _parse_response("Contact Tower 118.175.")
        assert r.get('frequency_change') == pytest.approx(118.175)

    def test_frequency_extracted_two_decimals(self):
        r = _parse_response("Contact Ground 121.90.")
        assert r.get('frequency_change') == pytest.approx(121.90)

    def test_qnh_extracted(self):
        r = _parse_response("QNH 1018, runway 27L.")
        assert r['qnh'] == 1018

    def test_runway_extracted(self):
        r = _parse_response("Taxi to holding point Alpha, runway 27L.")
        assert r['runway'] == '27L'

    def test_takeoff_trigger(self):
        r = _parse_response("D-EIYD, cleared for takeoff runway 27L.")
        assert r.get('trigger') == 'takeoff'

    def test_takeoff_trigger_alternative_phrasing(self):
        r = _parse_response("Cleared to take off runway 27L.")
        assert r.get('trigger') == 'takeoff'

    def test_land_trigger(self):
        r = _parse_response("D-EIYD, cleared to land runway 25.")
        assert r.get('trigger') == 'land'

    def test_radar_contact_trigger(self):
        r = _parse_response("D-EIYD, radar contact, continue heading 200.")
        assert r.get('trigger') == 'radar_contact'

    def test_ctr_exit_trigger_squawk_7000(self):
        r = _parse_response("Squawk 7000, frequency change approved.")
        assert r.get('trigger') == 'ctr_exit'

    def test_no_false_positives_in_plain_text(self):
        r = _parse_response("D-EIYD, startup approved, QNH 1018, taxi via Alpha.")
        assert r.get('trigger') is None
        assert r.get('squawk') is None
        assert r['qnh'] == 1018

    def test_multiple_fields_in_one_response(self):
        r = _parse_response(
            "D-EIYD, squawk 0472, wind 270 degrees 8 knots, runway 27L, "
            "line up and wait, QNH 1018."
        )
        assert r['squawk'] == '0472'
        assert r['runway'] == '27L'
        assert r['qnh'] == 1018


# ------------------------------------------------------------------ #
# Squawk generation

class TestGenerateSquawk:
    def test_format_is_4_digits(self):
        for _ in range(20):
            assert _is_valid_squawk(_generate_squawk())

    def test_no_reserved_codes(self):
        reserved = {'7500', '7600', '7700', '7000', '2000', '0000', '1200'}
        for _ in range(100):
            sq = _generate_squawk()
            assert sq not in reserved

    def test_no_7xxx(self):
        for _ in range(100):
            assert not _generate_squawk().startswith('7')


# ------------------------------------------------------------------ #
# ATCSession initialisation

class TestSessionInit:
    def test_initial_phase(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.phase == Phase.PRE_DEPARTURE

    def test_initial_station(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.current_station == Station.GND

    def test_initial_squawk_none(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.squawk is None

    def test_squawk_history_empty(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.squawk_history == []

    def test_departure_airport_stored(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.departure.icao == "EDDV"

    def test_destination_airport_stored(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s.destination.icao == "EDDG"


# ------------------------------------------------------------------ #
# ATC callsign derivation

class TestATCCallsign:
    def test_ground_callsign(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s._atc_callsign() == "Hannover Ground"

    def test_tower_callsign(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        assert s._atc_callsign() == "Hannover Tower"

    def test_approach_callsign_at_destination(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_airport = eddg
        s.current_station = Station.APP
        assert s._atc_callsign() == "Münster/Osnabrück Approach"


# ------------------------------------------------------------------ #
# Flat conditions

class TestFlatConditions:
    def test_qnh_present(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        c = s._flat_conditions()
        assert c['qnh'] == '1018'

    def test_wind_formatted(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        c = s._flat_conditions()
        assert '270' in c['wind']
        assert '8' in c['wind']

    def test_active_runway_present(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        c = s._flat_conditions()
        assert c['active_runway'] == '27L'

    def test_switches_when_airport_changes(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_airport = eddg
        c = s._flat_conditions()
        assert c['active_runway'] == '25'
        assert '250' in c['wind']


# ------------------------------------------------------------------ #
# process() — phase and station transitions (LLM mocked)

class TestProcess:
    def test_returns_atc_response(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, startup approved, QNH 1018, taxi via Alpha, hold short 27L."):
            r = s.process("Hannover Ground, D-EIYD, request startup.")
        assert isinstance(r, ATCResponse)
        assert r.text

    def test_phase_advances_to_taxiing_on_taxi_clearance(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Startup approved, taxi to holding point Alpha, hold short 27L."):
            r = s.process("Hannover Ground, D-EIYD, request startup.")
        assert r.phase_after == Phase.TAXIING

    def test_qnh_extracted_from_ground_response(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Startup approved, QNH 1018, taxi via Alpha, hold short 27L."):
            r = s.process("Hannover Ground, D-EIYD, request startup.")
        assert r.qnh == 1018

    def test_runway_extracted_from_ground_response(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Taxi via Alpha, hold short runway 27L, QNH 1018."):
            r = s.process("Hannover Ground, D-EIYD, request startup.")
        assert r.runway == '27L'

    def test_station_switches_when_pilot_calls_tower(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, Hannover Tower, squawk 0472, wind 270/8, line up and wait runway 27L."):
            r = s.process("Hannover Tower, D-EIYD, holding point Alpha, runway 27L, ready.")
        assert r.station_after == Station.TWR

    def test_phase_advances_to_ground_departure_on_tower(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Squawk 0472, line up and wait runway 27L."):
            r = s.process("Hannover Tower, D-EIYD, holding Alpha, ready.")
        assert r.phase_after == Phase.GROUND_DEPARTURE

    def test_takeoff_clearance_advances_phase(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.GROUND_DEPARTURE
        s.squawk = '0472'
        with mock_respond("D-EIYD, cleared for takeoff runway 27L, after departure left heading 200."):
            r = s.process("Ready for departure runway 27L, D-EIYD.")
        assert r.phase_after == Phase.DEPARTING

    def test_radar_contact_advances_phase(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        with mock_respond("D-EIYD, radar contact, continue heading 200, climb 2500 feet."):
            r = s.process("Heading 200, climbing 2500, D-EIYD.")
        assert r.phase_after == Phase.EN_ROUTE

    def test_frequency_handoff_switches_station(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, contact Tower 118.175."):
            r = s.process("D-EIYD, ready at holding point Alpha.")
        assert r.frequency_change == pytest.approx(118.175)
        assert r.station_after == Station.TWR


class TestHandoffFrequencyGate:
    """With live COM1, a handed-off station only answers once COM1 is tuned."""

    def test_handoff_deferred_until_tuned(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        # Ground hands off to Tower 118.175; COM1 still on Ground (121.900).
        with mock_respond("D-EIYD, contact Tower 118.175."):
            r = s.process("D-EIYD, ready at Alpha.", com1_mhz=121.900)
        assert r.station_after == Station.GND          # not switched yet
        assert s._pending_handoff[0] == Station.TWR

        # Pilot calls Tower but hasn't retuned → no reply, a nudge instead.
        with mock_respond("(should not be called)"):
            r = s.process("Hannover Tower, D-EIYD, ready.", com1_mhz=121.900)
        assert r.on_wrong_frequency is True
        assert r.expected_frequency == pytest.approx(118.175)
        assert r.text == ""
        assert s.current_station == Station.GND

    def test_handoff_completes_after_tuning(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, contact Tower 118.175."):
            s.process("D-EIYD, ready at Alpha.", com1_mhz=121.900)
        # Now tuned to Tower → it answers and the station switches.
        with mock_respond("D-EIYD, Hannover Tower, hold short runway 27L."):
            r = s.process("Hannover Tower, D-EIYD, ready.", com1_mhz=118.175)
        assert r.on_wrong_frequency is False
        assert r.station_after == Station.TWR
        assert s._pending_handoff is None

    def test_tolerance_allows_833_rounding(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, contact Tower 118.175."):
            s.process("D-EIYD, ready.", com1_mhz=121.900)
        with mock_respond("D-EIYD, Hannover Tower, pass your message."):
            r = s.process("Hannover Tower, D-EIYD.", com1_mhz=118.180)  # 5 kHz off
        assert r.station_after == Station.TWR

    def test_no_com1_keeps_legacy_immediate_switch(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("D-EIYD, contact Tower 118.175."):
            r = s.process("D-EIYD, ready at Alpha.")   # com1 unknown
        assert r.station_after == Station.TWR          # switches immediately
        assert s._pending_handoff is None

    def test_history_grows_with_each_call(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Startup approved."):
            s.process("Request startup.")
        with mock_respond("Contact Tower 118.175."):
            s.process("Ready at Alpha.")
        assert len(s._history) == 2


# ------------------------------------------------------------------ #
# Squawk lifecycle via process()

class TestSquawkLifecycle:
    def test_squawk_assigned_on_first_tower_call(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.PRE_DEPARTURE
        with mock_respond("D-EIYD, squawk 0472, line up and wait."):
            r = s.process("Hannover Tower, D-EIYD, ready.")
        assert r.squawk is not None
        assert _is_valid_squawk(r.squawk)
        assert r.squawk != '7000'

    def test_squawk_stored_in_session(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.PRE_DEPARTURE
        with mock_respond("D-EIYD, squawk 0472, line up and wait."):
            s.process("Hannover Tower, D-EIYD, ready.")
        assert s.squawk is not None
        assert _is_valid_squawk(s.squawk)

    def test_squawk_in_history(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.PRE_DEPARTURE
        with mock_respond("Squawk 0472, line up."):
            s.process("Hannover Tower, D-EIYD, ready.")
        assert len(s.squawk_history) == 1

    def test_7000_added_to_history_on_ctr_exit(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        s.squawk = '0472'
        s.squawk_history = ['0472']
        with mock_respond("D-EIYD, squawk 7000, contact Hannover Radar 120.150, frequency change approved."):
            s.process("D-EIYD, approaching CTR boundary.")
        assert '7000' in s.squawk_history

    def test_squawk_not_reassigned_on_second_tower_call(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.GROUND_DEPARTURE
        s.squawk = '0472'
        s.squawk_history = ['0472']
        with mock_respond("D-EIYD, cleared for takeoff runway 27L."):
            r = s.process("Lineup and wait, D-EIYD.")
        # squawk should not be in this response (already assigned)
        assert r.squawk is None

    def test_squawk_none_before_tower(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with mock_respond("Startup approved, QNH 1018."):
            r = s.process("Hannover Ground, D-EIYD, request startup.")
        assert r.squawk is None
        assert s.squawk is None


# ------------------------------------------------------------------ #
# Station-from-frequency lookup

class TestStationFromFreq:
    def test_tower_freq_maps_to_twr(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s._station_from_freq(118.175) == Station.TWR

    def test_ground_freq_maps_to_gnd(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s._station_from_freq(121.900) == Station.GND

    def test_approach_freq_maps_to_app(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s._station_from_freq(120.800) == Station.APP

    def test_departure_freq_maps_to_radar(self, eddv, eddg, c172, conditions):
        # Code 56 (Departure) → RADAR (German airspace convention)
        s = make_session(eddv, eddg, c172, conditions)
        assert s._station_from_freq(120.150) == Station.RADAR

    def test_unknown_freq_returns_none(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        assert s._station_from_freq(999.999) is None

    def test_destination_freq_reachable(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        # EDDG tower 118.700 should be found via destination airport
        assert s._station_from_freq(118.700) == Station.TWR

    def test_departure_freq_maps_to_radar(self, eddv, eddg, c172, conditions):
        # Code 56 (Departure) maps to RADAR for German airspace convention
        from atc.session import _FREQ_TYPE_TO_STATION, Station
        assert _FREQ_TYPE_TO_STATION[56] == Station.RADAR


# ------------------------------------------------------------------ #
# extra_instructions are passed to the engine

class TestExtraInstructions:
    def test_squawk_instruction_sent_to_engine(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.PRE_DEPARTURE
        with patch("atc.session.atc_engine.respond", return_value="Squawk 0472, line up.") as mock:
            s.process("Hannover Tower, D-EIYD, ready.")
        call_kwargs = mock.call_args.kwargs
        extra = call_kwargs.get("extra_instructions", "")
        assert extra is not None
        assert "squawk" in extra.lower() or "Assign" in extra

    def test_ctr_exit_instruction_sent(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        s.squawk = '0472'
        with patch("atc.session.atc_engine.respond", return_value="Squawk 7000, contact Radar.") as mock:
            s.process("D-EIYD, approaching CTR, request frequency change.")
        call_kwargs = mock.call_args.kwargs
        extra = call_kwargs.get("extra_instructions", "") or ""
        assert "7000" in extra or "CTR" in extra

    def test_no_taxi_layout_without_taxi_request(self, eddv, eddg, c172, conditions):
        # A startup request (or bare contact) must NOT receive a taxi clearance —
        # the controller should not pre-empt a request the pilot didn't make.
        s = make_session(eddv, eddg, c172, conditions)
        with patch("atc.session.atc_engine.respond", return_value="Startup approved.") as mock:
            s.process("Hannover Ground, D-EIYD, request startup.")
        extra = mock.call_args.kwargs.get("extra_instructions") or ""
        assert "taxi route" not in extra.lower()

    def test_taxi_layout_injected_on_taxi_request(self, eddv, eddg, c172, conditions):
        # When taxi IS requested, the controller gets the airport's runway/taxi
        # context so it can issue a real clearance (here, no position → the
        # "do not invent" fallback, which still names the airport + runway).
        s = make_session(eddv, eddg, c172, conditions)
        with patch("atc.session.atc_engine.respond", return_value="Taxi approved.") as mock:
            s.process("Hannover Ground, D-EIYD, request taxi.")
        extra = mock.call_args.kwargs.get("extra_instructions") or ""
        assert "EDDV" in extra
        assert "27L" in extra   # active_runway from conditions fixture


# ------------------------------------------------------------------ #
# Model selection (Opus vs Sonnet)

class TestModelSelection:
    def test_first_call_uses_opus(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with patch("atc.session.atc_engine.respond", return_value="Startup approved.") as mock:
            s.process("Hannover Ground, D-EIYD, request startup.")
        assert mock.call_args.kwargs["model"] == config.MODEL_BOUNDARY

    def test_second_call_same_station_uses_sonnet(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        with patch("atc.session.atc_engine.respond", return_value="Startup approved."):
            s.process("Hannover Ground, D-EIYD, request startup.")
        with patch("atc.session.atc_engine.respond", return_value="Contact Tower 118.175.") as mock:
            s.process("D-EIYD, ready at Alpha.")
        assert mock.call_args.kwargs["model"] == config.MODEL_ROUTINE

    def test_first_call_on_new_station_uses_opus(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        # Exhaust Ground (mark it seen)
        with patch("atc.session.atc_engine.respond", return_value="Contact Tower 118.175."):
            s.process("Hannover Ground, D-EIYD, request startup.")
        # First Tower call should use Opus
        with patch("atc.session.atc_engine.respond", return_value="Squawk 0472, line up.") as mock:
            s.process("Hannover Tower, D-EIYD, holding Alpha, ready.")
        assert mock.call_args.kwargs["model"] == config.MODEL_BOUNDARY


# ------------------------------------------------------------------ #
# _should_issue_ctr_exit false-positive guard

class TestCtrExitTrigger:
    def test_approaching_ctr_triggers(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        assert s._should_issue_ctr_exit("D-EIYD, approaching CTR boundary.")

    def test_leaving_the_zone_triggers(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        assert s._should_issue_ctr_exit("D-EIYD, leaving the zone.")

    def test_leaving_altitude_does_not_trigger(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        # "leaving" alone was removed — must be "leaving the zone" or "leaving controlled"
        assert not s._should_issue_ctr_exit("D-EIYD, leaving 1500 for 2500.")

    def test_approaching_holding_does_not_trigger(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.TWR
        s.phase = Phase.DEPARTING
        assert not s._should_issue_ctr_exit("D-EIYD, approaching the holding point.")

    def test_does_not_trigger_on_ground(self, eddv, eddg, c172, conditions):
        s = make_session(eddv, eddg, c172, conditions)
        s.current_station = Station.GND
        s.phase = Phase.PRE_DEPARTURE
        assert not s._should_issue_ctr_exit("D-EIYD, approaching CTR.")
