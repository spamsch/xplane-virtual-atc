"""
Airspace model + spatial query.

Controlled airspace (CTR, TMA, …) isn't in X-Plane's data, so the live sim can't
tell the controller "you're in the Hannover CTR". This module fills that gap from
OpenAIP data (loaded by airspace/openaip.py): a list of polygonal volumes, each
with a class and vertical limits, plus a point-in-polygon query against the
aircraft's live position and altitude.

The geometry test is plain ray-casting on the outer ring with a bounding-box
pre-reject — fast enough to run per transmission over a country's ~750 volumes,
and stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── OpenAIP enums → human labels ─────────────────────────────────────────────
# type: the kind of airspace. Only the ones that matter to a VFR controller are
# named; anything else renders as "airspace".
AIRSPACE_TYPE: dict[int, str] = {
    0:  "Other",
    1:  "Restricted",
    2:  "Danger",
    3:  "Prohibited",
    4:  "CTR",
    5:  "TMZ",
    6:  "RMZ",
    7:  "TMA",
    10: "FIR",
    11: "UIR",
    12: "ADIZ",
    13: "ATZ",
    26: "CTA",
    33: "FIS",
}

# icaoClass: airspace class A–G. 7+ are special-use / unclassified.
ICAO_CLASS: dict[int, str] = {0: "A", 1: "B", 2: "C", 3: "D", 4: "E", 5: "F", 6: "G"}

CTR_TYPE = 4   # the control-zone type — the one the handover logic cares about


def _limit_to_ft(limit: Optional[dict]) -> Optional[float]:
    """Normalise an OpenAIP vertical limit to feet. unit: 0 = metres, 1 = feet,
    6 = flight level (value ×100 ft). referenceDatum is kept on the Airspace; we
    treat GND-referenced surface limits as ~0 for the ceiling/floor comparison."""
    if not limit:
        return None
    try:
        v = float(limit.get("value", 0))
    except (TypeError, ValueError):
        return None
    unit = limit.get("unit")
    if unit == 6:
        return v * 100.0     # FL → ft
    if unit == 0:
        return v * 3.28084   # m → ft
    return v                 # feet


@dataclass
class Airspace:
    name: str
    type_code: int
    icao_class: Optional[int]
    lower_ft: Optional[float]
    upper_ft: Optional[float]
    lower_ref: Optional[int]
    upper_ref: Optional[int]
    ring: list[tuple[float, float]]    # outer ring as (lon, lat) pairs
    # bbox for quick reject: (min_lon, min_lat, max_lon, max_lat)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def type_name(self) -> str:
        return AIRSPACE_TYPE.get(self.type_code, "airspace")

    @property
    def class_name(self) -> Optional[str]:
        return ICAO_CLASS.get(self.icao_class) if self.icao_class is not None else None

    @property
    def is_ctr(self) -> bool:
        return self.type_code == CTR_TYPE

    def contains_point(self, lon: float, lat: float) -> bool:
        """Ray-cast point-in-polygon on the outer ring, with a bbox pre-reject."""
        mnx, mny, mxx, mxy = self.bbox
        if not (mnx <= lon <= mxx and mny <= lat <= mxy):
            return False
        ring = self.ring
        n = len(ring)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > lat) != (yj > lat):
                x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
                if lon < x_cross:
                    inside = not inside
            j = i
        return inside

    def contains(self, lon: float, lat: float, alt_ft: Optional[float] = None,
                 buffer_ft: float = 200.0) -> bool:
        """Horizontal containment, plus a lenient vertical check when alt is known.
        Surface (GND/0) floors and missing limits never exclude — only a clear
        ceiling bust or being below a non-surface floor does."""
        if not self.contains_point(lon, lat):
            return False
        if alt_ft is None:
            return True
        if self.upper_ft is not None and alt_ft > self.upper_ft + buffer_ft:
            return False
        # A floor above the surface (lower_ref != GND) can exclude a low aircraft.
        if (self.lower_ft is not None and self.lower_ref not in (0, None)
                and self.lower_ft > 50 and alt_ft < self.lower_ft - buffer_ft):
            return False
        return True

    def describe(self) -> str:
        """Short controller-readable summary, e.g. 'Hannover CTR (Class D, SFC-2500 ft)'."""
        cls = f"Class {self.class_name}" if self.class_name else self.type_name
        lo = "SFC" if (self.lower_ft in (None, 0) or self.lower_ref == 0) else f"{int(self.lower_ft)} ft"
        hi = f"{int(self.upper_ft)} ft" if self.upper_ft is not None else "?"
        return f"{self.name} ({cls}, {lo}-{hi})"


def _bbox(ring: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def airspace_from_openaip(obj: dict) -> Optional[Airspace]:
    """Build an Airspace from one OpenAIP airspace record, or None if it has no
    usable polygon."""
    geom = obj.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if gtype == "Polygon":
        outer = coords[0]
    elif gtype == "MultiPolygon":
        outer = coords[0][0]
    else:
        return None
    ring = [(float(p[0]), float(p[1])) for p in outer if len(p) >= 2]
    if len(ring) < 3:
        return None
    return Airspace(
        name=str(obj.get("name", "")).strip() or "Unnamed",
        type_code=int(obj.get("type", 0)),
        icao_class=obj.get("icaoClass"),
        lower_ft=_limit_to_ft(obj.get("lowerLimit")),
        upper_ft=_limit_to_ft(obj.get("upperLimit")),
        lower_ref=(obj.get("lowerLimit") or {}).get("referenceDatum"),
        upper_ref=(obj.get("upperLimit") or {}).get("referenceDatum"),
        ring=ring,
        bbox=_bbox(ring),
    )


# Priority for "which controlled airspace are you in" — lower number wins.
_CONTROLLING_PRIORITY: dict[int, int] = {4: 0, 13: 1, 7: 2, 26: 3, 10: 4, 11: 5}


class AirspaceDB:
    def __init__(self, airspaces: list[Airspace]):
        self._all = list(airspaces)

    def __len__(self) -> int:
        return len(self._all)

    @property
    def all(self) -> list[Airspace]:
        return list(self._all)

    def at(self, lat: float, lon: float, alt_ft: Optional[float] = None) -> list[Airspace]:
        """Every airspace volume containing the point (and altitude, if given),
        innermost-first (lowest ceiling first)."""
        hits = [a for a in self._all if a.contains(lon, lat, alt_ft)]
        hits.sort(key=lambda a: (a.upper_ft if a.upper_ft is not None else 1e9))
        return hits

    def in_ctr(self, lat: float, lon: float, alt_ft: Optional[float] = None) -> Optional[Airspace]:
        """The control zone (CTR) containing the point, if any."""
        for a in self.at(lat, lon, alt_ft):
            if a.is_ctr:
                return a
        return None

    def controlling(self, lat: float, lon: float,
                    alt_ft: Optional[float] = None) -> Optional[Airspace]:
        """The most relevant *controlled* airspace at the point: CTR before ATZ
        before TMA before CTA before FIR. Returns None if only uncontrolled or
        special-use volumes are present."""
        candidates = [a for a in self.at(lat, lon, alt_ft) if a.type_code in _CONTROLLING_PRIORITY]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (_CONTROLLING_PRIORITY[a.type_code],
                                       a.upper_ft if a.upper_ft is not None else 1e9))
        return candidates[0]
