"""
A stateful party line — the traffic remembers itself.

The interaction library (library.py) plays *isolated* exchanges: each render
invents a fresh callsign, so a held aircraft is never released and a taxi request
never reaches the runway. That reads as noise, not an airfield.

This module keeps a small roster of other aircraft, each with a callsign that
sticks and a phase in a short lifecycle. Every time the frequency wants another
transmission, we advance one aircraft one step — and crucially we *finish what we
started*: an aircraft told to "hold position, expect a few minutes delay" is owed
a release, and it gets one. A departure walks request → taxi → holding point →
line up → cleared for takeoff → airborne. An arrival walks final → land → vacate
→ taxi to parking. The listener only hears the station they're tuned to, but the
same aircraft surfaces again when they switch frequency, because it's one roster.

Output is a `Rendered` (the same shape library.render produces), so the backend's
half-duplex playback and per-callsign voice seeding work unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from traffic.library import (
    Interaction, Line, Rendered, RenderContext,
    random_vfr_callsign, random_ifr_callsign,
)


# Which controller each phase is worked by. The listener hears only the station
# they're tuned to; an aircraft surfaces on Ground while taxiing, then on Tower
# once it's holding-point ready — same aircraft, different frequency.
_PHASE_STATION = {
    # VFR departure
    "dep_request":   "ground",
    "dep_hold":      "ground",
    "dep_taxi":      "ground",
    "twr_ready":     "tower",
    "twr_lineup":    "tower",
    "twr_departed":  "tower",
    # VFR arrival
    "twr_final":     "tower",
    "twr_landed":    "tower",
    "gnd_vacated":   "ground",
    "gnd_parked":    "ground",
    # radar / approach service
    "app_inbound":   "approach",
    "app_contact":   "approach",
    "app_handoff":   "approach",
}

# Stations served from each tuned frequency (radar reuses the approach pool).
_POOL_FOR = {
    "ground":   {"ground"},
    "tower":    {"tower"},
    "approach": {"approach"},
    "radar":    {"approach"},
}

# Opening phase(s) a brand-new aircraft can spawn into on a given frequency, with
# weights. Ground is mostly outbound taxi calls; Tower a mix of arrivals on final
# and departures reporting ready; radar/approach inbound checking in.
_SPAWN = {
    "ground":   [("dep_request", 0.8), ("gnd_vacated", 0.2)],
    "tower":    [("twr_final", 0.55), ("twr_ready", 0.45)],
    "approach": [("app_inbound", 1.0)],
    "radar":    [("app_inbound", 1.0)],
}

_MAX_AIRCRAFT = 6          # roster cap per airfield
_HOLD_PROB = 0.22         # chance a taxi request is held instead of cleared
_LINEUP_PROB = 0.45       # chance a ready departure is told to line up and wait first


@dataclass
class Aircraft:
    callsign: str
    phase: str
    ifr: bool = False
    ready_at: int = 0       # tick this aircraft may next transmit
    owed: bool = False      # controller owes it a follow-up (e.g. a release)
    note: str = ""          # carried context, e.g. the hold reason
    born: int = 0


def _rwy(ctx: RenderContext) -> str:
    r = (ctx.runway or "").strip()
    return r if r and r.lower() != "unknown" else ""


def _qnh(ctx: RenderContext) -> str:
    return (ctx.qnh or "").strip() or "1013"


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    for a, b in ((" ,", ","), (" .", "."), ("runway ,", "runway"), (",,", ",")):
        text = text.replace(a, b)
    return text.strip().strip(",").strip()


class TrafficWorld:
    """A living roster of other aircraft at one airfield. Call next_exchange()
    each time the frequency should speak; it advances one aircraft and returns
    the exchange, or None when nothing's due (a beat of quiet)."""

    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self.aircraft: list[Aircraft] = []
        self.tick = 0

    # ---- public API ----------------------------------------------------- #

    def next_exchange(self, station: str, ctx: RenderContext) -> Optional[Rendered]:
        self.tick += 1
        self._prune()
        pool = _POOL_FOR.get(station, set())
        if not pool:
            return None

        due = [a for a in self.aircraft
               if _PHASE_STATION.get(a.phase) in pool and a.ready_at <= self.tick]
        # Owed follow-ups first (a held aircraft must get released), then the
        # longest-waiting — so promises resolve and no one is forgotten.
        due.sort(key=lambda a: (not a.owed, a.ready_at, a.born))

        ac = None
        room = len(self.aircraft) < _MAX_AIRCRAFT and station in _SPAWN
        # Spawn a fresh aircraft when nothing is due, or now and then to keep the
        # field populated — but never starve an owed follow-up.
        spawn = room and (not due or (not due[0].owed and self.rng.random() < 0.4))
        if spawn:
            ac = self._spawn(station, ctx)
        elif due:
            ac = due[0]
        if ac is None:
            return None

        lines, nxt, delay, owed = self._advance(ac, station, ctx)
        ac.phase = nxt
        ac.ready_at = self.tick + max(1, delay)
        ac.owed = owed
        if not lines:
            return None
        rendered_lines = [Line(speaker=sp, text=_tidy(t)) for sp, t in lines if _tidy(t)]
        if not rendered_lines:
            return None
        return Rendered(
            interaction=Interaction(id=f"world:{ac.phase}", station=station),
            callsign=ac.callsign, callsign2="", lines=rendered_lines,
        )

    # ---- spawning ------------------------------------------------------- #

    def _spawn(self, station: str, ctx: RenderContext) -> Aircraft:
        choices = _SPAWN[station]
        phases = [p for p, _ in choices]
        weights = [w for _, w in choices]
        phase = self.rng.choices(phases, weights=weights, k=1)[0]
        ifr = phase in ("app_inbound",) and self.rng.random() < 0.45
        cs = self._fresh_callsign(ifr)
        ac = Aircraft(callsign=cs, phase=phase, ifr=ifr, born=self.tick)
        self.aircraft.append(ac)
        return ac

    def _fresh_callsign(self, ifr: bool) -> str:
        existing = {a.callsign for a in self.aircraft}
        for _ in range(20):
            cs = random_ifr_callsign(self.rng) if ifr else random_vfr_callsign(self.rng)
            if cs not in existing:
                return cs
        return cs

    def _prune(self) -> None:
        self.aircraft = [a for a in self.aircraft if a.phase != "done"]
        # Hard cap: drop the longest-lived if we somehow overflow.
        if len(self.aircraft) > _MAX_AIRCRAFT:
            self.aircraft.sort(key=lambda a: a.born)
            self.aircraft = self.aircraft[-_MAX_AIRCRAFT:]

    # ---- the lifecycle -------------------------------------------------- #
    #
    # Each handler returns (lines, next_phase, delay_ticks, owed_followup).
    # `delay` spaces an aircraft out so it doesn't monopolise the frequency;
    # `owed` flags a promise that must be kept (prioritised next time).

    def _advance(self, ac: Aircraft, station: str, ctx: RenderContext):
        atc = ctx.atc_callsign or "Tower"
        cs = ac.callsign
        rwy = _rwy(ctx)
        qnh = _qnh(ctx)
        wind = (ctx.wind or "").strip()
        city = (ctx.airport or "").strip()
        rng = self.rng

        rwy_tp = f"holding point runway {rwy}" if rwy else "the holding point"
        rwy_to = f"runway {rwy}" if rwy else "the runway"

        if ac.phase == "dep_request":
            opener = rng.choice([
                f"{atc}, {cs}, at the general aviation apron, request taxi for VFR departure.",
                f"{atc}, {cs}, ready to taxi.",
                f"{atc}, {cs}, at the GA apron, request taxi.",
            ])
            if rng.random() < _HOLD_PROB:
                ac.note = rng.choice([
                    "a disabled aircraft is being towed, expect a few minutes delay",
                    "company traffic is pushing back ahead of you",
                    "give way to the fuel truck crossing left to right",
                ])
                return ([("pilot", opener),
                         ("atc", f"{cs}, hold position, {ac.note}."),
                         ("pilot", f"Holding position, {cs}.")],
                        "dep_hold", rng.randint(2, 4), True)
            return ([("pilot", opener),
                     ("atc", f"{cs}, taxi to {rwy_tp}, QNH {qnh}."),
                     ("pilot", f"Taxi to {rwy_tp}, QNH {qnh}, {cs}.")],
                    "dep_taxi", rng.randint(2, 4), False)

        if ac.phase == "dep_hold":
            # The release we owe.
            return ([("atc", f"{cs}, the delay is over, taxi to {rwy_tp}, QNH {qnh}."),
                     ("pilot", f"Taxi to {rwy_tp}, QNH {qnh}, {cs}.")],
                    "dep_taxi", rng.randint(2, 4), False)

        if ac.phase == "dep_taxi":
            # An occasional give-way en route, then it's holding-point ready (Tower).
            if rng.random() < 0.4:
                return ([("atc", f"{cs}, give way to the traffic from your right, then continue.")],
                        "twr_ready", rng.randint(1, 3), False)
            return ([("pilot", f"{cs}, approaching {rwy_tp}.")], "twr_ready", rng.randint(1, 3), False)

        if ac.phase == "twr_ready":
            opener = f"{atc}, {cs}, {rwy_tp}, ready for departure."
            if rng.random() < _LINEUP_PROB:
                return ([("pilot", opener),
                         ("atc", f"{cs}, line up and wait {rwy_to}."),
                         ("pilot", f"Line up and wait {rwy_to}, {cs}.")],
                        "twr_lineup", rng.randint(1, 2), True)
            wind_tok = f"wind {wind}, " if wind else ""
            return ([("pilot", opener),
                     ("atc", f"{cs}, {rwy_to}, {wind_tok}cleared for takeoff."),
                     ("pilot", f"Cleared for takeoff {rwy_to}, {cs}.")],
                    "twr_departed", rng.randint(2, 4), False)

        if ac.phase == "twr_lineup":
            wind_tok = f"wind {wind}, " if wind else ""
            return ([("atc", f"{cs}, {rwy_to}, {wind_tok}cleared for takeoff."),
                     ("pilot", f"Cleared for takeoff, {cs}.")],
                    "twr_departed", rng.randint(2, 4), False)

        if ac.phase == "twr_departed":
            return ([("atc", f"{cs}, contact Radar, good day."),
                     ("pilot", f"Radar, {cs}, bye.")],
                    "done", 1, False)

        if ac.phase == "twr_final":
            pt = rng.choice(["3 miles final", "on final", "long final", "2 mile final"])
            wind_tok = f"wind {wind}, " if wind else ""
            return ([("pilot", f"{atc}, {cs}, {pt} {rwy_to}."),
                     ("atc", f"{cs}, {rwy_to}, {wind_tok}cleared to land."),
                     ("pilot", f"Cleared to land {rwy_to}, {cs}.")],
                    "twr_landed", rng.randint(2, 4), False)

        if ac.phase == "twr_landed":
            return ([("atc", f"{cs}, vacate next convenient, contact Ground, welcome to {city}."),
                     ("pilot", f"Vacating, Ground, {cs}.")],
                    "gnd_vacated", rng.randint(2, 4), False)

        if ac.phase == "gnd_vacated":
            opener = rng.choice([
                f"{atc}, {cs}, runway vacated, request taxi to parking.",
                f"{atc}, {cs}, {rwy_to} vacated, request taxi to general aviation parking.",
            ])
            return ([("pilot", opener),
                     ("atc", f"{cs}, taxi to general aviation, welcome to {city}."),
                     ("pilot", f"Taxi to general aviation, {cs}.")],
                    "gnd_parked", rng.randint(3, 5), False)

        if ac.phase == "gnd_parked":
            return ([("pilot", f"{cs}, parking in sight, good day.")], "done", 1, False)

        if ac.phase == "app_inbound":
            miles = rng.randint(8, 25)
            typ = "" if ac.ifr else rng.choice(["a PA28, ", "a Cessna 172, ", "a Robin, ", ""])
            opener = (f"{atc}, {cs}, {typ}{miles} miles to the {rng.choice(['north','south','east','west'])}, "
                      f"inbound, request basic service.")
            return ([("pilot", opener),
                     ("atc", f"{cs}, identified, QNH {qnh}, report field in sight.")],
                    "app_contact", rng.randint(2, 4), False)

        if ac.phase == "app_contact":
            return ([("atc", f"{cs}, traffic, {rng.choice(['11','12','1','2','10'])} o'clock, "
                             f"{rng.randint(2, 6)} miles, opposite direction."),
                     ("pilot", f"Looking, {cs}.")],
                    "app_handoff", rng.randint(2, 4), False)

        if ac.phase == "app_handoff":
            return ([("atc", f"{cs}, contact {city} Tower, good day."),
                     ("pilot", f"{city} Tower, {cs}.")],
                    "done", 1, False)

        # Unknown phase — retire it rather than loop.
        return ([], "done", 1, False)
