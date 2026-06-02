"""
Tests for xplane.ptt_listener.PTTListener.

Fully mocked — no live X-Plane. Discovery is patched at urlopen; the WebSocket
is replaced with a fake async-iterable that yields canned protocol frames.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from xplane.ptt_listener import PTTListener, _parse_index


# --------------------------------------------------------------------------- #
# Helpers

class _FakeWS:
    """Minimal async WebSocket: records sends, iterates over canned frames."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    def __aiter__(self):
        self._it = iter(self._frames)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeConnect:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc):
        return False


def _collect(listener_coro):
    """Run a listener coroutine and return the list of on_change(bool) calls."""
    return asyncio.run(listener_coro)


def _make(source, on_change):
    lis = PTTListener("127.0.0.1", 8086, source, on_change)
    lis._running = True
    return lis


# --------------------------------------------------------------------------- #
# _parse_index

class TestParseIndex:
    def test_with_index(self):
        assert _parse_index("sim/joystick/joystick_button_array[32]") == \
            ("sim/joystick/joystick_button_array", 32)

    def test_no_index(self):
        assert _parse_index("xpilot/ptt") == ("xpilot/ptt", None)

    def test_command_name_untouched(self):
        assert _parse_index("sim/operation/contact_atc_ptt") == \
            ("sim/operation/contact_atc_ptt", None)

    def test_malformed_bracket_kept_whole(self):
        # Not a valid int subscript → leave the name alone rather than corrupt it
        assert _parse_index("foo/bar[abc]") == ("foo/bar[abc]", None)


# --------------------------------------------------------------------------- #
# _apply — edge de-duplication and value coercion

class TestApply:
    def _run_values(self, values):
        edges = []

        async def on_change(p):
            edges.append(p)

        async def drive():
            lis = _make("x/y", on_change)
            for v in values:
                await lis._apply(v)
            return edges

        return _collect(drive())

    def test_fires_only_on_change(self):
        # 0,0,1,1,0 → press once, release once
        assert self._run_values([0, 0, 1, 1, 0]) == [True, False]

    def test_bool_values(self):
        assert self._run_values([False, True, False]) == [True, False]

    def test_float_threshold(self):
        # 0.4 is below the 0.5 threshold; 0.9 is above
        assert self._run_values([0.4, 0.9]) == [True]

    def test_list_value_uses_first_element(self):
        # array dataref delivered as a 1-element list
        assert self._run_values([[1], [0]]) == [True, False]

    def test_garbage_ignored(self):
        assert self._run_values([None, "nope", 1]) == [True]


# --------------------------------------------------------------------------- #
# _discover — dataref vs command resolution

def _fake_urlopen_factory(responses):
    """responses: dict mapping a substring of the URL → parsed JSON 'data' list."""
    class _Resp:
        def __init__(self, payload):
            self._payload = json.dumps({"data": payload}).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        url = req.full_url
        for needle, payload in responses.items():
            if needle in url:
                return _Resp(payload)
        return _Resp([])

    return _urlopen


class TestDiscover:
    def test_resolves_dataref(self):
        responses = {
            "/datarefs": [{"id": 99, "name": "xpilot/ptt"}],
        }
        with patch("xplane.ptt_listener.urlopen", _fake_urlopen_factory(responses)):
            lis = _make("xpilot/ptt", None)
            assert lis._discover() == ("dataref", 99, None)

    def test_resolves_dataref_with_index(self):
        responses = {
            "/datarefs": [{"id": 7, "name": "sim/joystick/joystick_button_array"}],
        }
        with patch("xplane.ptt_listener.urlopen", _fake_urlopen_factory(responses)):
            lis = _make("sim/joystick/joystick_button_array[32]", None)
            assert lis._discover() == ("dataref", 7, 32)

    def test_falls_through_to_command(self):
        # Not a dataref, but a command exists
        responses = {
            "/datarefs": [],
            "/commands": [{"id": 2911, "name": "sim/operation/contact_atc_ptt"}],
        }
        with patch("xplane.ptt_listener.urlopen", _fake_urlopen_factory(responses)):
            lis = _make("sim/operation/contact_atc_ptt", None)
            assert lis._discover() == ("command", 2911, None)

    def test_not_found(self):
        with patch("xplane.ptt_listener.urlopen", _fake_urlopen_factory({})):
            lis = _make("nope/nope", None)
            assert lis._discover() == (None, None, None)


# --------------------------------------------------------------------------- #
# _listen — full subscribe + parse flow against a fake socket

class TestListen:
    def test_command_subscription_and_edges(self):
        edges = []

        async def on_change(p):
            edges.append(p)

        frames = [
            json.dumps({"req_id": 1, "success": True, "type": "result"}),
            json.dumps({"type": "command_update_is_active", "data": {"2911": True}}),
            json.dumps({"type": "command_update_is_active", "data": {"2911": False}}),
        ]
        ws = _FakeWS(frames)

        async def drive():
            lis = _make("sim/operation/contact_atc_ptt", on_change)
            with patch("xplane.ptt_listener.websockets.connect", return_value=_FakeConnect(ws)):
                await lis._listen("command", 2911, None)
            return edges

        assert _collect(drive()) == [True, False]
        # Verify the subscribe message we actually sent
        assert ws.sent[0]["type"] == "command_subscribe_is_active"
        assert ws.sent[0]["params"]["commands"] == [{"id": 2911}]

    def test_dataref_subscription_with_index(self):
        edges = []

        async def on_change(p):
            edges.append(p)

        frames = [
            json.dumps({"type": "dataref_update_values", "data": {"7": 1}}),
            json.dumps({"type": "dataref_update_values", "data": {"7": 0}}),
        ]
        ws = _FakeWS(frames)

        async def drive():
            lis = _make("sim/joystick/joystick_button_array[32]", on_change)
            with patch("xplane.ptt_listener.websockets.connect", return_value=_FakeConnect(ws)):
                await lis._listen("dataref", 7, 32)
            return edges

        assert _collect(drive()) == [True, False]
        assert ws.sent[0]["type"] == "dataref_subscribe_values"
        assert ws.sent[0]["params"]["datarefs"] == [{"id": 7, "index": 32}]

    def test_ignores_unrelated_frames(self):
        edges = []

        async def on_change(p):
            edges.append(p)

        frames = [
            json.dumps({"type": "dataref_update_values", "data": {"999": 1}}),  # other id
            "not even json",
            json.dumps({"type": "something_else", "data": {"7": 1}}),
            json.dumps({"type": "dataref_update_values", "data": {"7": 1}}),
        ]
        ws = _FakeWS(frames)

        async def drive():
            lis = _make("xpilot/ptt", on_change)
            with patch("xplane.ptt_listener.websockets.connect", return_value=_FakeConnect(ws)):
                await lis._listen("dataref", 7, None)
            return edges

        assert _collect(drive()) == [True]


# --------------------------------------------------------------------------- #
# stop() surfaces a release if torn down mid-press

class TestStop:
    def test_stop_releases_stuck_ptt(self):
        edges = []

        async def on_change(p):
            edges.append(p)

        async def drive():
            lis = _make("xpilot/ptt", on_change)
            # Simulate a press with no matching release, then stop.
            await lis._apply(1)
            assert lis.pressed is True
            lis._task = None  # nothing real running
            await lis.stop()
            return edges

        assert _collect(drive()) == [True, False]
