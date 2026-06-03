"""
The boundary check must not run before X-Plane's weather has been read, or it
picks the active runway wind-blind. _await_live_weather() is the gate.
"""
import asyncio
from types import SimpleNamespace

import backend.server as srv


class _Driver:
    """Fake connector whose QNH appears after `arrives_after` reads."""
    def __init__(self, qnh, arrives_after=0):
        self._qnh = qnh
        self._arrives_after = arrives_after
        self.reads = 0

    @property
    def state(self):
        self.reads += 1
        q = self._qnh if self.reads > self._arrives_after else 0.0
        return SimpleNamespace(qnh_hpa=q, is_flight_loaded=True)


def test_returns_immediately_when_weather_present(monkeypatch):
    monkeypatch.setattr(srv, "_driver", _Driver(1013.0))
    wx = asyncio.run(srv._await_live_weather(timeout=2.0, poll=0.05))
    assert wx.qnh_hpa == 1013.0


def test_waits_until_weather_arrives(monkeypatch):
    drv = _Driver(1008.0, arrives_after=3)   # QNH shows up on the 4th read
    monkeypatch.setattr(srv, "_driver", drv)
    wx = asyncio.run(srv._await_live_weather(timeout=3.0, poll=0.02))
    assert wx.qnh_hpa == 1008.0
    assert drv.reads >= 4                     # it actually polled, didn't return early


def test_times_out_with_defaults(monkeypatch):
    monkeypatch.setattr(srv, "_driver", _Driver(0.0))   # weather never arrives
    wx = asyncio.run(srv._await_live_weather(timeout=0.2, poll=0.05))
    assert wx.qnh_hpa == 0.0   # returns the (still-default) state instead of hanging
