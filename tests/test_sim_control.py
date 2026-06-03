"""
Tests for the 'VFR day' weather setup. The REST layer is monkeypatched with an
in-memory dataref store, so this runs without X-Plane. The key assertions:
weather ends frozen with scattered clouds, visibility above the floor, local
noon — and wind/pressure/temperature are NEVER written.
"""
import pytest

from xplane import sim_control as sc


@pytest.fixture
def store(monkeypatch):
    # name → value. local = zulu + 2h so "local noon" should drive zulu to 36000.
    s = {
        'sim/weather/region/change_mode': 7,
        'sim/weather/region/cloud_coverage_percent': [0.9, 0.9, 0.9],
        'sim/weather/region/cloud_type': [1.0, 1.0, 1.0],
        'sim/weather/region/cloud_base_msl_m': [300.0, 0.0, 0.0],
        'sim/weather/region/cloud_tops_msl_m': [900.0, 0.0, 0.0],
        'sim/weather/region/visibility_reported_sm': 3.0,   # below the 5 sm floor
        'sim/time/zulu_time_sec': 54000.0,
        'sim/time/local_time_sec': 61200.0,
        # These must be left untouched (proves wind/pressure/temp are preserved):
        'sim/weather/region/wind_speed_msc': [5.0, 6.0, 7.0],
        'sim/weather/region/sealevel_pressure_pas': 100500.0,
        'sim/weather/region/temperatures_aloft_deg_c': [15.0, 10.0, 5.0],
    }
    monkeypatch.setattr(sc, '_resolve', lambda base, name: name if name in s else None)
    monkeypatch.setattr(sc, '_get', lambda base, rid: s.get(rid))

    def _patch(base, rid, value):
        s[rid] = value
        return True
    monkeypatch.setattr(sc, '_patch', _patch)
    return s


def test_applies_vfr_day(store):
    res = sc.apply_vfr_day('h', 8086, real_wx_wait=0, settle=0, sleep=lambda *_: None)
    assert store['sim/weather/region/change_mode'] == sc._MODE_STATIC          # frozen
    cov = store['sim/weather/region/cloud_coverage_percent']
    assert 0 < cov[0] < 1 and cov[1] == 0 and cov[2] == 0                       # one scattered layer
    assert store['sim/weather/region/visibility_reported_sm'] > 5              # floored above 5 sm
    # local noon: local was zulu+7200, so zulu must become 43200-7200 = 36000.
    assert store['sim/time/zulu_time_sec'] == 36000.0
    assert res['time'] == 'local noon'


def test_preserves_wind_pressure_temperature(store):
    sc.apply_vfr_day('h', 8086, real_wx_wait=0, settle=0, sleep=lambda *_: None)
    assert store['sim/weather/region/wind_speed_msc'] == [5.0, 6.0, 7.0]
    assert store['sim/weather/region/sealevel_pressure_pas'] == 100500.0
    assert store['sim/weather/region/temperatures_aloft_deg_c'] == [15.0, 10.0, 5.0]


def test_keeps_good_visibility(store):
    store['sim/weather/region/visibility_reported_sm'] = 12.0   # already great
    sc.apply_vfr_day('h', 8086, real_wx_wait=0, settle=0, sleep=lambda *_: None)
    assert store['sim/weather/region/visibility_reported_sm'] == 12.0   # untouched


def test_raises_when_datarefs_missing(monkeypatch):
    monkeypatch.setattr(sc, '_resolve', lambda base, name: None)
    with pytest.raises(RuntimeError):
        sc.apply_vfr_day('h', 8086, real_wx_wait=0, settle=0, sleep=lambda *_: None)
