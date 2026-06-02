import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / 'data.json'
_DB: dict = {}


@dataclass
class AircraftPerf:
    icao: str
    name: str
    category: str      # SEP, MEP, SET, MET, JET
    cruise_kts: int
    max_alt_ft: int
    mtow_kg: int
    wake_cat: str      # L, M, H, J

    def describe(self) -> str:
        return (f"{self.name} ({self.icao}), {self.category}, "
                f"cruise {self.cruise_kts} kt, max FL{self.max_alt_ft // 100:03d}, "
                f"MTOW {self.mtow_kg} kg, wake {self.wake_cat}")


def load(path: Path = _DATA_PATH):
    global _DB
    with open(path) as f:
        entries = json.load(f)
    _DB = {e['icao']: AircraftPerf(**e) for e in entries}
    log.debug(f"Loaded {len(_DB)} aircraft types")


def lookup(icao: str) -> Optional[AircraftPerf]:
    if not _DB:
        load()
    return _DB.get(icao.upper().strip())
