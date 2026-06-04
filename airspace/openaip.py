"""
Load airspace data from OpenAIP.

OpenAIP publishes per-country airspace exports as public files in a Google Cloud
bucket — no API key needed. We map the current airport's ICAO prefix to an ISO
country code, download that one file once (≈3 MB for Germany), cache it next to
the other app caches, and parse it into an AirspaceDB.

Data © OpenAIP contributors, licensed CC BY-NC 4.0 (https://www.openaip.net/).
Attribution is surfaced in the app; the data stays free for everyone to use.

Everything degrades gracefully: no network, an unknown country, or a malformed
file all return None, and the caller falls back to its distance-based proxy.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from airspace.database import AirspaceDB, airspace_from_openaip

log = logging.getLogger(__name__)

# Public OpenAIP export bucket (verified against the live data, June 2026).
_GCS_BASE = "https://storage.googleapis.com/29f98e10-a489-4c82-ae5e-489dbcd4912f"
_CACHE_DIR = Path.home() / ".cache" / "xplane-vatc" / "airspace"

# ICAO location-indicator prefix → ISO 3166-1 alpha-2 (OpenAIP file key).
# Europe-focused, matching the project's VFR scope; extend as needed.
_ICAO_TO_ISO: dict[str, str] = {
    "ED": "de", "ET": "de",            # Germany
    "LO": "at", "LS": "ch", "LF": "fr",
    "EH": "nl", "EB": "be", "EL": "lu",
    "EG": "gb", "EI": "ie",
    "EK": "dk", "EN": "no", "ES": "se", "EF": "fi",
    "EP": "pl", "LK": "cz", "LZ": "sk", "LH": "hu",
    "LJ": "si", "LD": "hr", "LI": "it",
    "LE": "es", "GC": "es", "LP": "pt",
    "LG": "gr", "LR": "ro", "LB": "bg",
    "EV": "lv", "EY": "lt", "EE": "ee",
    "LM": "mt", "BI": "is",
}

# In-process cache so we parse each country file at most once per run.
_loaded: dict[str, Optional[AirspaceDB]] = {}


def country_for_icao(icao: str) -> Optional[str]:
    """ISO country code for an ICAO id (by its first two letters), or None."""
    if not icao or len(icao) < 2:
        return None
    return _ICAO_TO_ISO.get(icao[:2].upper())


def _cache_path(cc: str) -> Path:
    return _CACHE_DIR / f"{cc.lower()}_asp.json"


def _download_country(cc: str, dest: Path) -> bool:
    """Download {cc}_asp.json to dest atomically. Returns True on success."""
    url = f"{_GCS_BASE}/{cc.lower()}_asp.json"
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        log.info(f"Downloading OpenAIP airspace for {cc.upper()}: {url}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = resp.read()
        fd, tmp = tempfile.mkstemp(dir=str(_CACHE_DIR), suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, dest)
        log.info(f"OpenAIP airspace for {cc.upper()} cached ({len(payload) / 1e6:.1f} MB)")
        return True
    except Exception as e:
        log.warning(f"OpenAIP airspace download failed for {cc.upper()}: {e}")
        return False


def _parse(path: Path) -> Optional[AirspaceDB]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"OpenAIP airspace parse failed ({path.name}): {e}")
        return None
    items = data.get("features", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        log.warning(f"OpenAIP airspace file {path.name} is not a list")
        return None
    airspaces = [a for a in (airspace_from_openaip(o) for o in items) if a]
    log.info(f"Airspace: {len(airspaces)} volumes from {path.name}")
    return AirspaceDB(airspaces)


def load_country(cc: str, *, allow_download: bool = True) -> Optional[AirspaceDB]:
    """Load (and cache) the airspace DB for an ISO country code. Uses the cached
    file if present; downloads it once otherwise. Returns None on any failure."""
    cc = cc.lower()
    if cc in _loaded:
        return _loaded[cc]
    path = _cache_path(cc)
    if not path.exists():
        if not (allow_download and _download_country(cc, path)):
            _loaded[cc] = None
            return None
    db = _parse(path)
    _loaded[cc] = db
    return db


def load_for_airport(icao: str, *, allow_download: bool = True) -> Optional[AirspaceDB]:
    """Load the airspace DB for the country the airport sits in, or None."""
    cc = country_for_icao(icao)
    if not cc:
        log.info(f"Airspace: no OpenAIP country mapping for {icao!r} — skipping")
        return None
    return load_country(cc, allow_download=allow_download)
