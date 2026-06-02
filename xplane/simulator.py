"""
ScenarioSimulator — a FlightDataSource backed by a JSON scenario file
rather than X-Plane UDP packets.

Scenario JSON format:
{
  "name": "EDDV Standard Departure",
  "description": "...",
  "aircraft": {"icao": "C172", "callsign": "D-EIYD"},
  "departure_airport": "EDDV",
  "conditions": {
    "qnh": 1018, "wind_dir": 270, "wind_kts": 8,
    "visibility_km": 10, "atis": "Charlie"
  },
  "position": {
    "lat": 52.461, "lon": 9.685,  // omit to auto-place at airport centroid
    "alt_ft": 183,
    "heading": 270,
    "on_ground": true
  }
}
"""

import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xplane.connector import FlightState

log = logging.getLogger(__name__)


@dataclass
class Scenario:
    name: str
    description: str
    aircraft_icao: str
    callsign: str
    departure_airport: str
    conditions: dict          # qnh, wind_dir, wind_kts, visibility_km, atis
    lat: float
    lon: float
    alt_ft: float
    heading: float
    on_ground: bool

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        acft = data["aircraft"]
        pos = data.get("position", {})
        return cls(
            name=data.get("name", "Unnamed Scenario"),
            description=data.get("description", ""),
            aircraft_icao=acft["icao"],
            callsign=acft["callsign"],
            departure_airport=data["departure_airport"],
            conditions=data.get("conditions", {}),
            lat=pos.get("lat", 0.0),
            lon=pos.get("lon", 0.0),
            alt_ft=pos.get("alt_ft", 0.0),
            heading=pos.get("heading", 270.0),
            on_ground=pos.get("on_ground", True),
        )

    @classmethod
    def from_file(cls, path: Path) -> "Scenario":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "aircraft": {"icao": self.aircraft_icao, "callsign": self.callsign},
            "departure_airport": self.departure_airport,
            "conditions": self.conditions,
            "position": {
                "lat": self.lat, "lon": self.lon, "alt_ft": self.alt_ft,
                "heading": self.heading, "on_ground": self.on_ground,
            },
        }


class ScenarioSimulator:
    """
    Implements the FlightDataSource Protocol.
    Returns a static FlightState derived from the loaded scenario.
    """

    def __init__(self, scenario: Optional[Scenario] = None):
        self._scenario = scenario
        self._state = FlightState()
        if scenario:
            self._apply(scenario)

    def load(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._apply(scenario)
        log.info(f"Scenario loaded: {scenario.name}")

    def _apply(self, s: Scenario) -> None:
        st = self._state
        st.lat = s.lat
        st.lon = s.lon
        st.elevation_m = s.alt_ft * 0.3048
        st.alt_ind_ft = s.alt_ft
        st.heading_mag = s.heading
        st.heading_true = s.heading
        st.on_ground = 1.0 if s.on_ground else 0.0
        st.ias_kts = 0.0
        st.groundspeed_ms = 0.0
        st.paused = 0.0
        # Encode ICAO chars
        icao = s.aircraft_icao.ljust(4)[:4]
        st._icao_chars = [float(ord(c)) for c in icao]
        # Encode tail number
        tail = s.callsign.ljust(10)[:10]
        st._tail_chars = [float(ord(c)) for c in tail]

    @property
    def state(self) -> FlightState:
        return copy.deepcopy(self._state)

    @property
    def scenario(self) -> Optional[Scenario]:
        return self._scenario

    @property
    def conditions(self) -> dict:
        if self._scenario:
            return self._scenario.conditions
        return {}

    def start(self) -> None:
        log.info("ScenarioSimulator started")

    def stop(self) -> None:
        log.info("ScenarioSimulator stopped")
