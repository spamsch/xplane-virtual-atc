"""
FlightPlan — parse an ICAO route into a staged VFR journey.

A route is a string like "EDLI OSN EDDG" (separators: spaces, arrows, DCT). The
first and last tokens are airports; everything between is a navaid or fix. We
resolve each point to coordinates, chaining the reference forward so an ambiguous
identifier picks the candidate nearest the previous point.

The plan then knows enough to stage the flight the way real VFR ops run:
  - departure: controlled → Ground/Tower; uncontrolled → self-announce on the
    field's CTAF/Info frequency, no clearances.
  - en route:  a Flight Information Service (FIS) following you between the two
    fields, named for the FIR you're in.
  - arrival:   controlled → Tower then Ground; uncontrolled → CTAF again.

"Controlled" is decided from the published frequencies, not the apt.dat Tower
flag alone — many German AFIS fields (Bielefeld "Info") carry a Tower-coded
frequency but have no real control service. A field counts as controlled only if
it also publishes Ground, Approach, or Departure. Callers can override per ICAO.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from airport.parser import Airport
from airport.database import AirportDB
from navigation.navaids import NavaidDB


class RouteError(ValueError):
    """A route string couldn't be parsed into a valid plan."""


# Frequency type codes that signal a genuinely controlled field.
_CONTROLLED_FREQ_TYPES = {53, 55, 56}   # Ground, Approach, Departure
_TOWER = 54
_CTAF = 51
_INFO_ATIS = 50


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def is_controlled(airport: Airport) -> bool:
    """True if the field has a real control service (Ground/Approach/Departure).
    A lone Tower-coded frequency (common for German AFIS/Info fields) does not
    count — those are uncontrolled."""
    return any(f.type_code in _CONTROLLED_FREQ_TYPES for f in airport.frequencies)


def field_service_freq(airport: Airport) -> Optional[float]:
    """The frequency a VFR pilot self-announces on at an uncontrolled field:
    its CTAF/Unicom, else its (AFIS) Tower/Info, else ATIS. None if it lists
    nothing usable."""
    for tc in (_CTAF, _TOWER, _INFO_ATIS):
        f = airport.freq(tc)
        if f:
            return f.freq_mhz
    return airport.frequencies[0].freq_mhz if airport.frequencies else None


# ------------------------------------------------------------------ #
# Region FIS — who provides the en-route information service.
#
# German lower airspace splits into two FIRs: Bremen Information in the north,
# Langen Information in the south, divided roughly along 51.0–51.5°N. The exact
# frequency is sector-dependent (a dozen+ per FIR); we publish one representative
# working frequency per FIR as a sane default. Outside Germany we fall back to a
# generic "Information" with no preset frequency.

@dataclass(frozen=True)
class FISStation:
    callsign: str            # e.g. "Bremen Information"
    freq_mhz: Optional[float]


_DE_BREMEN = FISStation("Bremen Information", 120.025)
_DE_LANGEN = FISStation("Langen Information", 120.575)


def fis_station_for(lat: float, lon: float) -> FISStation:
    """The FIS station appropriate to a position. German FIRs are split N/S;
    elsewhere returns a generic Information service."""
    if 47.0 <= lat <= 55.5 and 5.0 <= lon <= 15.5:   # roughly Germany
        return _DE_BREMEN if lat >= 51.3 else _DE_LANGEN
    return FISStation("Information", None)


# ------------------------------------------------------------------ #
# Model

@dataclass
class Waypoint:
    ident: str
    lat: float
    lon: float
    kind: str                       # "AIRPORT" | "VOR" | "NDB" | "FIX"
    name: str = ""
    airport: Optional[Airport] = None      # set when kind == "AIRPORT"
    controlled: Optional[bool] = None      # set when kind == "AIRPORT"

    @property
    def is_airport(self) -> bool:
        return self.kind == "AIRPORT"

    def label(self) -> str:
        if self.is_airport:
            tag = "controlled" if self.controlled else "uncontrolled"
            return f"{self.ident} ({self.name}, {tag})"
        return f"{self.ident} ({self.name or self.kind})"


