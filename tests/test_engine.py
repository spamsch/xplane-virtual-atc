"""
Tests for atc.engine JSON extraction. No live Claude — these exercise the
parser that reads boundary_check replies, which some models wrap in prose
and/or a markdown fence.
"""

from __future__ import annotations

import pytest

from atc.engine import _extract_json_object


class TestExtractJsonObject:
    def test_plain_object(self):
        obj = _extract_json_object('{"active_runway": "09L", "atc_callsign": "X Ground"}')
        assert obj["active_runway"] == "09L"

    def test_reasoning_preamble_then_fenced_json(self):
        # The real regression: the model prepends a reasoning paragraph, then a
        # ```json fence. A plain json.loads of the whole reply throws.
        raw = (
            "Wind 180/3 is a near-pure crosswind, effectively calm. Default goes "
            "to the prevailing-westerly preference and the long runway 09L/27R.\n\n"
            "```json\n"
            '{\n  "active_runway": "27R",\n  "atc_callsign": "Hannover Ground",\n'
            '  "notes": "EDDV is Class D; obtain ATIS before taxi."\n}\n'
            "```"
        )
        obj = _extract_json_object(raw)
        assert obj["active_runway"] == "27R"
        assert obj["atc_callsign"] == "Hannover Ground"
        assert "Class D" in obj["notes"]

    def test_bare_fence_without_json_tag(self):
        raw = '```\n{"active_runway": "22", "atc_callsign": "Z Tower"}\n```'
        assert _extract_json_object(raw)["active_runway"] == "22"

    def test_trailing_prose_no_fence(self):
        raw = '{"active_runway": "05", "atc_callsign": "Y"}\n\nHope this helps!'
        assert _extract_json_object(raw)["active_runway"] == "05"

    def test_leading_json_token(self):
        raw = 'json\n{"active_runway": "27", "atc_callsign": "W Ground"}'
        assert _extract_json_object(raw)["active_runway"] == "27"

    def test_nested_braces_in_notes(self):
        raw = '{"active_runway": "16", "atc_callsign": "A", "notes": "use {north} exit"}'
        assert _extract_json_object(raw)["notes"] == "use {north} exit"

    def test_no_json_returns_none(self):
        assert _extract_json_object("no json here at all") is None

    def test_non_object_json_returns_none(self):
        # A bare array is valid JSON but not the object we need → None (fallback).
        assert _extract_json_object("[1, 2, 3]") is None
