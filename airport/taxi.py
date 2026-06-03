"""
Ground taxi routing over X-Plane's apt.dat taxi-route network.

The network comes from `parse_apt_dat` (rows 1201 nodes / 1202 edges / 1204
active zones / 1300 ramp starts). Given the aircraft's position and the active
runway, `compute_route` snaps the aircraft onto the network and runs Dijkstra to
the nearest hold-short point for that runway, returning the real taxiway
designators along the way. The controller is then handed a concrete route to
phrase — it never has to invent taxiways or holding points.

Limitation: apt.dat has no AIP holding-point names (e.g. "W2"); a hold is
described by its runway ("hold short of runway 27R"), which is what the sim data
actually models.

Pure stdlib (heapq + math) — no third-party deps.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from airport.parser import Airport

# A runway edge is heavily penalised so routing stays on taxiways and only
# touches a runway when there is genuinely no other connection.
_RUNWAY_PENALTY = 80.0

# Beyond this, the nearest ramp start is not "where the aircraft is parked".
_STAND_MAX_M = 60.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _runway_tokens(runway: str) -> set[str]:
    """Normalise an active-runway string into the identifiers apt.dat uses.
    Accepts "27R", "09L/27R", "09L 27R", etc."""
    out: set[str] = set()
    for tok in runway.replace('/', ' ').replace(',', ' ').split():
        out.add(tok.strip().upper())
    return out


@dataclass
class TaxiRoute:
    runway: str               # the hold target, e.g. "27R"
    taxiways: List[str]       # ordered distinct designators, e.g. ["A", "F"]
    distance_m: float
    stand: Optional[str] = None        # nearest ramp/gate name, if the aircraft is on one
    active_zone: str = ""              # the 1204 zone of the hold edge, e.g. "09L,27R"

    def describe(self) -> str:
        if self.taxiways:
            via = ", ".join(self.taxiways)
            return f"taxi via {via} to hold short of runway {self.runway}"
        return f"taxi to hold short of runway {self.runway}"


def nearest_stand(airport: Airport, lat: float, lon: float) -> Optional[str]:
    """Name of the closest ramp start, or None if none is within ~60 m."""
    best, best_d = None, _STAND_MAX_M
    for rs in airport.ramp_starts:
        d = _haversine_m(lat, lon, rs.lat, rs.lon)
        if d < best_d:
            best, best_d = rs, d
    return (best.name or best.kind) if best else None


def _build_adjacency(airport: Airport) -> Dict[int, List[tuple]]:
    """node id → list of (neighbour id, weight, taxiway_name)."""
    nodes = airport.taxi_nodes
    adj: Dict[int, List[tuple]] = {nid: [] for nid in nodes}
    for e in airport.taxi_edges:
        n1, n2 = e.node1, e.node2
        if n1 not in nodes or n2 not in nodes:
            continue
        w = _haversine_m(nodes[n1].lat, nodes[n1].lon, nodes[n2].lat, nodes[n2].lon)
        if e.is_runway:
            w *= _RUNWAY_PENALTY
        adj[n1].append((n2, w, e.name))
        if not e.oneway:
            adj[n2].append((n1, w, e.name))
    return adj


def _hold_nodes(airport: Airport, runway: str) -> set[int]:
    """Taxiway-side nodes of edges whose 1204 active zone names this runway."""
    want = _runway_tokens(runway)
    holds: set[int] = set()
    for e in airport.taxi_edges:
        if e.is_runway or not e.active_zone:
            continue
        if want & _runway_tokens(e.active_zone):
            holds.add(e.node1)
            holds.add(e.node2)
    return holds


def _threshold(airport: Airport, runway: str) -> Optional[tuple]:
    """(lat, lon) of the named runway end, e.g. the 27R threshold — so a
    departure is routed to the correct end, not just any hold on the strip."""
    want = _runway_tokens(runway)
    for r in airport.runways:
        if r.name1.upper() in want:
            return (r.lat1, r.lon1)
        if r.name2.upper() in want:
            return (r.lat2, r.lon2)
    return None


def _nearest_node(airport: Airport, lat: float, lon: float) -> Optional[int]:
    best, best_d = None, math.inf
    for nid, n in airport.taxi_nodes.items():
        d = _haversine_m(lat, lon, n.lat, n.lon)
        if d < best_d:
            best, best_d = nid, d
    return best


def compute_route(airport: Airport, lat: float, lon: float,
                  runway: str) -> Optional[TaxiRoute]:
    """Compute a taxi route from the aircraft's position to a hold-short point
    for `runway`. Returns None if the airport has no taxi network, the runway is
    unknown, or no path exists."""
    if not airport.has_taxi_network() or not runway:
        return None

    start = _nearest_node(airport, lat, lon)
    if start is None:
        return None
    goals = _hold_nodes(airport, runway)
    if not goals:
        return None

    adj = _build_adjacency(airport)

    # Single-source Dijkstra from the snapped node over the whole network.
    dist: Dict[int, float] = {start: 0.0}
    prev: Dict[int, tuple] = {}        # node → (predecessor, taxiway_name)
    pq: List[tuple] = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        for v, w, name in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, name)
                heapq.heappush(pq, (nd, v))

    reachable = [g for g in goals if g in dist]
    if not reachable:
        return None

    # Hold nodes are tagged with the runway *pair* (e.g. "09L,27R") and run the
    # whole length of the strip. For a departure we want the hold at the cleared
    # runway's threshold end, so pick the reachable hold closest to that
    # threshold — not merely the nearest one, which could be a mid-field crossing
    # (or the opposite end). Falls back to nearest-by-taxi if the runway geometry
    # is unknown.
    thr = _threshold(airport, runway)
    nodes = airport.taxi_nodes
    if thr:
        reached = min(reachable,
                      key=lambda n: _haversine_m(thr[0], thr[1],
                                                 nodes[n].lat, nodes[n].lon))
    else:
        reached = min(reachable, key=lambda n: dist[n])

    # Reconstruct the taxiway sequence (collapse consecutive dups, drop blanks).
    seq: List[str] = []
    cur = reached
    while cur != start:
        u, name = prev[cur]
        seq.append(name)
        cur = u
    taxiways: List[str] = []
    for name in reversed(seq):
        if name and (not taxiways or taxiways[-1] != name):
            taxiways.append(name)

    zone = ""
    for e in airport.taxi_edges:
        if not e.is_runway and e.active_zone and reached in (e.node1, e.node2):
            if _runway_tokens(runway) & _runway_tokens(e.active_zone):
                zone = e.active_zone
                break

    return TaxiRoute(
        runway=runway,
        taxiways=taxiways,
        distance_m=dist[reached],
        stand=nearest_stand(airport, lat, lon),
        active_zone=zone,
    )