@dataclass
class RouteProgress:
    """Where the aircraft is along the planned route, from a live position."""
    leg_index: int                  # index of the leg currently being flown
    flown_nm: float                 # great-circle distance covered from departure
    remaining_nm: float             # straight-line distance still to fly to dest
    total_nm: float
    dist_from_dep_nm: float         # direct distance from the departure field
    dist_to_dest_nm: float          # direct distance to the destination field
    nearest: Waypoint               # closest plan point to the aircraft
    nearest_nm: float
    next_wp: Optional[Waypoint]     # next plan point ahead on the route
    next_nm: Optional[float]
    bearing_to_dest: float

    @property
    def fraction(self) -> float:
        return self.flown_nm / self.total_nm if self.total_nm > 0 else 0.0


@dataclass
class FlightPlan:
    route: str
    waypoints: List[Waypoint]              # departure … intermediate … destination
    fis: FISStation

    @property
    def departure(self) -> Waypoint:
        return self.waypoints[0]

    @property
    def destination(self) -> Waypoint:
        return self.waypoints[-1]

    @property
    def intermediate(self) -> List[Waypoint]:
        return self.waypoints[1:-1]

    @property
    def total_nm(self) -> float:
        return sum(
            _haversine_nm(a.lat, a.lon, b.lat, b.lon)
            for a, b in zip(self.waypoints, self.waypoints[1:])
        )

    def summary(self) -> str:
        return " → ".join(w.ident for w in self.waypoints)

    def describe(self) -> str:
        legs = "  ".join(w.label() for w in self.waypoints)
        return f"{self.summary()}  ({self.total_nm:.0f} NM)\n  {legs}\n  En route: {self.fis.callsign}"

    def progress(self, lat: float, lon: float) -> RouteProgress:
        """Resolve a live position into route progress. flown_nm is measured by
        projecting onto the nearest leg, so it advances smoothly even when the
        aircraft is off the centreline."""
        wps = self.waypoints
        cum = [0.0]
        for a, b in zip(wps, wps[1:]):
            cum.append(cum[-1] + _haversine_nm(a.lat, a.lon, b.lat, b.lon))
        total = cum[-1]

        # Nearest plan point.
        nearest_i = min(range(len(wps)),
                        key=lambda i: _haversine_nm(lat, lon, wps[i].lat, wps[i].lon))
        nearest = wps[nearest_i]
        nearest_nm = _haversine_nm(lat, lon, nearest.lat, nearest.lon)

        # Which leg are we on, and how far along it — pick the leg whose start
        # gives the best forward projection.
        best_leg, best_flown = 0, 0.0
        best_off = float("inf")
        for i in range(len(wps) - 1):
            a, b = wps[i], wps[i + 1]
            leg_nm = cum[i + 1] - cum[i]
            if leg_nm < 1e-6:
                continue
            # Project the aircraft onto leg a→b using an equirectangular frame.
            t, off = _project(lat, lon, a, b)
            t = max(0.0, min(1.0, t))
            if off < best_off:
                best_off = off
                best_leg = i
                best_flown = cum[i] + t * leg_nm

        dep, dest = wps[0], wps[-1]
        next_wp = wps[best_leg + 1] if best_leg + 1 < len(wps) else None
        return RouteProgress(
            leg_index=best_leg,
            flown_nm=best_flown,
            remaining_nm=max(0.0, total - best_flown),
            total_nm=total,
            dist_from_dep_nm=_haversine_nm(lat, lon, dep.lat, dep.lon),
            dist_to_dest_nm=_haversine_nm(lat, lon, dest.lat, dest.lon),
            nearest=nearest,
            nearest_nm=nearest_nm,
            next_wp=next_wp,
            next_nm=_haversine_nm(lat, lon, next_wp.lat, next_wp.lon) if next_wp else None,
            bearing_to_dest=_bearing_deg(lat, lon, dest.lat, dest.lon),
        )


