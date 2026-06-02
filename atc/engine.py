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
import subprocess
import textwrap
from typing import Any, Optional

from airport.parser import Airport
from aircraft.database import AircraftPerf

log = logging.getLogger(__name__)


def _claude(prompt: str, model: str, timeout: int = 90) -> str:
    proc = subprocess.run(
        ['claude', '-p', '--model', model],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        stderr = proc.stderr[:300].strip()
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr}")
    return proc.stdout.strip()


# ------------------------------------------------------------------ #
# Context building

def _build_situation(airport: Airport, acft: Optional[AircraftPerf],
                     callsign: str, conditions: dict,
                     destination: Optional[Airport] = None) -> str:
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

Respond with valid JSON only, no commentary:
{{
  "active_runway": "<rwy designator, e.g. 27L>",
  "atc_callsign": "<full callsign, e.g. Hannover Ground>",
  "notes": "<brief operational note, max 2 sentences>"
}}"""


def boundary_check(airport: Airport, acft: Optional[AircraftPerf],
                   callsign: str, conditions: dict, model: str,
                   destination: Optional[Airport] = None) -> dict:
    situation = _build_situation(airport, acft, callsign, conditions, destination)
    prompt = _BOUNDARY_PROMPT.format(situation=situation)
    log.info(f"[Opus boundary check for {airport.icao}]")
    raw = _claude(prompt, model)
    # Strip markdown code fences if present
    raw = raw.strip().strip('`')
    if raw.lower().startswith('json'):
        raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"Boundary check returned non-JSON: {raw[:200]}")
        return {'active_runway': 'unknown', 'atc_callsign': f'{airport.name} Ground', 'notes': raw}


# ------------------------------------------------------------------ #
# ATC response generation

_ATC_SYSTEM = """\
You are an air traffic controller. Call sign: {atc_callsign}.

Situation:
{situation}

Session history (most recent last):
{history}

Rules:
- Respond ONLY with the radio transmission — no explanation, no stage directions.
- Use standard ICAO phraseology.
- Address the pilot by their callsign on every transmission.
- Keep it concise. One clearance per transmission.
- If the request is outside your authority (e.g. pilot asks tower questions to
  ground), redirect them politely to the correct frequency.{extra_block}"""


def respond(pilot_message: str,
            airport: Airport,
            acft: Optional[AircraftPerf],
            callsign: str,
            conditions: dict,
            atc_callsign: str,
            history: list,
            model: str,
            extra_instructions: Optional[str] = None,
            destination: Optional[Airport] = None) -> str:

    situation = _build_situation(airport, acft, callsign, conditions, destination)
    history_text = '\n'.join(
        f"  Pilot: {h['pilot']}\n  ATC:   {h['atc']}"
        for h in history[-6:]
    ) or '  (none yet)'

    extra_block = (f"\n\nThis transmission:\n{extra_instructions}"
                   if extra_instructions else "")

    system = _ATC_SYSTEM.format(
        atc_callsign=atc_callsign,
        situation=situation,
        history=history_text,
        extra_block=extra_block,
    )
    prompt = f"{system}\n\nPilot: {pilot_message}\nATC:"

    log.debug(f"Querying {model}")
    return _claude(prompt, model)
