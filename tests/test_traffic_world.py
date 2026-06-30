"""The stateful party line must remember its aircraft and keep its promises:
a held aircraft gets released, a departure walks request → ... → airborne, and
a callsign stays constant across an aircraft's whole life. No clock, no audio —
the world is RNG-seeded and pure."""

import random

import pytest

import traffic.world as world
from traffic.world import TrafficWorld
from traffic.library import RenderContext


def _ctx() -> RenderContext:
    return RenderContext(atc_callsign="Hannover Ground", runway="27R",
                         qnh="1018", wind="270 degrees 8 knots", airport="Hannover")


def _texts(rendered):
    return " | ".join(l.text for l in rendered.lines)


def test_callsign_consistent_within_exchange():
    w = TrafficWorld(random.Random(1))
    for _ in range(10):
        r = w.next_exchange("ground", _ctx())
        if r:
            for line in r.lines:
                # Every line that names an aircraft names *this* aircraft.
                assert r.callsign in line.text or "QNH" in line.text or "give way" in line.text \
                    or "delay" in line.text or "hold" in line.text.lower()


def test_departure_progresses_through_phases():
    w = TrafficWorld(random.Random(3))
    # Track one aircraft's phase trajectory across ground+tower frequencies.
    trajectory = {}
    for _ in range(60):
        for st in ("ground", "tower"):
            r = w.next_exchange(st, _ctx())
            if r:
                trajectory.setdefault(r.callsign, []).append(r.interaction.id.split(":")[1])
    # At least one aircraft made it all the way to airborne (twr_departed → done).
    assert any("done" in phases and "twr_departed" in phases for phases in trajectory.values())


def test_held_aircraft_is_released(monkeypatch):
    # Force every taxi request to be held, so we can prove the release is owed
    # and delivered to the same aircraft.
    monkeypatch.setattr(world, "_HOLD_PROB", 1.0)
    w = TrafficWorld(random.Random(5))

    held_cs = None
    released = False
    for _ in range(40):
        r = w.next_exchange("ground", _ctx())
        if not r:
            continue
        txt = _texts(r)
        if "hold position" in txt:
            held_cs = r.callsign
            assert "delay" in txt or "give way" in txt or "pushing back" in txt
        elif held_cs and r.callsign == held_cs and "taxi to holding point" in txt:
            released = True
            break
    assert held_cs is not None, "no aircraft was ever held"
    assert released, "a held aircraft was never released"


def test_roster_capped():
    w = TrafficWorld(random.Random(9))
    for _ in range(200):
        w.next_exchange(random.choice(["ground", "tower", "approach"]), _ctx())
    assert len(w.aircraft) <= world._MAX_AIRCRAFT


def test_approach_inbound_checks_in_and_hands_off():
    w = TrafficWorld(random.Random(11))
    phases = {}
    for _ in range(50):
        r = w.next_exchange("approach", _ctx())
        if r:
            phases.setdefault(r.callsign, []).append(r.interaction.id.split(":")[1])
    # Someone walked inbound → contact → handoff → done.
    assert any("done" in p for p in phases.values())


def test_unknown_station_is_silent():
    w = TrafficWorld(random.Random(2))
    assert w.next_exchange("fis", _ctx()) is None
    assert w.next_exchange("ctaf", _ctx()) is None
