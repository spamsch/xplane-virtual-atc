#!/usr/bin/env python3
"""
Interactive weather editor for a running X-Plane 12 sim.

Drives the same REST API the backend uses (xplane/sim_control.py) to set
clouds, wind, and visibility by hand. Unlike apply_vfr_day() — which freezes
real weather and only nudges a few things — this gives you direct control over
every layer, so it WILL write wind.

Prerequisites:
  - X-Plane 12.1.0+ running, with Settings → Network → "Enable local network
    access" turned on (REST API on port 8086).
  - Run from the repo root:  python3 scripts/set_weather.py
    Host/port come from config (XPLANE_IP / XPLANE_REST_PORT env vars).

The first thing it does is switch the region weather to STATIC mode, otherwise
X-Plane's real-weather engine overwrites your edits on the next regen cycle.
"""

import sys
from pathlib import Path

# Run from anywhere: put the repo root on the path so `xplane` / `config` import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from xplane.sim_control import _resolve, _get, _patch  # proven REST helpers

KTS_TO_MS = 0.514444
FT_TO_M = 0.3048

# Region datarefs we touch. All region arrays hold 3 layers (low/mid/high).
NAMES = {
    "mode":  "sim/weather/region/change_mode",
    "cov":   "sim/weather/region/cloud_coverage_percent",   # [3] 0.0–1.0
    "ctype": "sim/weather/region/cloud_type",               # [3] see CLOUD_TYPES
    "cbase": "sim/weather/region/cloud_base_msl_m",          # [3] metres MSL
    "ctops": "sim/weather/region/cloud_tops_msl_m",          # [3] metres MSL
    "vis":   "sim/weather/region/visibility_reported_sm",    # scalar, statute miles
    "wspd":  "sim/weather/region/wind_speed_msc",            # [13] m/s, surface→~16 km
    "wdir":  "sim/weather/region/wind_direction_degt",       # [13] degrees true
    "walt":  "sim/weather/region/wind_altitude_msl_m",       # [13] metres MSL
    "rain":  "sim/weather/region/rain_percent",              # scalar 0.0–1.0
}

MODE_STATIC = 3  # manual/static weather — required for edits to stick

CLOUD_TYPES = {0: "cirrus", 1: "stratus", 2: "cumulus", 3: "cumulonimbus"}

# name -> coverage fraction (oktas/8). XP wants 0.0–1.0.
COVERAGE = {
    "clear": 0.0,
    "few": 2 / 8,
    "scattered": 4 / 8,   # XP treats ~0.3–0.5 as SCT visually
    "broken": 6 / 8,
    "overcast": 8 / 8,
}


def base_url() -> str:
    return f"http://{config.XPLANE_IP}:{config.XPLANE_REST_PORT}"


def resolve_all(base: str) -> dict:
    ids = {k: _resolve(base, n) for k, n in NAMES.items()}
    missing = [n for k, n in NAMES.items() if ids[k] is None]
    if missing:
        raise RuntimeError(
            "Could not resolve weather datarefs — is X-Plane running with the "
            f"REST API enabled at {base}?\n  first missing: {missing[0]}")
    return ids


def ask(prompt: str, default=None):
    """Prompt, returning stripped input or the default on empty/EOF."""
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        print()
        return default
    return raw if raw else default


def ask_float(prompt: str, default=None):
    while True:
        raw = ask(prompt, default)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            print("  not a number, try again.")


# --- actions ---------------------------------------------------------------

def show_current(base: str, ids: dict):
    cov = _get(base, ids["cov"])
    ctype = _get(base, ids["ctype"])
    cbase = _get(base, ids["cbase"])
    vis = _get(base, ids["vis"])
    wspd = _get(base, ids["wspd"])
    wdir = _get(base, ids["wdir"])

    def first(v):
        return v[0] if isinstance(v, list) and v else v

    cov0 = first(cov)
    ct0 = int(first(ctype) or 0)
    base_m = first(cbase) or 0.0
    spd_ms = first(wspd) or 0.0
    dir_t = first(wdir) or 0.0
    print("\n  Current region weather (low layer):")
    print(f"    clouds      {CLOUD_TYPES.get(ct0, ct0)} @ "
          f"{cov0 * 100 if cov0 is not None else 0:.0f}% cover, "
          f"base {base_m / FT_TO_M:.0f} ft")
    print(f"    visibility  {vis:.1f} sm" if vis is not None else "    visibility  n/a")
    print(f"    wind        {dir_t:.0f}° at {spd_ms / KTS_TO_MS:.0f} kt\n")


