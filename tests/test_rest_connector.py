"""
Tests for the X-Plane REST connector.

No running X-Plane needed — all HTTP calls are mocked via unittest.mock.patch.
"""

import json
import threading
import time
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from xplane.rest_connector import (
    XPlaneRestConnector,
    _decode_string,
    _extract_data,
    _extract_datarefs,
    _str_to_chars,
    encode_fixed_string,
)


# ------------------------------------------------------------------ #
# Helpers

def _response(body: dict) -> MagicMock:
    """Fake urlopen context-manager that returns a fresh BytesIO on each entry.

    Using side_effect instead of return_value ensures the BytesIO cursor is
    reset on every 'with urlopen(...) as resp:' block, even when the same
    mock is returned across multiple _http_get calls.
    """
    encoded = json.dumps(body).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(side_effect=lambda *_: BytesIO(encoded))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ------------------------------------------------------------------ #
# Unit helpers

class TestDecodeString:
    def test_plain_ascii(self):
        assert _decode_string("C172") == "C172"

    def test_base64_bytes(self):
        import base64
        encoded = base64.b64encode(b"D-EIYD\x00\x00\x00\x00").decode()
        assert _decode_string(encoded) == "D-EIYD"

    def test_char_array(self):
        # [67, 49, 55, 50] → "C172"
        assert _decode_string([67.0, 49.0, 55.0, 50.0]) == "C172"

    def test_control_chars_stripped(self):
        assert _decode_string("C172\x00\x00") == "C172"


class TestExtractDatarefs:
    def test_list_response(self):
        # Filter/list responses: {"data": [{id, name, value_type}, ...]}
        data = {"data": [{"id": 1, "name": "sim/...", "value_type": "float"}]}
        refs = _extract_datarefs(data)
        assert refs == [{"id": 1, "name": "sim/...", "value_type": "float"}]

    def test_scalar_data_returns_empty(self):
        # Value-endpoint responses return a scalar in "data", not a list
        assert _extract_datarefs({"data": 52.461}) == []

    def test_empty_on_error_response(self):
        assert _extract_datarefs({"error_code": "not_found", "error_message": "x"}) == []

    def test_extract_data_scalar(self):
        assert _extract_data({"data": 52.461}) == pytest.approx(52.461)

    def test_extract_data_missing(self):
        assert _extract_data({"error_code": "not_found"}) is None


class TestStrToChars:
    def test_fills_to_length(self):
        chars = _str_to_chars("AB", 4)
        assert len(chars) == 4
        assert chars[0] == 65.0   # 'A'
        assert chars[1] == 66.0   # 'B'
        assert chars[2] == 0.0
        assert chars[3] == 0.0

    def test_truncates_long_string(self):
        chars = _str_to_chars("ABCDE", 4)
        assert len(chars) == 4
        assert chars[3] == 68.0   # 'D'


# ------------------------------------------------------------------ #
# Connector state: probe → discover → poll

