"""
LLM-powered ATC response engine.

Uses the `claude -p` CLI for all generation.
  - Routine exchanges: MODEL_ROUTINE (Sonnet)
  - Boundary decisions (first call, complex clearances): MODEL_BOUNDARY (Opus)

"Boundary" = moments that require operational judgement rather than rote
phraseology: setting up the active runway, issuing a VFR departure clearance,
handling an unusual request, or the first transmission of a new session.
"""

import json
import logging
import re
import subprocess
import textwrap
from typing import Any, Optional

from airport.parser import Airport
from aircraft.database import AircraftPerf
from atc import phraseology

log = logging.getLogger(__name__)


def _claude(prompt: str, model: str, timeout: int = 90) -> str:
    import time as _time
    log.info(f"claude -p --model {model}  ({len(prompt)} chars, timeout {timeout}s)")
    t0 = _time.monotonic()
    try:
        proc = subprocess.run(
            ['claude', '-p', '--model', model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        elapsed = _time.monotonic() - t0
        raise RuntimeError(f"claude timed out after {elapsed:.0f}s (model {model})")
    elapsed = _time.monotonic() - t0
    if proc.returncode != 0:
        stderr = proc.stderr[:300].strip()
        log.warning(f"claude exited {proc.returncode} after {elapsed:.1f}s: {stderr}")
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr}")
    out_chars = len(proc.stdout.strip())
    log.info(f"claude replied in {elapsed:.1f}s ({out_chars} chars)")
    return proc.stdout.strip()


# ------------------------------------------------------------------ #
# Context building

def _build_situation(airport: Airport, acft: Optional[AircraftPerf],
                     callsign: str, conditions: dict,
                     destination: Optional[Airport] = None,
                     flight_status: Optional[str] = None) -> str:
    lines = [
        f"Airport     : {airport.name} ({airport.icao}), elevation {airport.elevation_ft} ft",
    ]
    if destination:
        lines.append(f"Destination : {destination.name} ({destination.icao})")
    lines += [
        f"Runways     : {airport.runway_summary()}",
        f"Frequencies :\n{airport.freq_summary()}",
        f"Aircraft    : {acft.describe() if acft else f'unknown type, callsign {callsign}'}",
        f"Callsign    : {callsign}",
    ]
    if flight_status:
        lines.append(f"Flight phase: {flight_status}")
    if conditions.get('stand'):
        lines.append(f"Stand       : {conditions['stand']}")
    lines += [
        f"Wind        : {conditions.get('wind', 'not available — pilot should check ATIS')}",
        f"QNH         : {conditions.get('qnh', 'not available')}",
    ]
    if conditions.get('atis'):
        lines.append(f"ATIS        : {conditions['atis']}")
    lines += [
        f"Visibility  : {conditions.get('vis', 'not available')}",
        f"Time        : {conditions.get('time', 'not available')}",
        f"Active RWY  : {conditions.get('active_runway', 'to be determined')}",
    ]
    return '\n'.join(lines)


# ------------------------------------------------------------------ #
# Boundary check (Opus): called once per session at startup

_BOUNDARY_PROMPT = """\
You are a senior ATC supervisor setting up a ground control session.

Situation:
{situation}

Determine:
1. Which runway is most likely in use (if wind is unknown, pick based on typical
   prevailing winds or the longest runway).
2. The correct ATC station callsign (e.g. "Hannover Ground").
3. Any relevant notes for VFR operations at this airport.

Output ONLY the JSON object below — no reasoning, preamble, or explanation
before or after it, and no markdown fences. Put any commentary in "notes".
{{
  "active_runway": "<rwy designator, e.g. 27L>",
  "atc_callsign": "<full callsign, e.g. Hannover Ground>",
  "notes": "<brief operational note, max 2 sentences>"
}}"""


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(raw: str) -> Optional[dict]:
    """Pull a JSON object out of an LLM reply that may wrap it in prose and/or a
    markdown fence. Some models now prepend a reasoning paragraph before the
    JSON, so a plain json.loads of the whole reply fails — try, in order:
      1. the contents of a ```json … ``` (or bare ``` … ```) fence,
      2. the substring from the first '{' to the last '}',
      3. the whole reply.
    Returns the first candidate that parses to a dict, else None."""
    s = raw.strip()
    candidates: list[str] = []

    m = _JSON_FENCE_RE.search(s)
    if m:
        candidates.append(m.group(1))

    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        candidates.append(s[start:end + 1])

    candidates.append(s)

    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def boundary_check(airport: Airport, acft: Optional[AircraftPerf],
                   callsign: str, conditions: dict, model: str,
                   destination: Optional[Airport] = None) -> dict:
    situation = _build_situation(airport, acft, callsign, conditions, destination)
    prompt = _BOUNDARY_PROMPT.format(situation=situation)
    log.info(f"[Opus boundary check for {airport.icao}]")
    raw = _claude(prompt, model)
    obj = _extract_json_object(raw)
    if obj is not None:
        return obj
    log.warning(f"Boundary check returned non-JSON: {raw[:200]}")
    return {'active_runway': 'unknown', 'atc_callsign': f'{airport.name} Ground', 'notes': raw}


# ------------------------------------------------------------------ #
# ATC response generation

# Role line + role-specific rules per service kind. The generic rules (concise,
# ICAO phraseology, answer only what was asked, phase-matching) are shared; the
# difference between a controller, a FIS officer, and an uncontrolled-field radio
# operator is authority — who may issue clearances and who may only inform.
_ROLES = {
    "control": "You are an air traffic controller. Call sign: {atc_callsign}.",
    "fis": (
        "You are a Flight Information Service (FIS) officer providing a BASIC "
        "service to VFR traffic in uncontrolled airspace. Call sign: {atc_callsign}. "
        "You inform and assist; you do NOT control."
    ),
    "uncontrolled": (
        "You are the AFIS / radio operator at an UNCONTROLLED aerodrome — there is "
        "NO air traffic control on this frequency. Call sign: {atc_callsign}. "
        "Pilots self-announce their intentions and act on their own authority."
    ),
}

_ROLE_RULES = {
    "control": """\
- For taxi clearances, use ONLY taxiways and holding points given to you in the
  instructions below. Never invent, guess, or substitute taxiway letters or
  holding-point names. If none are provided, keep the taxi instruction generic
  (e.g. "taxi to the holding point for runway 27, follow the green centreline").
- If the request is outside your authority (e.g. pilot asks tower questions to
  ground), redirect them politely to the correct frequency.
- Hand off only to a DIFFERENT station: after take-off, a Tower hands the aircraft
  to Departure or Radar (use the frequency list above), not back to Tower.
- Match every instruction to the Flight phase above. If the aircraft is AIRBORNE,
  do NOT issue ground instructions — no taxi, no "report runway vacated", no
  "hold short", no "line up". If it is ON THE GROUND, do not give airborne
  instructions. A departing aircraft that has just lifted off gets a climb/turn,
  a frequency change, or "report leaving the zone" — never a taxi or vacate call.""",
    "fis": """\
- You provide INFORMATION, not control. You CANNOT issue clearances, take-off or
  landing clearances, headings/altitudes as instructions, or separation. NEVER
  say "cleared". The pilot is responsible for their own navigation, terrain, and
  separation from other traffic.
- Give what a basic service gives: traffic information, weather, QNH, danger/
  restricted-area activity, and navigation help on request. Phrase traffic as
  advice and qualify it ("traffic believed to be…", "if observed", "altitude
  unknown") — you are not separating them.
- You may pass a squawk (usually 7000 for VFR) and acknowledge position reports
  with "roger". Do not invent precise traffic you have no basis for.
- When the pilot reaches their destination's airspace or leaves your area, END
  the service: "<callsign>, basic service terminated, contact <station> on
  <freq>, good day." Use the destination's real frequency from the list above.""",
    "uncontrolled": """\
- You have NO control authority. NEVER issue clearances or instructions: no
  "cleared for take-off", no "cleared to land", no taxi clearance, no "line up",
  no "hold short", no squawk assignment. Pilots act at their own discretion.
- Pass only AERODROME INFORMATION: the runway in use, surface wind, QNH, and any
  known or reported traffic. Acknowledge self-announce/blind calls with "roger".
- A typical reply is "<callsign>, <aerodrome> Information, runway 25 in use, wind
  250 degrees 6 knots, QNH 1015, no reported traffic." If asked for a clearance,
  make clear you can only pass information and the decision is the pilot's.""",
}

_ATC_SYSTEM = """\
{role}

Situation:
{situation}

Session history (most recent last):
{history}

Rules:
- Respond ONLY with the radio transmission — no explanation, no stage directions.
- Use standard ICAO phraseology.
- Address the pilot by their callsign on every transmission.
- Respond ONLY to what the pilot actually said. On a bare initial contact
  (station + callsign with no request), reply "<callsign>, <station>, pass your
  message." — nothing more. NEVER volunteer a clearance, instruction, or
  information the pilot did not request.
- Keep it concise. One item per transmission.
- You ARE {atc_callsign}. NEVER tell the pilot to contact {atc_callsign} or to
  switch to your own frequency — they are already talking to you.
{role_rules}

{examples}{extra_block}"""


def respond(pilot_message: str,
            airport: Airport,
            acft: Optional[AircraftPerf],
            callsign: str,
            conditions: dict,
            atc_callsign: str,
            history: list,
            model: str,
            extra_instructions: Optional[str] = None,
            destination: Optional[Airport] = None,
            flight_status: Optional[str] = None,
            service_kind: str = "control") -> str:
    """Generate one ATC radio reply.

    service_kind selects the operator's authority and example phraseology:
      "control"      — a real controller (Ground/Tower/Approach): may clear.
      "fis"          — Flight Information Service: informs only, never clears.
      "uncontrolled" — AFIS/UNICOM at a field with no ATC: aerodrome info only.
    """
    kind = service_kind if service_kind in _ROLES else "control"
    situation = _build_situation(airport, acft, callsign, conditions, destination,
                                 flight_status=flight_status)
    history_text = '\n'.join(
        f"  Pilot: {h['pilot']}\n  ATC:   {h['atc']}"
        for h in history[-6:]
    ) or '  (none yet)'

    extra_block = (f"\n\nThis transmission:\n{extra_instructions}"
                   if extra_instructions else "")

    system = _ATC_SYSTEM.format(
        role=_ROLES[kind].format(atc_callsign=atc_callsign),
        atc_callsign=atc_callsign,
        situation=situation,
        history=history_text,
        role_rules=_ROLE_RULES[kind],
        examples=phraseology.render_for(kind),
        extra_block=extra_block,
    )
    prompt = f"{system}\n\nPilot: {pilot_message}\nATC:"

    log.debug(f"Querying {model} (service={kind})")
    return _claude(prompt, model)


# ------------------------------------------------------------------ #
# Proactive (controller-initiated) transmission — no pilot prompt

_PROACTIVE_SYSTEM = """\
{role}

Situation:
{situation}

Recent exchanges (most recent last):
{history}

You are about to make a PROACTIVE radio call to {callsign} — they did not just
transmit; you are calling them. Convey exactly this, in one natural radio
transmission:

  {directive}

Rules:
- Output ONLY the radio transmission — no explanation, no stage directions.
- Standard ICAO phraseology; address {callsign}; keep it to one short call.
- You are a Flight Information Service: you INFORM, you do not control. Never
  issue clearances or instructions. Qualify traffic as advice ("if observed",
  "altitude unknown") — you are not separating anyone.
- Do not invent specifics beyond the directive (no precise traffic the directive
  didn't give you)."""


def proactive(directive: str,
              airport: Airport,
              acft: Optional[AircraftPerf],
              callsign: str,
              conditions: dict,
              atc_callsign: str,
              history: list,
              model: str,
              destination: Optional[Airport] = None,
              flight_status: Optional[str] = None,
              service_kind: str = "fis") -> str:
    """Generate a controller-initiated radio call (e.g. FIS passing traffic).

    Unlike respond(), there is no pilot transmission — `directive` says what to
    convey, and the model phrases it. Used by the FIS director to keep the
    en-route service lively and position-aware."""
    kind = service_kind if service_kind in _ROLES else "fis"
    situation = _build_situation(airport, acft, callsign, conditions, destination,
                                 flight_status=flight_status)
    history_text = '\n'.join(
        f"  Pilot: {h['pilot']}\n  ATC:   {h['atc']}"
        for h in history[-4:]
    ) or '  (none yet)'
    prompt = _PROACTIVE_SYSTEM.format(
        role=_ROLES[kind].format(atc_callsign=atc_callsign),
        situation=situation,
        history=history_text,
        callsign=callsign,
        directive=directive,
    )
    log.debug(f"Proactive {model} (service={kind})")
    return _claude(f"{prompt}\n\n{atc_callsign}:", model)
