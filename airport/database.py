"""
Spatial airport database. Answers two questions:
  1. What airport am I at? (nearest within N nm)
  2. Which runway am I on? (point-in-rectangle test)

Uses a 1-degree lat/lon grid for fast candidate filtering.
"""

import math
from typing import Dict, List, Optional, Tuple

from .parser import Airport, Runway


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065  # Earth radius in nautical miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _point_on_runway(rwy: Runway, lat: float, lon: float, margin_m: float) -> bool:
    """Rectangle test in a local ENU frame centered on the runway midpoint."""
    clat = (rwy.lat1 + rwy.lat2) / 2
    m_per_lat = 111320.0
    m_per_lon = 111320.0 * math.cos(math.radians(clat))

    # Runway vector
    dx = (rwy.lon2 - rwy.lon1) * m_per_lon
    dy = (rwy.lat2 - rwy.lat1) * m_per_lat
    length = math.hypot(dx, dy)
    if length < 1.0:
        return False

    # Unit vectors: along runway (ux, uy) and across (vx, vy)
    ux, uy = dx / length, dy / length
    vx, vy = -uy, ux

    # Point relative to threshold 1
    px = (lon - rwy.lon1) * m_per_lon
    py = (lat - rwy.lat1) * m_per_lat

    along = px * ux + py * uy
    across = px * vx + py * vy

    return (-margin_m <= along <= length + margin_m) and (abs(across) <= rwy.width_m / 2 + margin_m)


class AirportDB:
    def __init__(self, airports: Dict[str, Airport], margin_nm: float = 5.0):
        self._airports = airports
        self._margin_nm = margin_nm
        # 1-degree grid: cell → list of ICAO codes
        self._grid: Dict[Tuple[int, int], List[str]] = {}
        for icao, ap in airports.items():
            cell = (int(math.floor(ap.lat)), int(math.floor(ap.lon)))
            self._grid.setdefault(cell, []).append(icao)

    def nearest(self, lat: float, lon: float,
                max_dist_nm: Optional[float] = None) -> Optional[Airport]:
        limit = max_dist_nm if max_dist_nm is not None else self._margin_nm
        best_dist = limit
        best: Optional[Airport] = None

        # Check 3×3 cells centred on position (enough for airports within ~100 nm)
        for dlat in range(-2, 3):
            for dlon in range(-2, 3):
                cell = (int(math.floor(lat)) + dlat, int(math.floor(lon)) + dlon)
                for icao in self._grid.get(cell, []):
                    ap = self._airports[icao]
                    d = haversine_nm(lat, lon, ap.lat, ap.lon)
                    if d < best_dist:
                        best_dist = d
                        best = ap

        return best

    def get(self, icao: str) -> Optional[Airport]:
        return self._airports.get(icao.upper())

    def which_runway(self, airport: Airport, lat: float, lon: float,
                     margin_m: float = 40.0) -> Optional[Runway]:
        for rwy in airport.runways:
            if _point_on_runway(rwy, lat, lon, margin_m):
                return rwy
        return None