class TestConnectorProbeAndDiscover:
    def _make_connector(self):
        return XPlaneRestConnector(host='127.0.0.1', port=8086)

    @patch('xplane.rest_connector.urlopen')
    def test_probe_detects_capabilities_response(self, mock_open):
        # /api/capabilities response (X-Plane 12.1.4+)
        mock_open.return_value = _response({
            "api": {"versions": ["v1", "v2", "v3"]},
            "x-plane": {"version": "12.4.0"},
        })
        c = self._make_connector()
        c._probe()
        assert c._connected

    @patch('xplane.rest_connector.urlopen')
    def test_probe_falls_back_to_v1_count(self, mock_open):
        # First call (capabilities) returns an error; second call (count) succeeds.
        responses = iter([
            _response({"error_code": "not_found"}),   # capabilities fails
            _response({"data": 9554}),                 # datarefs/count succeeds
        ])
        mock_open.side_effect = lambda *a, **kw: next(responses)
        c = self._make_connector()
        c._probe()
        assert c._connected

    @patch('xplane.rest_connector.urlopen')
    def test_probe_ignores_empty_response(self, mock_open):
        # Neither "x-plane" nor "data" key → not connected
        mock_open.return_value = _response({})
        c = self._make_connector()
        c._probe()
        assert not c._connected

    @patch('xplane.rest_connector.urlopen')
    def test_probe_ignores_connection_error(self, mock_open):
        from urllib.error import URLError
        mock_open.side_effect = URLError("connection refused")
        c = self._make_connector()
        c._probe()
        assert not c._connected

    @patch('xplane.rest_connector.urlopen')
    def test_on_connected_callback_fired(self, mock_open):
        mock_open.return_value = _response({"x-plane": {"version": "12.4.0"}})
        fired = []
        c = XPlaneRestConnector(on_connected=lambda: fired.append(True))
        c._probe()
        assert fired == [True]

    @patch('xplane.rest_connector.urlopen')
    def test_discover_caches_ids(self, mock_open):
        # /api/v3/datarefs?filter[name]=... → {"data": [{id, name, value_type}]}
        mock_open.return_value = _response({
            "data": [{"id": 42, "name": "sim/flightmodel/position/latitude",
                      "value_type": "float"}]
        })
        c = self._make_connector()
        c._discover()
        assert c._ids.get("sim/flightmodel/position/latitude") == 42

    @patch('xplane.rest_connector.urlopen')
    def test_discover_skips_wrong_name(self, mock_open):
        mock_open.return_value = _response({
            "data": [{"id": 99, "name": "sim/other/dataref", "value_type": "float"}]
        })
        c = self._make_connector()
        c._discover()
        assert "sim/flightmodel/position/latitude" not in c._ids

    @patch('xplane.rest_connector.urlopen')
    def test_discover_empty_then_retry_resolves(self, mock_open):
        # Discovery at the menu resolves nothing (0/18); once the flight loads,
        # _retry_dataref_discovery must pick the datarefs up. This is the bug:
        # without the retry, position stays 0 and airport detection is wrong.
        lat = "sim/flightmodel/position/latitude"
        mock_open.return_value = _response({"data": []})   # nothing registered yet
        c = self._make_connector()
        c._discover()
        assert c._ids == {}

        # Flight loads: the same query now returns ids. Retry (timer is 0 → fires).
        mock_open.return_value = _response({
            "data": [{"id": 7, "name": lat, "value_type": "double"}]
        })
        c._retry_dataref_discovery()
        assert c._ids.get(lat) == 7

    @patch('xplane.rest_connector.urlopen')
    def test_retry_dataref_discovery_throttled(self, mock_open):
        # A second call before the 5 s window must not re-query.
        mock_open.return_value = _response({"data": []})
        c = self._make_connector()
        c._discover()
        c._retry_dataref_discovery()           # fires (timer was 0), sets next window
        mock_open.reset_mock()
        c._retry_dataref_discovery()           # within window → no HTTP
        mock_open.assert_not_called()


# ------------------------------------------------------------------ #
# Poll → FlightState

