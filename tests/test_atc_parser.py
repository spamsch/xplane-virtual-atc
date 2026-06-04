import pytest
from atc.parser import parse, format_station


class TestParse:
    def test_bare_callsign_european(self):
        c = parse("Hanover Ground D-EIYD")
        assert c.station == "GND"
        assert c.callsign == "D-EIYD"
        assert c.airport_name == "Hanover"
        assert c.message is None

    def test_bare_callsign_us(self):
        c = parse("Nashville Ground N12345")
        assert c.station == "GND"
        assert c.callsign == "N12345"
        assert c.airport_name == "Nashville"

    def test_with_message(self):
        c = parse("Hannover Ground, D-EIYD, request startup")
        assert c.station == "GND"
        assert c.callsign == "D-EIYD"
        assert c.message == "request startup"

    def test_full_call(self):
        c = parse("Hannover Ground, D-EIYD, Cessna 172, at the apron, request startup and taxi information")
        assert c.station == "GND"
        assert c.callsign == "D-EIYD"
        assert "startup and taxi information" in c.message

    def test_tower_station(self):
        c = parse("Hannover Tower D-EIYD ready runway 27")
        assert c.station == "TWR"
        assert c.callsign == "D-EIYD"

    def test_approach_station(self):
        c = parse("Hannover Approach DLH123 descending")
        assert c.station == "APP"
        assert c.callsign == "DLH123"

    def test_no_callsign(self):
        c = parse("Hannover Ground, request startup")
        assert c.station == "GND"
        assert c.callsign is None

    def test_raw_preserved(self):
        raw = "Hanover Ground D-EIYD"
        c = parse(raw)
        assert c.raw == raw

    def test_callsign_with_numbers(self):
        c = parse("Frankfurt Tower BAW456 ready")
        assert c.callsign == "BAW456"

    def test_departure_station(self):
        c = parse("Hannover Departure D-EIYD airborne")
        assert c.station == "DEP"

    def test_clearance_station(self):
        c = parse("Berlin Clearance D-ABCD IFR to Munich")
        assert c.station == "CLD"

    def test_approaching_does_not_match_approach(self):
        # "approaching" contains "approach" but must NOT be parsed as APP station
        c = parse("D-EIYD, approaching CTR boundary, request frequency change.")
        assert c.station != "APP"

    def test_departure_word_in_callsign_context(self):
        # "Departure" as a station word should still match when standing alone
        c = parse("Hannover Departure D-EIYD checking in")
        assert c.station == "DEP"

    def test_information_is_fis(self):
        c = parse("Langen Information D-EIYD, VFR, request basic service")
        assert c.station == "FIS"
        assert c.callsign == "D-EIYD"

    def test_info_abbreviation_is_fis(self):
        assert parse("Bremen Info D-EIYD").station == "FIS"

    def test_fis_keyword_is_fis(self):
        assert parse("Munich FIS D-EIYD").station == "FIS"

    def test_radar_station(self):
        c = parse("Hannover Radar D-EIYD, request traffic service")
        assert c.station == "RADAR"

    def test_information_in_message_does_not_override_ground(self):
        # A Ground call that merely mentions "information" must stay GND — the
        # controlling station keyword wins because it's matched first.
        c = parse("Hannover Ground, D-EIYD, request startup and taxi information")
        assert c.station == "GND"


class TestFormatStation:
    def test_ground(self):
        assert format_station("Hannover", "GND") == "Hannover Ground"

    def test_tower(self):
        assert format_station("Frankfurt", "TWR") == "Frankfurt Tower"

    def test_approach(self):
        assert format_station("Munich", "APP") == "Munich Approach"

    def test_unknown_station(self):
        result = format_station("Berlin", "XYZ")
        assert "Berlin" in result
        assert "XYZ" in result
