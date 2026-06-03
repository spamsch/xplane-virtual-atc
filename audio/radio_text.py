"""
Radio text normalizer — turns compact ATC text into its spoken radio form
*before* TTS, so any backend (ElevenLabs flash has no pronunciation controls)
says it like a controller would.

Only the spoken copy is normalized; the text shown in the UI stays compact.

Conversions (deliberately narrow, to avoid mangling normal words / German names):
  D-EIYD                  → Delta Echo India Yankee Delta
  runway 27R              → runway two seven Right
  squawk 3674             → squawk three six seven four
  QNH 1018                → QNH one zero one eight
  120.180                 → one two zero decimal one eight zero
  270 degrees / 8 knots   → two seven zero degrees / eight knots
  taxiway L / holding point B1 → taxiway Lima / holding point Bravo one
"""

import re

NATO = {
    'A': 'Alpha', 'B': 'Bravo', 'C': 'Charlie', 'D': 'Delta', 'E': 'Echo',
    'F': 'Foxtrot', 'G': 'Golf', 'H': 'Hotel', 'I': 'India', 'J': 'Juliet',
    'K': 'Kilo', 'L': 'Lima', 'M': 'Mike', 'N': 'November', 'O': 'Oscar',
    'P': 'Papa', 'Q': 'Quebec', 'R': 'Romeo', 'S': 'Sierra', 'T': 'Tango',
    'U': 'Uniform', 'V': 'Victor', 'W': 'Whiskey', 'X': 'X-ray',
    'Y': 'Yankee', 'Z': 'Zulu',
}
DIGITS = {
    '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
    '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'niner',
}
_SIDE = {'L': 'Left', 'C': 'Center', 'R': 'Right'}


def _spell(token: str) -> str:
    """Expand an alphanumeric token to NATO letters + spoken digits."""
    out = []
    for ch in token:
        if ch.isalpha():
            out.append(NATO.get(ch.upper(), ch))
        elif ch.isdigit():
            out.append(DIGITS[ch])
        # drop hyphens / other separators
    return ' '.join(out)


def _digits(num: str) -> str:
    return ' '.join(DIGITS[d] for d in num if d.isdigit())


# Aircraft registration / callsign: 1-2 letters, hyphen, 2-6 alphanumerics
# (D-EIYD, D-ABCD, N-12345). Hyphen is required so we never touch plain words.
_REG_RE = re.compile(r'\b([A-Z]{1,2}-[A-Z0-9]{2,6})\b')
_FREQ_RE = re.compile(r'\b(1[0-9]{2})\.(\d{2,3})\b')
_RUNWAY_RE = re.compile(r'\brunway\s+(\d{1,2})([LCR])?\b', re.IGNORECASE)
_SQUAWK_RE = re.compile(r'\b(squawk)\s+(\d{4})\b', re.IGNORECASE)
_QNH_RE = re.compile(r'\b(QNH|QFE)\s+(\d{3,4})\b', re.IGNORECASE)
_NUM_UNIT_RE = re.compile(r'\b(\d{1,3})\s+(degrees|knots|feet|knot)\b', re.IGNORECASE)
# Taxiway / holding-point identifiers: a letter optionally followed by a number
_TAXI_RE = re.compile(
    r'\b(taxiways?|holding\s+point|via)\s+([A-Z](?:\d{1,2})?'
    r'(?:\s+(?:and|,)\s+[A-Z](?:\d{1,2})?)*)\b'
)


def _taxi_sub(m: re.Match) -> str:
    lead = m.group(1)
    # Expand each letter[number] id but keep connectors ("and", ",") intact
    def repl(tok: re.Match) -> str:
        return _spell(tok.group(0))
    ids = re.sub(r'[A-Z]\d{0,2}', repl, m.group(2))
    return f"{lead} {ids}"


def to_spoken(text: str) -> str:
    """Return the spoken radio form of an ATC transmission."""
    text = _REG_RE.sub(lambda m: _spell(m.group(1)), text)
    text = _FREQ_RE.sub(lambda m: f"{_digits(m.group(1))} decimal {_digits(m.group(2))}", text)
    text = _RUNWAY_RE.sub(
        lambda m: f"runway {_digits(m.group(1))}"
                  + (f" {_SIDE[m.group(2).upper()]}" if m.group(2) else ""),
        text)
    text = _SQUAWK_RE.sub(lambda m: f"{m.group(1)} {_digits(m.group(2))}", text)
    text = _QNH_RE.sub(lambda m: f"{m.group(1)} {_digits(m.group(2))}", text)
    text = _NUM_UNIT_RE.sub(lambda m: f"{_digits(m.group(1))} {m.group(2)}", text)
    text = _TAXI_RE.sub(_taxi_sub, text)
    return text
