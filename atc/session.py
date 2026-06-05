"""
ATCSession — stateful multi-station ATC session.

Wraps atc.engine.respond() with:
  - Phase and Station tracking across the full flight
  - Squawk lifecycle: assign at first Tower call, 7000 on CTR exit, reassign at Approach
  - Frequency handoff detection parsed from LLM response text
  - Per-airport conditions (keyed by ICAO) flattened for the engine
  - First call on each new station uses Opus; routine exchanges use Sonnet
"""

import logging
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)

import config
from airport.parser import Airport
from airport import taxi
from aircraft.database import AircraftPerf
from atc import engine as atc_engine
from atc.parser import parse as parse_radio_call


# ------------------------------------------------------------------ #
# Enums

class Phase(Enum):
    PRE_DEPARTURE   = "pre_departure"
    GROUND_DEPARTURE = "ground_departure"
    TAXIING         = "taxiing"
    DEPARTING       = "departing"
    EN_ROUTE        = "en_route"
    EN_ROUTE_FIS    = "en_route_fis"
    APPROACH        = "approach"
    CIRCUIT         = "circuit"
    GROUND_ARRIVAL  = "ground_arrival"
    PARKED          = "parked"


class Station(Enum):
    GND   = "ground"
    TWR   = "tower"
    APP   = "approach"
    DEP   = "departure"
    RADAR = "radar"
    FIS   = "fis"
    CTAF  = "ctaf"   # uncontrolled aerodrome self-announce / AFIS — no ATC


# Stations that issue no control clearances. The state machine treats these as
# information-only: no squawk assignment, no taxi clearance injection.
_INFO_STATIONS = (Station.FIS, Station.CTAF)


# ------------------------------------------------------------------ #
# Response type

@dataclass
class ATCResponse:
    text: str
    phase_after: Phase
    station_after: Station
    squawk: Optional[str] = None            # assigned IN this response; None if not assigned here
    frequency_change: Optional[float] = None
    runway: Optional[str] = None
    qnh: Optional[int] = None
    model: Optional[str] = None             # model used to generate this response
    # Set when the pilot called a handed-off station before tuning COM1 to it.
    # No LLM was called; the new station didn't "hear" the transmission.
    on_wrong_frequency: bool = False
    expected_frequency: Optional[float] = None   # freq COM1 must be on to reach the station
    pending_station: Optional[str] = None        # station label the pilot was trying to reach


# ------------------------------------------------------------------ #
# Response parsing

_SQUAWK_RE  = re.compile(r'\bsquawk\s+([0-7]{4})\b', re.IGNORECASE)
_FREQ_RE    = re.compile(r'contact\b[^.\n]{0,40}?(1[1-3][0-9]\.\d{2,3})', re.IGNORECASE)
_QNH_RE     = re.compile(r'\bQ\.?N\.?H\.?\s+(\d{3,4})\b', re.IGNORECASE)
_RUNWAY_RE  = re.compile(r'\brunway\s+(\d{1,2}[LCR]?)\b', re.IGNORECASE)
_TAKEOFF_RE = re.compile(r'cleared\s+(for|to)\s+take[\s-]?off', re.IGNORECASE)
_LAND_RE    = re.compile(r'cleared\s+(to\s+land|for\s+landing)', re.IGNORECASE)
_RADAR_RE   = re.compile(r'radar\s+contact', re.IGNORECASE)
_CTR_EXIT_RE = re.compile(
    r'leaving\s+controlled\s+airspace|frequency\s+change\s+approved|squawk\s+7000',
    re.IGNORECASE,
)
_TAXI_RE = re.compile(r'\btaxi\s+(?:to|via)\b', re.IGNORECASE)
# A pilot asking for taxi (or reporting ready to taxi). Used to gate the taxi
# clearance so a bare initial contact doesn't get one unrequested.
_TAXI_REQUEST_RE = re.compile(r'\b(?:request\s+taxi|ready\s+(?:to|for)\s+taxi|for\s+taxi|taxi)\b',
                              re.IGNORECASE)
