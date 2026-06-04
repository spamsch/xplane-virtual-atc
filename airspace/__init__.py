"""
Controlled-airspace awareness from OpenAIP.

X-Plane doesn't expose airspace, so the controller can't otherwise know it's in
the Hannover CTR. This package loads OpenAIP's per-country airspace export and
answers point-in-polygon queries against the live aircraft position, so the
handover logic and the controller's situational awareness can use real control
zones instead of a distance guess.

Data © OpenAIP contributors, CC BY-NC 4.0 — https://www.openaip.net/
"""

from airspace.database import Airspace, AirspaceDB, airspace_from_openaip
from airspace.openaip import country_for_icao, load_country, load_for_airport

__all__ = [
    "Airspace",
    "AirspaceDB",
    "airspace_from_openaip",
    "country_for_icao",
    "load_country",
    "load_for_airport",
]