def _project(lat: float, lon: float, a: Waypoint, b: Waypoint):
    """Fraction t along segment a→b for the foot of perpendicular from (lat,lon),
    plus the perpendicular distance (NM-ish), in a local equirectangular frame."""
    clat = math.radians((a.lat + b.lat) / 2)
    def xy(la, lo):
        return (math.radians(lo) * math.cos(clat), math.radians(la))
    ax, ay = xy(a.lat, a.lon)
    bx, by = xy(b.lat, b.lon)
    px, py = xy(lat, lon)
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / denom
    # Perpendicular offset in radians → NM (Earth radius 3440 NM).
    fx, fy = ax + t * dx, ay + t * dy
    off = math.hypot(px - fx, py - fy) * 3440.065
    return t, off


# ------------------------------------------------------------------ #
# Parsing

_SEP_RE = re.compile(r"[\s,]+|->|→|=>")
_DROP_TOKENS = {"DCT", "VFR", "IFR"}


def _tokenize(route: str) -> List[str]:
    raw = _SEP_RE.split(route.strip().upper())
    return [t for t in raw if t and t not in _DROP_TOKENS]


def parse_route(route: str, airport_db: AirportDB, navaid_db: Optional[NavaidDB],
                controlled_overrides: Optional[Dict[str, bool]] = None) -> FlightPlan:
    """Parse a route string into a FlightPlan.

    Args:
        route: e.g. "EDLI OSN EDDG" (also accepts arrows / DCT separators).
        airport_db: resolves the first and last tokens (ICAO airports).
        navaid_db: resolves intermediate VOR/NDB/fix idents. May be None — then
            intermediate points are dropped (the plan still stages dep→FIS→dest).
        controlled_overrides: {ICAO: bool} forcing a field's control status,
            from the UI (so a user can correct the auto-guess).

    Raises RouteError if there are fewer than two tokens or an endpoint airport
    can't be found."""
    overrides = {k.upper(): v for k, v in (controlled_overrides or {}).items()}
    tokens = _tokenize(route)
    if len(tokens) < 2:
        raise RouteError("A route needs at least a departure and a destination, "
                         'e.g. "EDLI EDDG" or "EDLI OSN EDDG".')

    dep_icao, dest_icao = tokens[0], tokens[-1]
    dep_ap = airport_db.get(dep_icao)
    dest_ap = airport_db.get(dest_icao)
    missing = [ic for ic, ap in ((dep_icao, dep_ap), (dest_icao, dest_ap)) if ap is None]
    if missing:
        raise RouteError(f"Unknown airport(s): {', '.join(missing)}. "
                         "Use ICAO codes (e.g. EDLI, EDDG).")

    def airport_wp(ap: Airport) -> Waypoint:
        ctrl = overrides.get(ap.icao)
        if ctrl is None:
            ctrl = is_controlled(ap)
        return Waypoint(ident=ap.icao, lat=ap.lat, lon=ap.lon, kind="AIRPORT",
                        name=ap.name, airport=ap, controlled=ctrl)

    waypoints: List[Waypoint] = [airport_wp(dep_ap)]
    ref_lat, ref_lon = dep_ap.lat, dep_ap.lon

    for tok in tokens[1:-1]:
        nav = navaid_db.resolve(tok, ref_lat, ref_lon) if navaid_db else None
        if nav is None:
            # Unknown intermediate point — skip it rather than fail the whole
            # plan; the journey still stages correctly end to end.
            continue
        waypoints.append(Waypoint(ident=nav.ident, lat=nav.lat, lon=nav.lon,
                                  kind=nav.kind, name=nav.name))
        ref_lat, ref_lon = nav.lat, nav.lon

    waypoints.append(airport_wp(dest_ap))

    # FIS named for the midpoint of the route (whichever FIR it mostly sits in).
    mid_lat = sum(w.lat for w in waypoints) / len(waypoints)
    mid_lon = sum(w.lon for w in waypoints) / len(waypoints)
    fis = fis_station_for(mid_lat, mid_lon)

    return FlightPlan(route=route.strip(), waypoints=waypoints, fis=fis)
