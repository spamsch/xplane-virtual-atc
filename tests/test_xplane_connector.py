"""
Tests for the X-Plane RREF connector.

No UDP socket or running X-Plane needed — we test packet construction
and state update logic directly.
"""
import struct
import pytest
from xplane.connector import XPlaneConnector, FlightState, _IDX, _IDX_ICAO_BASE, _IDX_TAIL_BASE


@pytest.fixture
def connector():
    return XPlaneConnector()


# ------------------------------------------------------------------ #
# RREF packet construction

class TestRREFPacket:
    def test_packet_length(self, connector):
        pkt = connector._make_rref_packet(5, 0, "sim/flightmodel/position/latitude")
        assert len(pkt) == 413   # 5 header + 4 freq + 4 index + 400 path

    def test_header(self, connector):
        pkt = connector._make_rref_packet(5, 0, "sim/flightmodel/position/latitude")
        assert pkt[:5] == b'RREF\x00'

    def test_frequency_encoded(self, connector):
        pkt = connector._make_rref_packet(10, 0, "sim/time/paused")
        freq = struct.unpack_from('<i', pkt, 5)[0]
        assert freq == 10

    def test_index_encoded(self, connector):
        pkt = connector._make_rref_packet(5, 42, "sim/time/paused")
        idx = struct.unpack_from('<i', pkt, 9)[0]
        assert idx == 42

    def test_path_null_terminated(self, connector):
        path = "sim/time/paused"
        pkt = connector._make_rref_packet(1, 0, path)
        path_bytes = pkt[13:]
        assert path_bytes[:len(path)] == path.encode('ascii')
        assert path_bytes[len(path)] == 0

    def test_path_padded_to_400_bytes(self, connector):
        pkt = connector._make_rref_packet(1, 0, "sim/time/paused")
        assert len(pkt[13:]) == 400

    def test_unsubscribe_uses_zero_freq(self, connector):
        pkt = connector._make_rref_packet(0, 7, "sim/time/paused")
        freq = struct.unpack_from('<i', pkt, 5)[0]
        assert freq == 0


# ------------------------------------------------------------------ #
# FlightState properties

class TestFlightState:
    def test_gs_kts_conversion(self):
        s = FlightState(groundspeed_ms=51.44)  # 100 knots in m/s
        assert s.gs_kts == pytest.approx(100.0, rel=0.01)

    def test_com1_mhz_conversion(self):
        s = FlightState(com1_raw=12175.0)
        assert s.com1_mhz == pytest.approx(121.75, abs=0.001)

    def test_com2_mhz_conversion(self):
        s = FlightState(com2_raw=11865.0)
        assert s.com2_mhz == pytest.approx(118.65, abs=0.001)

    def test_acf_icao_from_chars(self):
        s = FlightState()
        s._icao_chars = [67.0, 49.0, 55.0, 50.0]   # 'C', '1', '7', '2'
        assert s.acf_icao == "C172"

    def test_acf_icao_strips_nulls(self):
        s = FlightState()
        s._icao_chars = [67.0, 49.0, 55.0, 0.0]   # "C17\x00"
        assert s.acf_icao == "C17"

    def test_tail_number_from_chars(self):
        s = FlightState()
        # 'D', '-', 'E', 'I', 'Y', 'D', then zeros
        s._tail_chars = [68.0, 45.0, 69.0, 73.0, 89.0, 68.0, 0.0, 0.0, 0.0, 0.0]
        assert s.tail_number == "D-EIYD"

    def test_is_flight_loaded_false_at_origin(self):
        s = FlightState(lat=0.0, lon=0.0)
        assert not s.is_flight_loaded

    def test_is_flight_loaded_true_with_position(self):
        s = FlightState(lat=52.461, lon=9.685)
        assert s.is_flight_loaded

    def test_is_flight_loaded_true_with_lon_only(self):
        # Non-zero lon counts even if lat is 0 (equatorial airports)
        s = FlightState(lat=0.0, lon=9.685)
        assert s.is_flight_loaded


# ------------------------------------------------------------------ #
# State update dispatch

class TestStateApply:
    def test_apply_lat(self, connector):
        connector._apply(_IDX['lat'], 52.461)
        assert connector._state.lat == pytest.approx(52.461)

    def test_apply_lon(self, connector):
        connector._apply(_IDX['lon'], 9.685)
        assert connector._state.lon == pytest.approx(9.685)

    def test_apply_on_ground(self, connector):
        connector._apply(_IDX['on_ground'], 1.0)
        assert connector._state.on_ground == 1.0

    def test_apply_paused(self, connector):
        connector._apply(_IDX['paused'], 1.0)
        assert connector._state.paused == 1.0

    def test_apply_icao_char(self, connector):
        connector._apply(_IDX_ICAO_BASE + 0, 67.0)  # 'C'
        connector._apply(_IDX_ICAO_BASE + 1, 49.0)  # '1'
        connector._apply(_IDX_ICAO_BASE + 2, 55.0)  # '7'
        connector._apply(_IDX_ICAO_BASE + 3, 50.0)  # '2'
        assert connector._state.acf_icao == "C172"

    def test_apply_tail_char(self, connector):
        connector._apply(_IDX_TAIL_BASE + 0, 68.0)  # 'D'
        connector._apply(_IDX_TAIL_BASE + 1, 45.0)  # '-'
        connector._apply(_IDX_TAIL_BASE + 2, 65.0)  # 'A'
        assert connector._state._tail_chars[0] == 68.0
        assert connector._state._tail_chars[1] == 45.0

    def test_unknown_index_is_ignored(self, connector):
        connector._apply(999, 99.0)  # should not raise

    def test_state_returns_deepcopy(self, connector):
        connector._apply(_IDX['lat'], 52.0)
        s = connector.state
        s.lat = 0.0
        assert connector._state.lat == 52.0  # original unchanged
