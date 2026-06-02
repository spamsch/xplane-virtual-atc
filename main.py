#!/usr/bin/env python3
"""
X-Plane Virtual ATC — VFR Ground Control

State machine:
  WAITING_FOR_XPLANE  → polls until X-Plane reports a valid position
  DETECTING_AIRPORT   → matches position against apt.dat
  READY               → boundary check with Opus, then interactive loop
  LISTENING           → waits for pilot radio call via stdin
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

import config
import aircraft.database as acdb
from airport.parser import parse_apt_dat
from airport.database import AirportDB
from atc import engine as atc_engine
from atc.session import ATCSession
from xplane.connector import XPlaneConnector, FlightState

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #

def _find_apt_dat() -> Path:
    for p in config.APT_DAT_PATHS:
        if p.exists():
            return p
    paths_str = '\n  '.join(str(p) for p in config.APT_DAT_PATHS)
    raise FileNotFoundError(
        f"apt.dat not found. Set XPLANE_PATH environment variable.\n"
        f"Checked:\n  {paths_str}"
    )


def _wait_for_flight(connector: XPlaneConnector) -> FlightState:
    print("Waiting for X-Plane to load a flight", end='', flush=True)
    while True:
        state = connector.state
        if state.is_flight_loaded:
            print()
            return state
        print('.', end='', flush=True)
        time.sleep(2)


def _detect_airport(db: AirportDB, state: FlightState):
    airport = db.nearest(state.lat, state.lon,
                         max_dist_nm=config.AIRPORT_DETECTION_RADIUS_NM)
    return airport


# ------------------------------------------------------------------ #

def main():
    print("=" * 60)
    print("  X-Plane Virtual ATC  —  VFR Ground Control")
    print("=" * 60)

    # --- Load databases ---
    apt_dat = _find_apt_dat()
    print(f"\nLoading airport database ({apt_dat.parent.parent.name}/…/{apt_dat.name})")
    print("(First run parses the full file and caches it — may take a minute)\n")
    airports = parse_apt_dat(apt_dat)
    db = AirportDB(airports, margin_nm=config.AIRPORT_DETECTION_RADIUS_NM)
    acdb.load()
    print(f"Ready: {len(airports):,} airports indexed.\n")

    # --- Connect to X-Plane ---
    connector = XPlaneConnector(
        xplane_host=config.XPLANE_IP,
        xplane_port=config.XPLANE_UDP_PORT,
        local_port=config.LOCAL_RECV_PORT,
    )
    try:
        connector.start()
    except OSError as e:
        print(f"Could not bind UDP port {config.LOCAL_RECV_PORT}: {e}")
        print("Is another instance already running? Try: XPLANE_PORT=49002 python main.py")
        sys.exit(1)

    # --- Wait for a flight ---
    try:
        state = _wait_for_flight(connector)
    except KeyboardInterrupt:
        print("\nAborted.")
        connector.stop()
        return

    icao_type = state.acf_icao or 'UNKN'
    callsign = state.tail_number or 'UNKNOWN'
    acft = acdb.lookup(icao_type)

    print(f"Aircraft : {icao_type}  ({callsign})")
    if acft:
        print(f"           {acft.describe()}")
    else:
        print(f"           (no performance data for type '{icao_type}')")

    print(f"Position : lat {state.lat:.4f}  lon {state.lon:.4f}  "
          f"alt {state.alt_ind_ft:.0f} ft")
    print(f"On ground: {'yes' if state.on_ground > 0.5 else 'no'}\n")

    # --- Find airport ---
    airport = _detect_airport(db, state)
    if not airport:
        print(f"Not within {config.AIRPORT_DETECTION_RADIUS_NM} nm of any known airport.")
        print("Start a flight at an airport and restart.")
        connector.stop()
        return

    print(f"Airport  : {airport.name}  ({airport.icao})")
    print(f"Runways  : {airport.runway_summary()}")
    print("Frequencies:")
    print(airport.freq_summary())

    # Check which runway the aircraft is on
    rwy = db.which_runway(airport, state.lat, state.lon,
                          margin_m=config.RUNWAY_DETECTION_MARGIN_M)
    if rwy:
        print(f"On runway: {rwy.name1}/{rwy.name2}")
    print()

    # --- Boundary check: Opus determines active runway ---
    print("[Querying Opus for ATC context setup…]")
    active_runway = 'unknown'
    notes = ''
    try:
        ctx = atc_engine.boundary_check(
            airport=airport,
            acft=acft,
            callsign=callsign,
            conditions={'wind': '?', 'qnh': '?', 'vis': '?', 'time': 'check simulator clock'},
            model=config.MODEL_BOUNDARY,
        )
        active_runway = ctx.get('active_runway', 'unknown')
        notes = ctx.get('notes', '')
        print(f"Active runway : {active_runway}")
        if notes:
            print(f"Notes         : {notes}")
    except Exception as e:
        print(f"[Boundary check failed: {e}]")

    # --- Create session ---
    session_conditions = {
        airport.icao: {
            'qnh': '??',
            'wind_dir': 0,
            'wind_kts': 0,
            'visibility_km': 10,
            'atis': '?',
            'active_runway': active_runway,
        }
    }
    session = ATCSession(
        departure=airport,
        destination=None,
        aircraft=acft,
        callsign=callsign,
        conditions=session_conditions,
    )

    # --- Interactive loop ---
    print()
    print("=" * 60)
    print(f"  {session._atc_callsign()} is online")
    print(f"  Callsign in sim: {callsign}  ({icao_type})")
    print(f"  Active runway  : {active_runway}")
    gnd = airport.ground_freq()
    if gnd:
        print(f"  Ground freq    : {gnd.freq_mhz:.3f} MHz")
    print()
    print("  Enter pilot radio calls below.")
    print("  Example: 'Hannover Ground D-EIYD request startup'")
    print("  Ctrl+C to exit")
    print("=" * 60)
    print()

    prev_phase = session.phase
    prev_station = session.current_station

    while True:
        try:
            raw = input("Pilot: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break

        if not raw:
            continue

        try:
            r = session.process(raw)
            print(f"\nATC: {r.text}\n")
            if r.squawk:
                print(f"[Squawk: {r.squawk}]")
            if r.frequency_change:
                print(f"[→ {r.frequency_change:.3f} MHz]")
            if r.phase_after != prev_phase or r.station_after != prev_station:
                print(f"[{r.station_after.value.upper()} / {r.phase_after.value}]")
                prev_phase = r.phase_after
                prev_station = r.station_after
            print()
        except subprocess.TimeoutExpired:
            print("[LLM timeout — try again]\n")
        except Exception as e:
            print(f"[Error: {e}]\n")

    connector.stop()


if __name__ == '__main__':
    main()