def set_clouds(base: str, ids: dict):
    print("\n  Coverage: " + ", ".join(COVERAGE))
    cov_name = (ask("Coverage", "scattered") or "scattered").lower()
    if cov_name not in COVERAGE:
        print(f"  unknown coverage '{cov_name}', using scattered.")
        cov_name = "scattered"
    cov = COVERAGE[cov_name]

    print("  Type: " + ", ".join(f"{k}={v}" for k, v in CLOUD_TYPES.items()))
    ctype = int(ask_float("Cloud type", 2))
    base_ft = ask_float("Cloud base (ft MSL)", 3000)
    thick_ft = ask_float("Cloud thickness (ft)", 2000)
    base_m = base_ft * FT_TO_M
    tops_m = (base_ft + thick_ft) * FT_TO_M

    # Write the low layer; zero the mid/high so there's only one deck.
    _patch(base, ids["ctype"], [float(ctype), 0.0, 0.0])
    _patch(base, ids["cbase"], [base_m, 0.0, 0.0])
    _patch(base, ids["ctops"], [tops_m, 0.0, 0.0])
    _patch(base, ids["cov"],   [float(cov), 0.0, 0.0])
    print(f"  → {cov_name} {CLOUD_TYPES.get(ctype, ctype)}, "
          f"{base_ft:.0f}–{base_ft + thick_ft:.0f} ft\n")


def set_wind(base: str, ids: dict):
    direction = ask_float("Wind direction (° true, where it blows FROM)", 270)
    speed_kt = ask_float("Wind speed (kt)", 10)
    spd_ms = speed_kt * KTS_TO_MS
    # The region wind datarefs are full altitude profiles (13 levels in XP12,
    # surface at index 0 up to ~16 km MSL), NOT 3 cloud-style layers. X-Plane
    # rejects any PATCH whose length doesn't match the array exactly, so size
    # the write from the live array rather than hardcoding the count. We fill
    # every level with the same value: uniform wind from the surface up, so the
    # connector reports it whatever altitude the aircraft sits at.
    levels = len(_get(base, ids["wspd"]) or [])
    if levels == 0:
        print("  could not read the wind array length; aborting.\n")
        return
    _patch(base, ids["wdir"], [float(direction)] * levels)
    _patch(base, ids["wspd"], [float(spd_ms)] * levels)
    print(f"  → wind {direction:.0f}° at {speed_kt:.0f} kt ({levels} levels)")
    print("    (X-Plane commits region wind on its weather-regen cycle — give it "
          "a few seconds before 's' shows the new value)\n")


def set_visibility(base: str, ids: dict):
    vis = ask_float("Visibility (statute miles)", 10)
    _patch(base, ids["vis"], float(vis))
    print(f"  → visibility {vis:.1f} sm\n")


def set_rain(base: str, ids: dict):
    pct = ask_float("Rain (0–100%)", 0)
    _patch(base, ids["rain"], max(0.0, min(1.0, pct / 100.0)))
    print(f"  → rain {pct:.0f}%\n")


MENU = """\
  X-Plane weather editor — region is in STATIC mode (edits persist)
  ------------------------------------------------------------------
    c  clouds
    w  wind
    v  visibility
    r  rain
    s  show current
    q  quit
"""


def main():
    base = base_url()
    print(f"\nConnecting to X-Plane REST API at {base} …")
    try:
        ids = resolve_all(base)
    except RuntimeError as e:
        print(f"\n{e}\n")
        return 1

    # Force static mode up front, otherwise real-weather regen wipes the edits.
    _patch(base, ids["mode"], MODE_STATIC)
    print("Region weather set to STATIC. Ready.\n")

    actions = {
        "c": set_clouds,
        "w": set_wind,
        "v": set_visibility,
        "r": set_rain,
        "s": show_current,
    }
    while True:
        print(MENU)
        choice = (ask("Choose") or "").lower()
        if choice in ("q", "quit", "exit"):
            print("Done.")
            return 0
        action = actions.get(choice)
        if not action:
            print("  unknown option.\n")
            continue
        try:
            action(base, ids)
        except Exception as e:
            print(f"  failed: {e}\n")


if __name__ == "__main__":
    sys.exit(main())
