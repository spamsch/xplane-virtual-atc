"""Tests for the navaid/fix database — parsing, caching, and nearest-reference
resolution of ambiguous identifiers. No X-Plane install needed; tiny synthetic
.dat files are written to a tmp dir."""

from pathlib import Path

from navigation.navaids import Navaid, NavaidDB, parse_nav_data


# A leading space on every data row, exactly like X-Plane's files.
NAV_DAT = """\
I
1200 Version - data cycle 2406
 3  52.200136111   8.285519444     0    11430   130     1.000  OSN ENRT ED OSNABRUECK VOR
 2  53.500000000   9.000000000     0      375    50     0.000  XYZ ENRT ED SOME NDB
 3  40.000000000  -3.000000000     0    11430   130     1.000  OSN ENRT LE A FAR VOR
99
"""

FIX_DAT = """\
I
1200 Version - data cycle 2406
 51.425000000    7.280555556  ABAMI ENRT ED 2105431 ABAMI
 52.100000000    8.300000000  OSN   ENRT ED 9999999 OSN
99
"""


def _write(tmp_path: Path) -> tuple[Path, Path]:
    nav = tmp_path / "earth_nav.dat"
    fix = tmp_path / "earth_fix.dat"
    nav.write_text(NAV_DAT)
    fix.write_text(FIX_DAT)
    return nav, fix


def test_parse_counts_navaids_and_fixes(tmp_path):
    nav, fix = _write(tmp_path)
    db = parse_nav_data(nav, fix)
    assert len(db.all("OSN")) == 3        # two VORs + one fix
    assert db.all("ABAMI")[0].kind == "FIX"
    assert db.all("XYZ")[0].kind == "NDB"


def test_resolve_picks_nearest_reference(tmp_path):
    nav, fix = _write(tmp_path)
    db = parse_nav_data(nav, fix)
    # Near Bielefeld (52.0, 8.55) → the German Osnabrück VOR, not the far one in LE.
    osn = db.resolve("OSN", 52.0, 8.55)
    assert osn is not None
    assert osn.region == "ED"
    assert osn.kind == "VOR"
    assert abs(osn.lat - 52.2001) < 0.01


def test_resolve_prefers_vor_over_nearby_fix(tmp_path):
    nav, fix = _write(tmp_path)
    db = parse_nav_data(nav, fix)
    # The OSN fix (52.1, 8.3) is marginally closer to this ref than the VOR
    # (52.2, 8.29), but the navaid should still win within the 30 NM bias.
    osn = db.resolve("OSN", 52.12, 8.30)
    assert osn.kind == "VOR"


def test_resolve_unknown_is_none(tmp_path):
    nav, fix = _write(tmp_path)
    db = parse_nav_data(nav, fix)
    assert db.resolve("NOPE", 52.0, 8.0) is None


def test_cache_roundtrip(tmp_path):
    nav, fix = _write(tmp_path)
    parse_nav_data(nav, fix)                       # writes cache
    cache = nav.with_suffix(".vatc_navaids_v1.pkl")
    assert cache.exists()
    db2 = parse_nav_data(nav, fix)                 # reads cache
    assert len(db2.all("OSN")) == 3


def test_empty_db_resolves_none():
    db = NavaidDB({})
    assert db.resolve("OSN", 0, 0) is None
    assert len(db) == 0
