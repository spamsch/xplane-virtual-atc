"""
One-shot 'VFR day' weather setup via X-Plane's REST API.

apply_vfr_day() downloads the real weather as of now, freezes it (so WIND,
PRESSURE and TEMPERATURE stay real), then overrides only a few things:
  - a few scattered low cumulus,
  - visibility at least `min_vis_sm`,
  - rain off,
  - the clock at local noon.

It deliberately never writes wind/pressure/temperature — freezing real weather
preserves them. Independent of the polling connector: it resolves the handful of
datarefs it needs on demand. The HTTP helpers are module-level so tests can
monkeypatch them.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# Datarefs we touch. Wind/pressure/temperature are intentionally absent.
_NAMES = {
    'mode':  'sim/weather/region/change_mode',
    'cov':   'sim/weather/region/cloud_coverage_percent',
    'ctype': 'sim/weather/region/cloud_type',
    'cbase': 'sim/weather/region/cloud_base_msl_m',
    'ctops': 'sim/weather/region/cloud_tops_msl_m',
    'vis':   'sim/weather/region/visibility_reported_sm',
    'rain':  'sim/weather/region/rain_percent',
    'zulu':  'sim/time/zulu_time_sec',
    'local': 'sim/time/local_time_sec',
}

# X-Plane change_mode: 7 = use real weather (downloads), 3 = static (frozen).
_MODE_REAL = 7
_MODE_STATIC = 3
_NOON_LOCAL_SEC = 12 * 3600
_CUMULUS = 2.0   # cloud_type value for cumulus


def _resolve(base, name):
    """Resolve a dataref name → numeric id, or None."""
    url = f"{base}/api/v3/datarefs?filter[name]=" + urllib.parse.quote(name, safe='')
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            for ref in json.load(r).get('data', []):
                if ref.get('name') == name:
                    return ref.get('id')
    except Exception as e:
        log.debug(f"resolve {name}: {e}")
    return None


def _get(base, rid):
    with urllib.request.urlopen(f"{base}/api/v3/datarefs/{rid}/value", timeout=6) as r:
        return json.load(r).get('data')


def _patch(base, rid, value):
    body = json.dumps({'data': value}).encode()
    req = urllib.request.Request(f"{base}/api/v3/datarefs/{rid}/value", data=body,
                                 method='PATCH', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=6):
        return True


def apply_vfr_day(host, port, *, scattered=0.30, min_vis_sm=5.0,
                  real_wx_wait=6.0, settle=4.0, sleep=time.sleep) -> dict:
    """Set a clear-ish VFR day on the connected sim. Returns a short summary.
    Raises RuntimeError if the weather datarefs can't be resolved."""
    base = f"http://{host}:{port}"
    ids = {k: _resolve(base, n) for k, n in _NAMES.items()}
    missing = [n for k, n in _NAMES.items() if ids[k] is None]
    if missing:
        raise RuntimeError(f"weather datarefs unavailable ({len(missing)}): {missing[0]} …")

    # 1) Download the real weather as of now.
    _patch(base, ids['mode'], _MODE_REAL)
    sleep(real_wx_wait)
    # 2) Freeze it — wind, pressure and temperature now stay real.
    _patch(base, ids['mode'], _MODE_STATIC)
    sleep(0.5)
    # 3) A few scattered cumulus. Write the full layer (type/base/tops/coverage)
    #    so it persists; XP commits cloud edits on its next regen cycle.
    _patch(base, ids['ctype'], [_CUMULUS, 0.0, 0.0])
    _patch(base, ids['cbase'], [1000.0, 0.0, 0.0])
    _patch(base, ids['ctops'], [2100.0, 0.0, 0.0])
    _patch(base, ids['cov'],   [float(scattered), 0.0, 0.0])
    # 4) Visibility at least the floor (keep real if already better).
    vis = _get(base, ids['vis'])
    if vis is None or vis <= min_vis_sm:
        vis = max(min_vis_sm + 1.0, 6.0)
        _patch(base, ids['vis'], float(vis))
    # 4b) No rain — freezing static weather keeps whatever precip was real.
    _patch(base, ids['rain'], 0.0)
    # 5) Local noon — derive zulu from the live local↔zulu offset.
    local = _get(base, ids['local'])
    zulu = _get(base, ids['zulu'])
    if local is not None and zulu is not None:
        offset = (local - zulu) % 86400
        _patch(base, ids['zulu'], float((_NOON_LOCAL_SEC - offset) % 86400))
    sleep(settle)

    return {
        'clouds': 'scattered cumulus',
        'visibility_sm': round(float(vis), 1) if vis is not None else None,
        'rain': 'off',
        'time': 'local noon',
        'preserved': 'wind, pressure, temperature (real)',
    }
