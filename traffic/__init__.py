"""
Ambient radio traffic — the party line.

When X-Plane is connected, the frequency you're tuned to shouldn't be dead air.
Other aircraft call the same controller; the controller answers them. This
package holds the *content* of that chatter (a library of short scripted
interactions) and the logic that picks, fills, and paces it. It is deliberately
transport-agnostic and stdlib-only: the backend drives synthesis and playback,
this package decides *what* gets said and *when*.

Layers:
  library.py  — interaction schema, JSON loader, airport-size classification,
                callsign generation, filtering, placeholder rendering.
  ambient.py  — level → timing/probability (light/medium/heavy), seeded RNG.
"""

from traffic.library import (
    Interaction,
    Line,
    Rendered,
    RenderContext,
    InteractionLibrary,
    classify_size,
    load_library,
    render,
)
from traffic.ambient import Level, LEVELS, AmbientPlanner, resolve_level

__all__ = [
    "Interaction",
    "Line",
    "Rendered",
    "RenderContext",
    "InteractionLibrary",
    "classify_size",
    "load_library",
    "render",
    "Level",
    "LEVELS",
    "AmbientPlanner",
    "resolve_level",
]
