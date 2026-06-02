"""
FlightDataSource Protocol — the interface both XPlaneConnector and
ScenarioSimulator must satisfy. Enforced by Python's structural subtyping.
"""

from typing import Protocol, runtime_checkable
from xplane.connector import FlightState


@runtime_checkable
class FlightDataSource(Protocol):
    @property
    def state(self) -> FlightState: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
