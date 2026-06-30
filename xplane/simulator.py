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
    conditions: dict               # departure wx: qnh, wind_dir, wind_kts, visibility_km, atis
    lat: float
    lon: float
    alt_ft: float
    heading: float
    on_ground: bool
    destination_airport: Optional[str] = None
    destination_conditions: dict = None    # type: ignore[assignment]

    def __post_init__(self):
        if self.destination_conditions is None:
            self.destination_conditions = {}

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
            destination_airport=data.get("destination_airport"),
            destination_conditions=data.get("destination_conditions", {}),
        )

    @classmethod
    def from_file(cls, path: Path) -> "Scenario":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        d = {
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
        if self.destination_airport:
            d["destination_airport"] = self.destination_airport
        if self.destination_conditions:
            d["destination_conditions"] = self.destination_conditions
        return d


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
        # Seed weather from the scenario so the UI's aircraft panel and the
        # party-line render the same QNH/wind the ATC session was built with.
        cond = s.conditions or {}
        qnh = cond.get("qnh")
        st.qnh_inhg = float(qnh) / 33.8639 if qnh else 0.0
        st.wind_dir_deg = float(cond.get("wind_dir", 0) or 0)
        st.wind_speed_kts = float(cond.get("wind_kts", 0) or 0)
        # Encode ICAO chars
        icao = s.aircraft_icao.ljust(4)[:4]
        st._icao_chars = [float(ord(c)) for c in icao]
        # Encode tail number
        tail = s.callsign.ljust(10)[:10]
        st._tail_chars = [float(ord(c)) for c in tail]

    # ── Live mutation — the simulator stands in for X-Plane while debugging ──
    #
    # X-Plane feeds position/radio over UDP; here the backend drives the same
    # FlightState directly. Moving the aircraft or tuning a radio mutates the
    # one state the rest of the system already polls, so flight-plan progression,
    # airport adoption, handoff gating and the party-line all behave as if a real
    # sim were connected.

    def set_position(self, *, lat: Optional[float] = None, lon: Optional[float] = None,
                     alt_ft: Optional[float] = None, heading: Optional[float] = None,
                     on_ground: Optional[bool] = None, gs_kts: Optional[float] = None,
                     ias_kts: Optional[float] = None) -> None:
        """Set any subset of the aircraft's position/attitude. Unspecified fields
        are left where they are, so a caller can nudge one axis at a time."""
        st = self._state
        if lat is not None:
            st.lat = float(lat)
        if lon is not None:
            st.lon = float(lon)
        if alt_ft is not None:
            st.alt_ind_ft = float(alt_ft)
            st.elevation_m = float(alt_ft) * 0.3048
        if heading is not None:
            st.heading_mag = float(heading) % 360.0
            st.heading_true = st.heading_mag
        if on_ground is not None:
            st.on_ground = 1.0 if on_ground else 0.0
        if gs_kts is not None:
            st.groundspeed_ms = float(gs_kts) / 1.94384
            if ias_kts is None:
                st.ias_kts = float(gs_kts)   # rough IAS≈GS for the debug harness
        if ias_kts is not None:
            st.ias_kts = float(ias_kts)

    def tune(self, com: int, freq_mhz: float) -> None:
        """Set COM1 (com=1) or COM2 (com=2) in the same 10 kHz raw units X-Plane
        reports, so FlightState.comN_mhz reads back the tuned frequency."""
        raw = float(round(freq_mhz * 100))
        if com == 2:
            self._state.com2_raw = raw
        else:
            self._state.com1_raw = raw

    def set_transponder(self, code: int) -> None:
        self._state.transponder = float(int(code))

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
