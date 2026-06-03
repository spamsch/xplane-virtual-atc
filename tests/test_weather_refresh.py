"""
After set_vfr_weather, the session's stored QNH/wind get refreshed (no LLM), and
the boundary check is re-run only when the wind veers enough to change the
runway. Chat/phase are untouched.
"""
import asyncio
from types import SimpleNamespace

import backend.server as srv
from atc.session import Phase, Station


def test_angular_diff():
    assert srv._angular_diff(10, 350) == 20
    assert srv._angular_diff(270, 90) == 180
    assert srv._angular_diff(0, 0) == 0
    assert srv._angular_diff(80, 100) == 20


class _Sess:
    def __init__(self, cond):
        self.conditions = cond
        self.callsign = "D-EIYD"
        self.phase = Phase.PRE_DEPARTURE
        self.current_station = Station.GND

    def _atc_callsign(self):
        return "Hannover Ground"


def _setup(monkeypatch, *, qnh, wdir, wkts, old_dir):
    monkeypatch.setattr(srv, "_source", "xplane")
    monkeypatch.setattr(srv, "_current_airport", SimpleNamespace(icao="EDDV", runways=[]))
    monkeypatch.setattr(srv, "_current_acft", None)
    monkeypatch.setattr(srv, "_driver", SimpleNamespace(
        state=SimpleNamespace(qnh_hpa=qnh, wind_dir_deg=wdir, wind_speed_kts=wkts)))
    cond = {"EDDV": {"wind_dir": old_dir, "wind_kts": 8, "active_runway": "27R", "qnh": 1013}}
    monkeypatch.setattr(srv, "_session", _Sess(cond))
    sent = []

    async def fake_broadcast(t, **k):
        sent.append((t, k))
    monkeypatch.setattr(srv, "_broadcast", fake_broadcast)
    return cond, sent


def test_refresh_updates_conditions_without_recheck(monkeypatch):
    cond, sent = _setup(monkeypatch, qnh=1008.0, wdir=275, wkts=10, old_dir=270)  # 5° shift
    called = []
    monkeypatch.setattr(srv.atc_engine, "boundary_check",
                        lambda **k: called.append(k) or {"active_runway": "09L"})
    asyncio.run(srv._refresh_session_weather())
    assert cond["EDDV"]["qnh"] == 1008.0
    assert cond["EDDV"]["wind_dir"] == 275 and cond["EDDV"]["wind_kts"] == 10
    assert called == []                            # tiny shift → no boundary check
    assert cond["EDDV"]["active_runway"] == "27R"  # runway unchanged


def test_refresh_reruns_boundary_on_large_wind_shift(monkeypatch):
    cond, sent = _setup(monkeypatch, qnh=1008.0, wdir=90, wkts=12, old_dir=270)  # 180° reversal
    monkeypatch.setattr(srv.atc_engine, "boundary_check",
                        lambda **k: {"active_runway": "09L", "atc_callsign": "Hannover Ground"})
    asyncio.run(srv._refresh_session_weather())
    assert cond["EDDV"]["active_runway"] == "09L"
    assert any(t == "phase_change" and k.get("active_runway") == "09L" for t, k in sent)


def test_refresh_skips_when_calm(monkeypatch):
    cond, _ = _setup(monkeypatch, qnh=1008.0, wdir=90, wkts=1, old_dir=270)  # big shift but calm
    called = []
    monkeypatch.setattr(srv.atc_engine, "boundary_check",
                        lambda **k: called.append(k) or {"active_runway": "09L"})
    asyncio.run(srv._refresh_session_weather())
    assert called == []                            # calm wind → keep runway
    assert cond["EDDV"]["active_runway"] == "27R"


def test_refresh_noop_without_session(monkeypatch):
    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_source", "xplane")
    asyncio.run(srv._refresh_session_weather())   # must not raise