_PARK_RE = re.compile(r'readback correct', re.IGNORECASE)


def _parse_response(text: str) -> dict:
    out: dict = {}
    m = _SQUAWK_RE.search(text)
    if m:
        out['squawk'] = m.group(1)
    m = _FREQ_RE.search(text)
    if m:
        try:
            out['frequency_change'] = float(m.group(1))
        except ValueError:
            pass
    m = _QNH_RE.search(text)
    if m:
        out['qnh'] = int(m.group(1))
    m = _RUNWAY_RE.search(text)
    if m:
        out['runway'] = m.group(1)
    if _TAKEOFF_RE.search(text):
        out['trigger'] = 'takeoff'
    elif _LAND_RE.search(text):
        out['trigger'] = 'land'
    elif _RADAR_RE.search(text):
        out['trigger'] = 'radar_contact'
    elif _CTR_EXIT_RE.search(text):
        out['trigger'] = 'ctr_exit'
    return out


# ------------------------------------------------------------------ #
# Squawk generation

_RESERVED_SQUAWKS = {'7500', '7600', '7700', '7000', '2000', '0000', '1200'}


def _generate_squawk() -> str:
    """Random VFR discrete squawk: valid octal, not reserved, not 7xxx."""
    while True:
        code = ''.join(str(random.randint(0, 7)) for _ in range(4))
        if code not in _RESERVED_SQUAWKS and not code.startswith('7'):
            return code


_COMPASS_8 = ('north', 'north-east', 'east', 'south-east',
              'south', 'south-west', 'west', 'north-west')


def _compass(bearing_deg: float) -> str:
    """8-point compass name for a bearing in degrees."""
    return _COMPASS_8[int((bearing_deg % 360) / 45.0 + 0.5) % 8]