class TestConnectorPoll:
    def _connector_with_ids(self, overrides: dict | None = None) -> XPlaneRestConnector:
        """Return a connector with pre-populated IDs (skips discovery)."""
        from xplane.rest_connector import _DATAREFS
        c = XPlaneRestConnector()
        for i, name in enumerate(_DATAREFS.keys()):
            c._ids[name] = i + 100
        if overrides:
            c._ids.update(overrides)
        return c

    @patch('xplane.rest_connector.urlopen')
    def test_poll_updates_lat_lon(self, mock_open):
        c = self._connector_with_ids()
        lat_id = c._ids['sim/flightmodel/position/latitude']
        lon_id = c._ids['sim/flightmodel/position/longitude']

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if f'/{lat_id}/value' in url:
                return _response({"data": 52.461})
            if f'/{lon_id}/value' in url:
                return _response({"data": 9.685})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()

        s = c.state
        assert s.lat == pytest.approx(52.461)
        assert s.lon == pytest.approx(9.685)

    @patch('xplane.rest_connector.urlopen')
    def test_poll_decodes_icao_string(self, mock_open):
        import base64
        c = self._connector_with_ids()
        icao_id = c._ids['sim/aircraft/view/acf_ICAO']

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if f'/{icao_id}/value' in url:
                encoded = base64.b64encode(b"C172\x00\x00\x00\x00").decode()
                return _response({"data": encoded})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()
        assert c.state.acf_icao == "C172"

    @patch('xplane.rest_connector.urlopen')
    def test_poll_decodes_tail_string(self, mock_open):
        c = self._connector_with_ids()
        tail_id = c._ids['sim/aircraft/view/acf_tailnum']

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if f'/{tail_id}/value' in url:
                return _response({"data": "D-EIYD"})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()
        assert c.state.tail_number == "D-EIYD"

    @patch('xplane.rest_connector.urlopen')
    def test_poll_updates_on_ground(self, mock_open):
        c = self._connector_with_ids()
        gnd_id = c._ids['sim/flightmodel/failures/onground_any']

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if f'/{gnd_id}/value' in url:
                return _response({"data": 1.0})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()
        assert c.state.on_ground == pytest.approx(1.0)
        assert c.state.on_ground > 0.5


# ------------------------------------------------------------------ #
# PTT detection

class TestPTTDetection:
    @patch('xplane.rest_connector.urlopen')
    def test_ptt_discover_with_array_index(self, mock_open):
        ptt_name = 'sim/joystick/joystick_button_array'
        mock_open.return_value = _response({
            "data": [{"id": 77, "name": ptt_name, "value_type": "int_array"}]
        })
        c = XPlaneRestConnector(ptt_dataref='sim/joystick/joystick_button_array[32]')
        c._discover()
        assert c._ptt_id == 77
        assert c._ptt_idx == 32

    @patch('xplane.rest_connector.urlopen')
    def test_ptt_active_when_button_pressed(self, mock_open):
        # Poll uses GET /api/v3/datarefs/77/value?index=32 → {"data": 1}
        c = XPlaneRestConnector(ptt_dataref='sim/joystick/joystick_button_array[32]')
        c._ids = {}
        c._ptt_id = 77
        c._ptt_idx = 32

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if '/77/value' in url:
                return _response({"data": 1})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()
        assert c.state.ptt_active is True

    @patch('xplane.rest_connector.urlopen')
    def test_ptt_inactive_when_button_released(self, mock_open):
        c = XPlaneRestConnector(ptt_dataref='sim/joystick/joystick_button_array[32]')
        c._ids = {}
        c._ptt_id = 77
        c._ptt_idx = 32

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            if '/77/value' in url:
                return _response({"data": 0})
            return _response({"data": 0.0})

        mock_open.side_effect = side_effect
        c._poll()
        assert c.state.ptt_active is False


# ------------------------------------------------------------------ #
# Fixed-length string encoding (acf_tailnum write path)

class TestEncodeFixedString:
    def test_pads_to_full_length(self):
        # 40-byte field → base64 of exactly 40 bytes
        import base64
        raw = base64.b64decode(encode_fixed_string("D-AB", 40))
        assert len(raw) == 40
        assert raw == b"D-AB" + b"\x00" * 36

    def test_short_over_long_leaves_no_residue(self):
        # The actual bug: a short callsign must not leave the old tail's bytes.
        # Decoding the encoded value (as the connector does) yields only the new
        # string — never "D-EIYD" remnants.
        assert _decode_string(encode_fixed_string("D-AB", 40)) == "D-AB"
        assert _decode_string(encode_fixed_string("N1", 40)) == "N1"
        assert _decode_string(encode_fixed_string("D-EMUH", 40)) == "D-EMUH"

    def test_truncates_overlong_input(self):
        import base64
        raw = base64.b64decode(encode_fixed_string("ABCDEFGHIJ", 4))
        assert raw == b"ABCD"


# ------------------------------------------------------------------ #
# Thread-safety: state returns deepcopy

