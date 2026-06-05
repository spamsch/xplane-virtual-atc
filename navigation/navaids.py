"""
Navaid + fix lookup, parsed from X-Plane's earth_nav.dat and earth_fix.dat.

A flight plan names points by identifier — "OSN" (a VOR), "DENGI" (a fix). To
stage a journey we need their coordinates. This module streams the two nav-data
files X-Plane ships, keeps the points we can route to (VORs, NDBs, en-route
fixes), and resolves an identifier to a position.

earth_nav.dat row (whitespace-separated, leading space on every data row):
    type  lat  lon  elev  freq  range  slaved_var  IDENT  region/ENRT  ICAO  name…
  type 2 = NDB, 3 = VOR/VOR-DME/VORTAC. ILS/glideslope/marker rows (4–9) are
  airport-local and useless for en-route routing, so they're dropped.

earth_fix.dat row:
    lat  lon  IDENT  terminal_area  ICAO_region  …
  Identifiers repeat all over the world (there are dozens of "DENGI"-style
  five-letter fixes), so resolution always takes a reference point and returns
  the nearest match. A VOR/NDB is preferred over a fix when both share an ident
  at similar distance — that matches how a route like "EDLI OSN EDDG" means the
  Osnabrück VOR, not some far fix that happens to share the letters.

Parsed data is pickled next to the source file, like apt.dat.
"""

import logging
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# kind ranking for tie-breaks when an ident exists as both a navaid and a fix.
# Lower sorts first → preferred. A named VOR beats an NDB beats a plain fix.
_KIND_RANK = {"VOR": 0, "NDB": 1, "FIX": 2}


@dataclass(frozen=True)
class Navaid:
    ident: str
    lat: float
    lon: float
    kind: str          # "VOR" | "NDB" | "FIX"
    name: str          # human name (VOR/NDB) or the ident again (fix)
    region: str        # ICAO region code, e.g. "ED" — handy for disambiguation


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


class NavaidDB:
    """Identifier → navaid/fix positions, resolved nearest a reference point."""

    def __init__(self, navaids: Dict[str, List[Navaid]]):
        self._by_ident = navaids

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_ident.values())

    def all(self, ident: str) -> List[Navaid]:
        return self._by_ident.get(ident.upper().strip(), [])

    def resolve(self, ident: str,
                ref_lat: Optional[float] = None,
                ref_lon: Optional[float] = None) -> Optional[Navaid]:
        """The best match for `ident`. With a reference point, returns the
        nearest candidate (preferring a VOR/NDB over a fix when distances are
        close). Without one, returns the highest-ranked candidate, then the
        first parsed. None if the ident is unknown."""
        candidates = self.all(ident)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if ref_lat is None or ref_lon is None:
            return sorted(candidates, key=lambda n: _KIND_RANK.get(n.kind, 3))[0]

        # Nearest reference match, with a gentle bias toward real navaids: a VOR
        # within ~30 NM of the nearest fix still wins, so "OSN" near EDLI picks
        # the Osnabrück VOR rather than a marginally-closer airway fix.
        def score(n: Navaid) -> float:
            d = _haversine_nm(ref_lat, ref_lon, n.lat, n.lon)
            return d + _KIND_RANK.get(n.kind, 3) * 0.0   # distance first
        ranked = sorted(candidates, key=score)
        nearest = ranked[0]
        nearest_d = _haversine_nm(ref_lat, ref_lon, nearest.lat, nearest.lon)
        if nearest.kind == "FIX":
            for n in ranked:
                if n.kind in ("VOR", "NDB"):
                    if _haversine_nm(ref_lat, ref_lon, n.lat, n.lon) - nearest_d <= 30.0:
                        return n
                    break
        return nearest


# ------------------------------------------------------------------ #
# Parsing

_NAV_KIND = {"2": "NDB", "3": "VOR"}


def _parse_nav_dat(path: Path, out: Dict[str, List[Navaid]]) -> None:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 11 or parts[0] not in _NAV_KIND:
                continue
            try:
                lat, lon = float(parts[1]), float(parts[2])
            except ValueError:
                continue
            ident = parts[7]
            if not ident or ident == "----":
                continue
            region = parts[9] if len(parts) > 9 else ""
            name = " ".join(parts[10:]) if len(parts) > 10 else ident
            out.setdefault(ident, []).append(
                Navaid(ident=ident, lat=lat, lon=lon,
                       kind=_NAV_KIND[parts[0]], name=name, region=region))


def _parse_fix_dat(path: Path, out: Dict[str, List[Navaid]]) -> None:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except ValueError:
                continue   # header rows ("I", "1200 Version …") fail the float parse
            ident = parts[2]
            if not ident or len(ident) > 5 or not ident.isalnum():
                continue
            region = parts[4] if len(parts) > 4 else ""
            out.setdefault(ident, []).append(
                Navaid(ident=ident, lat=lat, lon=lon, kind="FIX",
                       name=ident, region=region))


def parse_nav_data(nav_path: Optional[Path], fix_path: Optional[Path],
                   cache_path: Optional[Path] = None) -> NavaidDB:
    """Parse earth_nav.dat + earth_fix.dat into a NavaidDB, caching the result.

    Either path may be None (parse only what's available). The cache lives next
    to nav_path when given, else fix_path; it's keyed on both files' mtimes so a
    nav-data update invalidates it."""
    anchor = nav_path or fix_path
    if anchor is None:
        return NavaidDB({})
    if cache_path is None:
        cache_path = anchor.with_suffix(".vatc_navaids_v1.pkl")

    newest_src = max(p.stat().st_mtime for p in (nav_path, fix_path) if p and p.exists())
    if cache_path.exists() and cache_path.stat().st_mtime >= newest_src:
        log.info(f"Loading navaid cache: {cache_path.name}")
        try:
            with open(cache_path, "rb") as f:
                return NavaidDB(pickle.load(f))
        except Exception as e:
            log.warning(f"Navaid cache unreadable ({e}); re-parsing")

    out: Dict[str, List[Navaid]] = {}
    if nav_path and nav_path.exists():
        log.info(f"Parsing {nav_path.name} (VOR/NDB) …")
        _parse_nav_dat(nav_path, out)
    if fix_path and fix_path.exists():
        log.info(f"Parsing {fix_path.name} (fixes) …")
        _parse_fix_dat(fix_path, out)
    log.info(f"Navaids ready: {sum(len(v) for v in out.values()):,} points "
             f"({len(out):,} idents)")

    try:
        with open(cache_path, "wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError as e:
        log.debug(f"Could not write navaid cache: {e}")
    return NavaidDB(out)