def _dist_bearing_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Great-circle distance (NM) and initial bearing (deg) FROM point 1 (the
    field) TO point 2 (the aircraft) — i.e. which way the aircraft lies."""
    import math
    R_NM = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * R_NM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    brg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return dist, brg


# Tolerance for matching live COM1 to a handoff frequency, in MHz. 0.02 covers
# 8.33 kHz channels labelled at 25 kHz spacing and float rounding, without
# letting an adjacent 25 kHz channel slip through.
_FREQ_MATCH_TOL = 0.02


def _freq_matches(com1_mhz: float, target_mhz: float) -> bool:
    return abs(com1_mhz - target_mhz) <= _FREQ_MATCH_TOL


# ------------------------------------------------------------------ #
# Parser code → Station

_PARSER_TO_STATION: dict[str, Station] = {
    'GND':   Station.GND,
    'TWR':   Station.TWR,
    'APP':   Station.APP,
    'DEP':   Station.RADAR, # "Departure" is the Radar service in German airspace
    'RADAR': Station.RADAR,
    'CLD':   Station.GND,   # clearance delivery handled by Ground in this engine
    'FIS':   Station.FIS,   # "Information" — the flight information service
    'ATIS':  Station.FIS,   # recorded ATIS has no live station; treat a call as FIS
    'CTAF':  Station.CTAF,  # UNICOM / self-announce at an uncontrolled aerodrome
}

# Apt.dat frequency type code → Station
# Code 56 (Departure) maps to RADAR — in German airspace "Radar" is the Departure service
_FREQ_TYPE_TO_STATION: dict[int, Station] = {
    50: Station.FIS,
    51: Station.CTAF,   # CTAF/Unicom — uncontrolled self-announce
    52: Station.GND,
    53: Station.GND,
    54: Station.TWR,
    55: Station.APP,
    56: Station.RADAR,
}

# Phase reached when switching to a station (departing direction)
_STATION_TO_PHASE: dict[Station, Phase] = {
    Station.TWR:   Phase.GROUND_DEPARTURE,
    Station.APP:   Phase.APPROACH,
    Station.RADAR: Phase.EN_ROUTE,
    Station.FIS:   Phase.EN_ROUTE_FIS,
}

_STATION_LABELS: dict[Station, str] = {
    Station.GND:   'Ground',
    Station.TWR:   'Tower',
    Station.APP:   'Approach',
    Station.DEP:   'Departure',
    Station.RADAR: 'Radar',
    Station.FIS:   'Information',
    Station.CTAF:  'Information',   # AFIS field; pure UNICOM becomes 'Traffic' below
}


# ------------------------------------------------------------------ #

class ATCSession:
    """
    Manages a complete VFR flight session across multiple ATC stations.

    Usage:
        session = ATCSession(departure, destination, aircraft, callsign, conditions)
        response = session.process("Hannover Ground, D-EIYD, request startup...")
        print(response.text, response.phase_after, response.squawk)
    """

    def __init__(self,
                 departure: Airport,
                 destination: Optional[Airport],
                 aircraft: Optional[AircraftPerf],
                 callsign: str,
                 conditions: dict,
                 flight_plan=None):
        """
        Args:
            conditions: {ICAO: {qnh, wind_dir, wind_kts, visibility_km, atis,
                                 active_runway}} — keyed by airport ICAO.
            flight_plan: an optional flightplan.FlightPlan. When set, the session
                stages a full journey: an uncontrolled departure self-announces on
                CTAF/AFIS (no clearances), the en-route leg is worked by a named
                FIS, and a controlled arrival runs Tower→Ground. Station context
                is driven by position (the backend), not by the pilot's wording —
                so "Information" doesn't yank an AFIS field over to en-route FIS.
        """
        self.departure       = departure
        self.destination     = destination
        self.aircraft        = aircraft
        self.callsign        = callsign
        self.conditions      = conditions
        self.flight_plan     = flight_plan

        self.phase           = Phase.PRE_DEPARTURE
        # An uncontrolled departure field starts on CTAF/AFIS, not Ground.
        self.current_station = (
            Station.CTAF
            if flight_plan is not None and flight_plan.departure.controlled is False
            else Station.GND
        )
        self.current_airport = departure

        self.squawk: Optional[str] = None
        self.squawk_history: list[str] = []
        # True once the controller has released us from the current frequency —
        # a "frequency change approved", CTR exit, or a handoff to another freq.
        # Until then, an airborne aircraft must not be handed to a new airport.
        self.freq_change_cleared: bool = False

        self._history: list[dict] = []
        self._stations_seen: set[Station] = set()   # stations that have had ≥1 response
        # A handoff the controller issued ("contact X on FREQ") that the pilot
        # has not yet tuned COM1 to. (station, freq_mhz). Only used when the
        # caller passes live COM1 to process(); None otherwise (CLI/tests switch
        # immediately as before).
        self._pending_handoff: Optional[tuple["Station", float]] = None

    # -------------------------------------------------------------- #
    # Public interface

    def process(self, pilot_message: str,
                com1_mhz: Optional[float] = None,
                lat: Optional[float] = None,
                lon: Optional[float] = None,
                on_ground: Optional[bool] = None,
                altitude_ft: Optional[float] = None,
                gs_kts: Optional[float] = None,
                airspace: Optional[str] = None) -> ATCResponse:
        """Generate the ATC reply to a pilot transmission.

        com1_mhz: the aircraft's live COM1 frequency, if known. When provided,
        a station the controller handed the pilot off to will only answer once
        COM1 is actually tuned to its frequency — otherwise an on_wrong_frequency
        response is returned and no LLM call is made. When None (CLI / tests),
        handoffs take effect immediately, as before.

        lat/lon: the aircraft's live position. When known on the ground, a real
        taxi route is computed from the airport's apt.dat taxi network and handed
        to the controller, so it never invents taxiways or holding points.

        on_ground / altitude_ft / gs_kts: the live flight situation. When known,
        a short situation summary (on the ground vs airborne + height AGL,
        distance and bearing from the field, taxiing vs stationary, phase) is
        handed to the controller so it gives phase-appropriate instructions —
        e.g. it won't tell an airborne aircraft to report runway vacated.
        """
        call = parse_radio_call(pilot_message)

        # Detect station switch from the pilot's own transmission
        pilot_station = _PARSER_TO_STATION.get(call.station or '')
        # In flight-plan mode, while we're in an information context (an
        # uncontrolled field's AFIS or the en-route FIS), the station is driven by
        # position — the backend hands us to the next service. Ignore any
        # voice-derived switch here: "Bielefeld Information" must not flip to
        # en-route FIS, and "request departure information" must not be mistaken
        # for a Departure/Radar handoff. Once at a controlled field, normal
        # Ground↔Tower voice switching resumes.
        if (self.flight_plan is not None
                and self.current_station in _INFO_STATIONS):
            pilot_station = None
        if pilot_station and pilot_station != self.current_station:
            # If this is a handed-off station and we know COM1, require the pilot
            # to be tuned to it before that station will respond.
            if (com1_mhz is not None and self._pending_handoff
                    and self._pending_handoff[0] == pilot_station):
                target_freq = self._pending_handoff[1]
                if not _freq_matches(com1_mhz, target_freq):
                    return ATCResponse(
                        text="",
                        phase_after=self.phase,
                        station_after=self.current_station,
                        on_wrong_frequency=True,
                        expected_frequency=target_freq,
                        pending_station=_STATION_LABELS.get(pilot_station, "the next station"),
                    )
                # Tuned correctly — complete the handoff.
                self._pending_handoff = None
            self._switch_station(pilot_station)

        # Decide whether to pre-assign a squawk
        pre_squawk: Optional[str] = None
        if self._should_assign_squawk():
            pre_squawk = _generate_squawk()

        # Build extra instructions for the LLM
        extra_parts: list[str] = []
        if pre_squawk:
            extra_parts.append(
                f"Assign squawk {pre_squawk} to {self.callsign} in this transmission. "
                f"Use this exact code."
            )
        if self._should_issue_ctr_exit(pilot_message):
            extra_parts.append(
                "Issue CTR exit instructions: tell the pilot to squawk 7000 and "
                "contact the next frequency (use the departure/radar frequency from "
                "the airport's frequency list)."
            )
        # Only hand over a taxi clearance when the pilot actually asks for taxi.
        # Otherwise (e.g. a bare initial contact) the controller must not pre-empt
        # the request — the system prompt's examples make it reply "pass your
        # message" instead.
        if self.current_station == Station.GND and _TAXI_REQUEST_RE.search(pilot_message):
            extra_parts.append(self._taxi_instruction(lat, lon))

        # Model: Opus for first call on each new station, Sonnet thereafter
        first_call_on_station = self.current_station not in self._stations_seen
        model = config.MODEL_BOUNDARY if first_call_on_station else config.MODEL_ROUTINE

        # Call LLM
        response_text = atc_engine.respond(
            pilot_message=pilot_message,
            airport=self.current_airport,
            acft=self.aircraft,
            callsign=call.callsign or self.callsign,
            conditions=self._flat_conditions(),
            atc_callsign=self._atc_callsign(),
            history=self._history,
            model=model,
            extra_instructions='\n'.join(extra_parts) if extra_parts else None,
            destination=self.destination,
            flight_status=self._flight_status(on_ground, altitude_ft, gs_kts, lat, lon, airspace),
            service_kind=self._service_kind(),
        )

        self._stations_seen.add(self.current_station)

        # Parse structured data from the response
        parsed = _parse_response(response_text)

        # --- Squawk bookkeeping ---
        assigned_squawk: Optional[str] = None
        if pre_squawk:
            assigned_squawk = pre_squawk
            self.squawk = pre_squawk
            self.squawk_history.append(pre_squawk)
            spoken = parsed.get('squawk')
            if spoken and spoken != pre_squawk:
                log.warning(
                    f"LLM ignored squawk injection: instructed {pre_squawk}, "
                    f"spoke {spoken}. Using {pre_squawk}."
                )
        elif parsed.get('squawk') and parsed['squawk'] != self.squawk:
            sq = parsed['squawk']
            if sq != '7000':
                assigned_squawk = sq
                self.squawk = sq
            if sq not in self.squawk_history:
                self.squawk_history.append(sq)

        # --- Phase transitions from response content ---
        trigger = parsed.get('trigger')
        if trigger == 'takeoff':
            self.phase = Phase.DEPARTING
        elif trigger == 'radar_contact' and self.phase in (Phase.DEPARTING, Phase.EN_ROUTE):
            self.phase = Phase.EN_ROUTE
        elif trigger == 'ctr_exit':
            self.phase = Phase.EN_ROUTE
            self.freq_change_cleared = True   # released from the zone/frequency
            if '7000' not in self.squawk_history:
                self.squawk_history.append('7000')
        elif trigger == 'land':
            self.phase = Phase.CIRCUIT

        if _TAXI_RE.search(response_text) and self.phase == Phase.PRE_DEPARTURE:
            self.phase = Phase.TAXIING
        if _PARK_RE.search(response_text) and self.phase == Phase.GROUND_ARRIVAL:
            self.phase = Phase.PARKED

        # --- Station transition from handoff frequency in response ---
        freq_change: Optional[float] = parsed.get('frequency_change')
        if freq_change:
            self.freq_change_cleared = True   # controller handed us to another freq
            new_station = self._station_from_freq(freq_change)
            if new_station and new_station != self.current_station:
                if com1_mhz is None:
                    # Legacy: no live COM1 — switch immediately.
                    self._switch_station(new_station)
                else:
                    # Defer: the new station won't answer until the pilot tunes
                    # COM1 to this frequency (checked on the next transmission).
                    self._pending_handoff = (new_station, freq_change)

        # Update history
        self._history.append({
            'pilot': pilot_message,
            'atc': response_text,
            'model': model,
            'station': self.current_station.value,
        })

        return ATCResponse(
            text=response_text,
            phase_after=self.phase,
            station_after=self.current_station,
            squawk=assigned_squawk,
            frequency_change=freq_change,
            runway=parsed.get('runway'),
            qnh=parsed.get('qnh'),
            model=model,
        )

    # -------------------------------------------------------------- #
    # Internal helpers

    def _switch_station(self, new_station: Station) -> None:
        self.current_station = new_station
        if new_station in _STATION_TO_PHASE:
            # Only advance phase if it makes sense directionally
            new_phase = _STATION_TO_PHASE[new_station]
            if new_phase != self.phase:
                self.phase = new_phase
        elif new_station == Station.GND and self.phase in (Phase.CIRCUIT, Phase.GROUND_ARRIVAL):
            self.phase = Phase.GROUND_ARRIVAL

    def _should_assign_squawk(self) -> bool:
        # First Tower call with no squawk yet (departing aircraft)
        if (self.current_station == Station.TWR
                and self.squawk is None
                and self.phase in (Phase.PRE_DEPARTURE, Phase.GROUND_DEPARTURE, Phase.TAXIING)):
            return True
        # First Approach call (arriving aircraft — always reassigns)
        if (self.current_station == Station.APP
                and self.phase == Phase.APPROACH
                and Station.APP not in self._stations_seen):
            return True
        return False

    def _should_issue_ctr_exit(self, text: str) -> bool:
        text_lower = text.lower()
        return (
            self.phase in (Phase.DEPARTING, Phase.EN_ROUTE)
            and self.current_station == Station.TWR
            and any(kw in text_lower for kw in (
                'ctr boundary', 'leaving the zone', 'leaving controlled',
                'control zone', 'ctr', 'approaching ctr',
            ))
        )

    def _station_from_freq(self, freq_mhz: float) -> Optional[Station]:
        """Map a MHz value to a Station by checking both airport frequency lists."""
        for ap in filter(None, (self.current_airport, self.departure, self.destination)):
            for f in ap.frequencies:
                if abs(f.freq_mhz - freq_mhz) < 0.01:
                    return _FREQ_TYPE_TO_STATION.get(f.type_code)
        return None

    def _atc_callsign(self) -> str:
        # En-route FIS is a regional service, not the field's — name it from the
        # flight plan (e.g. "Bremen Information").
        if self.current_station == Station.FIS and self.flight_plan is not None:
            return self.flight_plan.fis.callsign
        city = self.current_airport.name.split()[0]
        if self.current_station == Station.CTAF:
            # AFIS fields answer as "<field> Information"; a field with no
            # frequency at all is pure UNICOM → "<field> Traffic".
            label = 'Information' if self.current_airport.frequencies else 'Traffic'
            return f"{city} {label}"
        label = _STATION_LABELS.get(self.current_station, 'Radio')
        return f"{city} {label}"

    def _service_kind(self) -> str:
        """Which authority the current station speaks with — selects the engine's
        role + phraseology. FIS informs, CTAF/AFIS passes aerodrome info, the rest
        control."""
        if self.current_station == Station.FIS:
            return 'fis'
        if self.current_station == Station.CTAF:
            return 'uncontrolled'
        return 'control'

    # -------------------------------------------------------------- #
    # Flight-plan context — driven by the backend from live position.

    def enter_arrival_field(self, airport: Airport, controlled: bool) -> None:
        """Switch the session to the arrival field as it comes into range. For a
        VFR arrival a controlled field is worked Tower-first (then Ground after
        landing, by voice); an uncontrolled field is CTAF. Phase → APPROACH."""
        self.current_airport = airport
        if controlled:
            self.current_station = Station.TWR
        else:
            self.current_station = Station.CTAF
        # Orchestrator only calls this once the field is in range, so advance to
        # APPROACH unless we've already touched down there.
        if self.phase not in (Phase.GROUND_ARRIVAL, Phase.PARKED):
            self.phase = Phase.APPROACH

    def enter_enroute_fis(self, context_airport: Optional[Airport] = None) -> None:
        """Hand the flight to the en-route FIS. context_airport (usually the
        destination) gives the engine a frequency list for the eventual handoff.
        Called by the orchestrator once airborne and clear of the departure field,
        so it advances to the en-route FIS phase outright."""
        if context_airport is not None:
            self.current_airport = context_airport
        self.current_station = Station.FIS
        if self.phase not in (Phase.APPROACH, Phase.CIRCUIT,
                              Phase.GROUND_ARRIVAL, Phase.PARKED):
            self.phase = Phase.EN_ROUTE_FIS

    def _flight_status(self,
                       on_ground: Optional[bool],
                       altitude_ft: Optional[float],
                       gs_kts: Optional[float],
                       lat: Optional[float],
                       lon: Optional[float],
                       airspace: Optional[str] = None) -> Optional[str]:
        """A compact, human-readable summary of where the aircraft actually is,
        for the controller's situational awareness. Returns None when we know
        nothing live (CLI / tests) so the prompt is unchanged there."""
        phase = self.phase.value.replace('_', ' ')
        if on_ground is None and altitude_ft is None and lat is None and not airspace:
            return None

        parts: list[str] = []
        if on_ground is True:
            moving = (gs_kts or 0) > 3
            parts.append("on the ground" + (", taxiing" if moving else ", stationary"))
        elif on_ground is False:
            s = "airborne"
            if altitude_ft is not None:
                s += f" {int(round(altitude_ft)):,} ft"
                if self.current_airport is not None:
                    agl = altitude_ft - self.current_airport.elevation_ft
                    if agl > 50:
                        s += f" ({int(round(agl)):,} ft AGL)"
            parts.append(s)

        # Distance and bearing from the field — lets the controller reason about
        # departing the zone / arriving / overflying.
        if (lat is not None and lon is not None and self.current_airport is not None):
            nm, brg = _dist_bearing_nm(
                self.current_airport.lat, self.current_airport.lon, lat, lon)
            if nm >= 0.6:
                parts.append(f"{nm:.0f} NM {_compass(brg)} of {self.current_airport.icao}")

        if airspace:
            parts.append(airspace)
        route_note = self._route_note(lat, lon)
        if route_note:
            parts.append(route_note)
        parts.append(f"phase {phase}")
        return ", ".join(parts)

    def _route_note(self, lat: Optional[float], lon: Optional[float]) -> Optional[str]:
        """Where the aircraft is along the planned route, for the controller's
        situational awareness — e.g. 'en route EDLI→EDDG, 12 NM to run, next OSN
        4 NM ahead'. None unless a flight plan and live position are both known."""
        if self.flight_plan is None or lat is None or lon is None:
            return None
        try:
            p = self.flight_plan.progress(lat, lon)
        except Exception:
            return None
        fp = self.flight_plan
        bits = [f"flight plan {fp.summary()}",
                f"{p.dist_to_dest_nm:.0f} NM to {fp.destination.ident}"]
        if p.next_wp is not None and p.next_wp.is_airport is False and p.next_nm is not None:
            bits.append(f"next {p.next_wp.ident} {p.next_nm:.0f} NM")
        return ", ".join(bits)

    def _taxi_instruction(self, lat: Optional[float], lon: Optional[float]) -> str:
        """Ground taxi guidance for the LLM. With position + active runway + a
        taxi network, compute the real route and tell the controller to relay it
        verbatim. Otherwise, explicitly forbid inventing taxiways/holding points."""
        icao = self.current_airport.icao
        active_rwy = self._flat_conditions().get('active_runway', 'unknown')
        route = None
        if (lat is not None and lon is not None
                and active_rwy not in ('', 'unknown', None)):
            try:
                route = taxi.compute_route(self.current_airport, lat, lon, active_rwy)
            except Exception:
                route = None

        if route and route.taxiways:
            stand = f"stand {route.stand}" if route.stand else "the apron"
            return (
                f"TAXI ROUTE for {icao} (computed from the sim's taxi network — relay it "
                f"EXACTLY; do NOT invent, add, or substitute taxiways or holding points): "
                f"from {stand}, {route.describe()}. The ONLY valid taxiways for this "
                f"clearance are: {', '.join(route.taxiways)}. Active runway {active_rwy}."
            )

        # No computed route — make sure the controller does not fabricate one.
        stand = (route.stand if route else None) or self._flat_conditions().get('stand', '')
        stand_note = f" Aircraft is parked at stand {stand}." if stand else ""
        return (
            f"No published taxi route is available for {icao}. Give a SIMPLE, generic taxi "
            f"instruction (e.g. \"taxi to the holding point for runway {active_rwy}, follow "
            f"the green centreline\") and do NOT invent specific taxiway letters or "
            f"holding-point names.{stand_note} Active runway: {active_rwy}."
        )

    def _flat_conditions(self) -> dict:
        """Convert per-airport conditions dict to the flat format engine.respond() expects."""
        icao = self.current_airport.icao
        # Fall back to first available airport's conditions
        c = self.conditions.get(icao) or next(iter(self.conditions.values()), {})
        wind_dir = c.get('wind_dir', '?')
        wind_kts = c.get('wind_kts', '?')
        return {
            'wind':           f"{wind_dir}° / {wind_kts} kt",
            'qnh':            str(c.get('qnh', '??')),
            'vis':            f"{c.get('visibility_km', c.get('vis', '?'))} km",
            'time':           'check simulator clock',
            'active_runway':  c.get('active_runway', 'unknown'),
            'atis':           c.get('atis', ''),
            'stand':          c.get('stand', ''),
        }
