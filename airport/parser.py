"""
Streaming parser for X-Plane's apt.dat file.

Relevant row codes:
  1   / 16 / 17 — airport / seaplane base / heliport header
  100           — land runway
  50–56         — frequencies (ATIS, CTAF, Clearance, Ground, Tower, Approach, Departure)
  99            — end of file

Frequency field is in units of 10 kHz:
  5-digit: 12175  → 121.75 MHz  (divide by 100)
  6-digit: 121750 → 121.750 MHz (divide by 1000)

Results are cached as a pickle file next to apt.dat to avoid re-parsing on
every startup (the full file can be 500 MB+).
"""

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

FREQ_TYPE_NAMES: Dict[int, str] = {
    50: 'ATIS',
    51: 'CTAF/Unicom',
    52: 'Clearance',
    53: 'Ground',
    54: 'Tower',
    55: 'Approach',
    56: 'Departure',
}


@dataclass
class Frequency:
    type_code: int
    type_name: str
    freq_mhz: float
    name: str


@dataclass
class Runway:
    name1: str      # e.g. "27L"
    name2: str      # e.g. "09R"
    lat1: float
    lon1: float
    lat2: float
    lon2: float
    width_m: float
    displaced1_m: float
    displaced2_m: float


@dataclass
class Airport:
    icao: str
    name: str
    elevation_ft: int
    lat: float
    lon: float
    frequencies: List[Frequency] = field(default_factory=list)
    runways: List[Runway] = field(default_factory=list)

    def freq(self, type_code: int) -> Optional[Frequency]:
        for f in self.frequencies:
            if f.type_code == type_code:
                return f
        return None

    def ground_freq(self) -> Optional[Frequency]:
        return self.freq(53)

    def tower_freq(self) -> Optional[Frequency]:
        return self.freq(54)

    def atis_freq(self) -> Optional[Frequency]:
        return self.freq(50)

    def clearance_freq(self) -> Optional[Frequency]:
        return self.freq(52)

    def freq_summary(self) -> str:
        lines = []
        for f in self.frequencies:
            lines.append(f"  {f.type_name}: {f.freq_mhz:.3f} MHz  ({f.name})")
        return '\n'.join(lines) if lines else '  (no frequencies in database)'

    def runway_summary(self) -> str:
        if not self.runways:
            return 'none'
        return ', '.join(f"{r.name1}/{r.name2}" for r in self.runways)


def _parse_freq_mhz(s: str) -> float:
    v = int(s)
    return v / 1000.0 if v > 99999 else v / 100.0


def parse_apt_dat(path: Path, cache_path: Optional[Path] = None) -> Dict[str, Airport]:
    if cache_path is None:
        cache_path = path.with_suffix('.vatc_cache.pkl')

    if cache_path.exists() and cache_path.stat().st_mtime >= path.stat().st_mtime:
        log.info(f"Loading apt.dat cache: {cache_path.name}")
        with open(cache_path, 'rb') as f:
            return pickle.load(f)

    log.info(f"Parsing {path} — this may take 30–60 seconds for large files...")
    airports: Dict[str, Airport] = {}
    current: Optional[Airport] = None
    count = 0

    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line or line[0] == '#':
                continue

            parts = line.split()
            if not parts:
                continue

            code = parts[0]

            if code == '99':
                break

            # Airport / seaplane base / heliport header
            if code in ('1', '16', '17'):
                if len(parts) < 6:
                    current = None
                    continue
                try:
                    elev = int(parts[1])
                except ValueError:
                    elev = 0
                icao = parts[4]
                name = ' '.join(parts[5:])
                current = Airport(icao=icao, name=name, elevation_ft=elev, lat=0.0, lon=0.0)
                airports[icao] = current
                count += 1
                if count % 10000 == 0:
                    log.info(f"  {count} airports parsed...")
                continue

            if current is None:
                continue

            # Land runway
            if code == '100' and len(parts) >= 21:
                try:
                    width = float(parts[1])
                    name1, lat1, lon1 = parts[8], float(parts[9]), float(parts[10])
                    disp1 = float(parts[11])
                    name2, lat2, lon2 = parts[17], float(parts[18]), float(parts[19])
                    disp2 = float(parts[20])
                    rwy = Runway(name1=name1, name2=name2,
                                 lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2,
                                 width_m=width, displaced1_m=disp1, displaced2_m=disp2)
                    current.runways.append(rwy)
                    if current.lat == 0.0 and current.lon == 0.0:
                        current.lat = (lat1 + lat2) / 2
                        current.lon = (lon1 + lon2) / 2
                except (ValueError, IndexError):
                    pass
                continue

            # Frequencies — legacy codes 50-56 and X-Plane 12 codes 1050-1056
            if code in ('50','51','52','53','54','55','56',
                        '1050','1051','1052','1053','1054','1055','1056') and len(parts) >= 3:
                try:
                    raw_code = int(code)
                    tc = raw_code - 1000 if raw_code > 100 else raw_code
                    current.frequencies.append(Frequency(
                        type_code=tc,
                        type_name=FREQ_TYPE_NAMES.get(tc, code),
                        freq_mhz=_parse_freq_mhz(parts[1]),
                        name=' '.join(parts[2:]),
                    ))
                except (ValueError, IndexError):
                    pass

    airports = {k: v for k, v in airports.items()
                if v.lat != 0.0 or v.lon != 0.0}
    log.info(f"Parsed {len(airports)} airports with position data")

    with open(cache_path, 'wb') as f:
        pickle.dump(airports, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info(f"Cached to {cache_path.name}")

    return airports
