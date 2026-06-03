"""
Live taxi-routing diagnostic against a RUNNING X-Plane.

This is opt-in: it is SKIPPED unless VATC_LIVE is set, because it needs X-Plane
running with a flight loaded and the REST API enabled.

Run it and read the report (use -s so the diagnostic prints):

    VATC_LIVE=1 .venv/bin/python -m pytest -s tests/test_live_xplane.py

or directly:

    VATC_LIVE=1 .venv/bin/python tests/test_live_xplane.py

It reads your live position, finds the nearest airport, shows which stand and
taxi node you snap to, and prints the computed route to every runway end — so
you can compare it against what you see out the window (e.g. parked on "GA2").
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from airport.parser import parse_apt_dat
from airport.database import AirportDB, haversine_nm
from airport import taxi
from xplane.rest_connector import XPlaneRestConnector

pytestmark = pytest.mark.skipif(
    not os.environ.get("VATC_LIVE"),
    reason="live test — set VATC_LIVE=1 with X-Plane running and a flight loaded",
)


def _live_state(timeout: float = 25.0):
    """Connect to the live X-Plane REST API and return a FlightState with a
    valid position, or None if it never arrives within `timeout`."""
    c = XPlaneRestConnector(host=config.XPLANE_IP, port=config.XPLANE_REST_PORT)
    c.start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            st = c.state
            if c.connected and (st.lat or st.lon):
                return st
            time.sleep(0.5)
    finally:
        c.stop()
    return None


def _load_db() -> AirportDB:
    apt = next((p for p in config._apt_dat_paths(config.XPLANE_BASE) if p.exists()), None)
    assert apt, "apt.dat not found — set XPLANE_PATH"
    return AirportDB(parse_apt_dat(apt))


def _report(st, db: AirportDB) -> str:
    L = []
    p = L.append
    p("")
    p("=" * 64)
    p(f" LIVE TAXI DIAGNOSTIC")
    p("=" * 64)
    p(f" position    : {st.lat:.6f}, {st.lon:.6f}")
    p(f" heading     : {st.heading_mag:.0f}° mag   on_ground={bool(st.on_ground > 0.5)}")
    p(f" wind        : {int(st.wind_dir_deg)}° / {int(st.wind_speed_kts)} kt"
      f"   (active runway is wind-dependent — judge from this)")

    ap = db.nearest(st.lat, st.lon)
    if not ap:
        p(" nearest airport: NONE within range")
        return "\n".join(L)
    d_nm = haversine_nm(st.lat, st.lon, ap.lat, ap.lon)
    p(f" airport     : {ap.icao} {ap.name}  ({d_nm:.2f} nm away)")
    p(f" taxi network: nodes={len(ap.taxi_nodes)} edges={len(ap.taxi_edges)} "
      f"ramps={len(ap.ramp_starts)}  has_net={ap.has_taxi_network()}")

    # Nearest stands (what 'GA2' actually maps to in apt.dat)
    stands = sorted(ap.ramp_starts,
                    key=lambda r: taxi._haversine_m(st.lat, st.lon, r.lat, r.lon))[:3]
    p(" nearest stands:")
    for r in stands:
        dm = taxi._haversine_m(st.lat, st.lon, r.lat, r.lon)
        p(f"     '{r.name}'  ({r.kind})  {dm:.0f} m")

    # Snapped taxi node
    node = taxi._nearest_node(ap, st.lat, st.lon)
    if node is not None:
        n = ap.taxi_nodes[node]
        dm = taxi._haversine_m(st.lat, st.lon, n.lat, n.lon)
        p(f" snapped node: {node}  ({dm:.0f} m from aircraft)")

    # Route to every runway end
    p(" routes to each runway end:")
    ends = []
    for rwy in ap.runways:
        ends += [rwy.name1, rwy.name2]
    for end in ends:
        r = taxi.compute_route(ap, st.lat, st.lon, end)
        if r:
            via = ", ".join(r.taxiways) if r.taxiways else "(apron only)"
            p(f"     RWY {end:<4} : via {via:<18} → hold short {end}   ({r.distance_m:.0f} m)")
        else:
            p(f"     RWY {end:<4} : no route (no hold nodes / unreachable)")
    p("=" * 64)
    return "\n".join(L)


def test_live_taxi_routing(capsys):
    st = _live_state()
    if st is None:
        pytest.skip("X-Plane REST API not reachable / no position — is a flight loaded?")
    db = _load_db()
    report = _report(st, db)
    with capsys.disabled():
        print(report)

    # Sanity assertions — the diagnostic is the point, but fail loudly if broken.
    assert st.lat or st.lon, "no live position"
    ap = db.nearest(st.lat, st.lon)
    assert ap is not None, "no airport found near the aircraft"


if __name__ == "__main__":
    os.environ.setdefault("VATC_LIVE", "1")
    s = _live_state()
    if s is None:
        print("Could not read a live position from X-Plane. "
              "Is it running with a flight loaded and the REST API enabled?")
        sys.exit(1)
    print(_report(s, _load_db()))
