"""
Real-Claude phraseology tests — they actually shell out to `claude -p`.

Opt-in: SKIPPED unless VATC_LLM is set, because they need the `claude` CLI
installed and signed in, cost tokens, and are non-deterministic. Run them to
check the controller's real behaviour against the prompt/examples:

    VATC_LLM=1 .venv/bin/python -m pytest -s tests/test_live_claude.py

Assertions are intentionally loose (the model is non-deterministic); they check
the behaviours the prompt strongly directs — e.g. answering a bare initial
contact with "pass your message" and NOT clearing taxi unrequested.
"""
import os
import shutil

import pytest

import config
from airport.parser import Airport, Runway, Frequency
from atc import engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("VATC_LLM"),
    reason="real-Claude test — set VATC_LLM=1 (needs the `claude` CLI signed in)",
)


@pytest.fixture(scope="module", autouse=True)
def _require_claude():
    if not shutil.which("claude"):
        pytest.skip("`claude` CLI not found on PATH")


def _eddv() -> Airport:
    return Airport(
        icao="EDDV", name="Hannover", elevation_ft=183, lat=52.4604, lon=9.6849,
        frequencies=[
            Frequency(53, "Ground", 121.950, "Hannover Ground"),
            Frequency(54, "Tower", 120.180, "Hannover Tower"),
        ],
        runways=[Runway("27R", "09L", 52.4669, 9.6997, 52.4682, 9.6527, 45, 0, 0)],
    )


_CONDITIONS = {
    "active_runway": "27R", "qnh": "1013", "wind": "calm",
    "vis": "10 km", "time": "daytime",
}


def _norm(s: str) -> str:
    return s.lower().replace(" ", "").replace("-", "")


def test_initial_contact_gets_pass_your_message():
    """Bare initial contact, no request → 'pass your message', and crucially
    NO taxi/clearance volunteered."""
    reply = engine.respond(
        pilot_message="Hannover Ground, D-EIYD.",
        airport=_eddv(), acft=None, callsign="D-EIYD",
        conditions=_CONDITIONS, atc_callsign="Hannover Ground",
        history=[], model=config.MODEL_BOUNDARY,
    )
    print(f"\n[initial contact] ATC: {reply!r}")
    assert reply.strip(), "empty response"
    assert "deiyd" in _norm(reply), "controller did not address the callsign"
    assert "passyourmessage" in _norm(reply) or "goahead" in _norm(reply)
    assert "taxi" not in reply.lower(), "issued taxi clearance that was not requested"


def test_taxi_request_gets_a_taxi_clearance():
    """With a taxi request and a computed route, the controller clears taxi
    using the supplied taxiways only."""
    extra = (
        "TAXI ROUTE for EDDV (relay it EXACTLY; do NOT invent or substitute "
        "taxiways or holding points): from stand 138, taxi via T2, M, N to hold "
        "short of runway 27R. The ONLY valid taxiways for this clearance are: "
        "T2, M, N. Active runway 27R."
    )
    reply = engine.respond(
        pilot_message="Hannover Ground, D-EIYD, C172, VFR to Bielefeld, request taxi.",
        airport=_eddv(), acft=None, callsign="D-EIYD",
        conditions=_CONDITIONS, atc_callsign="Hannover Ground",
        history=[], model=config.MODEL_BOUNDARY, extra_instructions=extra,
    )
    print(f"\n[taxi request] ATC: {reply!r}")
    assert "taxi" in reply.lower()
    assert "27" in reply, "clearance did not mention the runway"
    # Must not introduce taxiways outside the supplied set (no hallucinated ones).
    bogus = [w for w in ("alpha", "quebec", "whiskey") if w in reply.lower()]
    assert not bogus, f"hallucinated taxiways: {bogus}"


def test_boundary_check_returns_runway_and_callsign():
    ctx = engine.boundary_check(
        _eddv(), acft=None, callsign="D-EIYD",
        conditions=_CONDITIONS, model=config.MODEL_BOUNDARY,
    )
    print(f"\n[boundary check] {ctx}")
    assert ctx.get("active_runway"), "no active_runway in boundary check"
    assert ctx.get("atc_callsign"), "no atc_callsign in boundary check"
