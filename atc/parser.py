"""
Parse pilot radio calls into structured form.

Input examples:
  "Hanover Ground D-EIYD"
  "Hannover Ground, D-EIYD, request startup"
  "Hannover Ground, D-EIYD, Cessna 172, holding short runway 27, request taxi"
  "Tower, N12345, ready for departure runway 28"
"""

import re
from dataclasses import dataclass
from typing import Optional

_STATION_KEYWORDS: dict = {
    'ground': 'GND',
    'gnd': 'GND',
    'tower': 'TWR',
    'twr': 'TWR',
    'approach': 'APP',
    'departure': 'DEP',
    'dep': 'DEP',
    'clearance': 'CLD',
    'delivery': 'CLD',
    'atis': 'ATIS',
    'information': 'ATIS',
    'radio': 'RADIO',
    'unicom': 'CTAF',
}

# Matches European (D-EIYD, OE-ABC) and US (N12345, N123AB) registrations,
# plus airline-style callsigns (DLH123, BAW456A — ICAO codes are 3 letters)
_CALLSIGN_RE = re.compile(
    r'\b([A-Z]{1,2}-[A-Z]{2,4}|[A-Z]{2,3}\d{2,4}[A-Z]?|[A-Z]\d{4,5}[A-Z]?)\b'
)


@dataclass
class RadioCall:
    raw: str
    station: Optional[str]       # 'GND', 'TWR', 'APP', ...
    airport_name: Optional[str]  # text before station keyword
    callsign: Optional[str]      # pilot's registration/callsign
    message: Optional[str]       # everything after callsign


def parse(text: str) -> RadioCall:
    text = text.strip()
    upper = text.upper()

    station: Optional[str] = None
    airport_name: Optional[str] = None
    callsign: Optional[str] = None
    message: Optional[str] = None

    for keyword, code in _STATION_KEYWORDS.items():
        # Word-boundary match so "approaching" doesn't trigger "approach"
        m_kw = re.search(r'\b' + re.escape(keyword.upper()) + r'\b', upper)
        if m_kw:
            station = code
            before = text[:m_kw.start()].strip().rstrip(',').strip()
            if before:
                airport_name = before
            break

    m = _CALLSIGN_RE.search(upper)
    if m:
        callsign = m.group(0)
        after = text[m.end():].strip().lstrip(',').strip()
        if after:
            message = after

    return RadioCall(raw=text, station=station, airport_name=airport_name,
                     callsign=callsign, message=message)


def format_station(airport_name: str, station: str) -> str:
    """Produce the ATC station callsign, e.g. 'Hannover Ground'."""
    labels = {
        'GND': 'Ground', 'TWR': 'Tower', 'APP': 'Approach',
        'DEP': 'Departure', 'CLD': 'Clearance', 'ATIS': 'Information',
        'CTAF': 'Traffic', 'RADIO': 'Radio',
    }
    return f"{airport_name} {labels.get(station, station)}"