class TestStateThreadSafety:
    @patch('xplane.rest_connector.urlopen')
    def test_state_returns_deepcopy(self, mock_open):
        mock_open.return_value = _response({"data": 0.0})
        c = XPlaneRestConnector()
        c._state.lat = 52.461
        s = c.state
        s.lat = 0.0
        assert c._state.lat == pytest.approx(52.461)

    @patch('xplane.rest_connector.urlopen')
    def test_on_disconnected_callback_on_poll_failure(self, mock_open):
        from urllib.error import URLError
        fired = []
        c = XPlaneRestConnector(on_disconnected=lambda: fired.append(True))
        c._connected = True
        c._ids = {'sim/flightmodel/position/latitude': 1}

        mock_open.side_effect = URLError("timeout")
        c._poll()   # first failure is silently swallowed per field

        # A full run() iteration triggers on_disconnected after a poll exception
        # We test the _run() logic by checking that after _poll raises, connected flips
        mock_open.side_effect = URLError("timeout")

        def fail_poll():
            raise URLError("timeout")

        c._poll = fail_poll
        c._connected = True
        t = threading.Thread(target=c._run, daemon=True)
        c._running = True
        t.start()
        time.sleep(0.3)
        c._running = False
        t.join(timeout=1.0)
        assert not c._connected
        assert fired


# ------------------------------------------------------------------ #
# Full flight state from live-like data

class TestFlightStateFromRestData:
    """
    Integration-style test: simulate a realistic X-Plane REST poll response
    for a C172 parked at EDDV, stand C3.
    """

    SAMPLE_DATAREFS = {
        'sim/flightmodel/position/latitude':           52.461,
        'sim/flightmodel/position/longitude':           9.685,
        'sim/flightmodel/position/elevation':          55.77,   # metres
        'sim/flightmodel/misc/h_ind':                 183.0,   # feet
        'sim/flightmodel/position/indicated_airspeed':  0.0,
        'sim/flightmodel/position/groundspeed':         0.0,
        'sim/flightmodel/position/psi':               270.0,
        'sim/flightmodel/position/mag_psi':           268.5,
        'sim/flightmodel/failures/onground_any':        1.0,
        'sim/time/paused':                              0.0,
        'sim/cockpit/radios/com1_freq_hz':          12190.0,   # 121.90 MHz
        'sim/cockpit/radios/com2_freq_hz':          11817.5,
        'sim/cockpit/radios/transponder_code':       2000.0,
        'sim/aircraft/view/acf_ICAO':               'C172',
        'sim/aircraft/view/acf_tailnum':            'D-EIYD',
    }

    @patch('xplane.rest_connector.urlopen')
    def test_full_state(self, mock_open):
        from xplane.rest_connector import _DATAREFS

        c = XPlaneRestConnector()
        # Build id map: name → sequential id starting at 100
        for i, name in enumerate(_DATAREFS.keys()):
            c._ids[name] = 100 + i

        reverse = {v: k for k, v in c._ids.items()}

        def side_effect(req, **kwargs):
            url = req.get_full_url()
            # URL is .../datarefs/{id}/value or .../datarefs/{id}/value?index=...
            # Extract the numeric id segment between the last two path components
            path = url.split('?')[0].rstrip('/')
            parts = path.split('/')
            # parts[-1] == 'value', parts[-2] == str(id)
            try:
                ref_id = int(parts[-2])
            except (ValueError, IndexError):
                return _response({"data": 0.0})
            name = reverse.get(ref_id, '')
            value = self.SAMPLE_DATAREFS.get(name, 0.0)
            return _response({"data": value})

        mock_open.side_effect = side_effect
        c._poll()
        s = c.state

        assert s.lat      == pytest.approx(52.461)
        assert s.lon      == pytest.approx(9.685)
        assert s.alt_ind_ft == pytest.approx(183.0)
        assert s.on_ground > 0.5
        assert s.com1_mhz == pytest.approx(121.90, abs=0.01)
        assert int(s.transponder) == 2000
        assert s.acf_icao    == "C172"
        assert s.tail_number == "D-EIYD"
        assert s.heading_true == pytest.approx(270.0)
        assert s.is_flight_loaded
