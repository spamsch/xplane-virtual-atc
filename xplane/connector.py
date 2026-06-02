"""
X-Plane UDP DataRef connector using the RREF protocol.

X-Plane listens on port 49000 (default). We subscribe to DataRefs by sending
413-byte RREF packets; X-Plane streams back (index, float) pairs.

String DataRefs (acf_ICAO, tailnum) are accessed character-by-character as
indexed floats, e.g. sim/aircraft/view/acf_ICAO[0].

Unit notes:
  - indicated_airspeed: kias (knots)
  - groundspeed: m/s  → multiply by 1.94384 for knots
  - elevation: meters MSL
  - h_ind (indicated altitude): feet
  - com freq: units of 10 kHz, i.e. 12175 = 121.75 MHz (divide by 100)
"""

import copy
import socket
import struct
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# --- DataRef index map ---
_IDX = {
    'lat':       0,
    'lon':       1,
    'elev':      2,
    'alt_ind':   3,
    'ias':       4,   # kias
    'gs':        5,   # m/s
    'hdg_true':  6,
    'hdg_mag':   7,
    'on_ground': 8,
    'paused':    9,
    'com1':      10,
    'com2':      11,
    'xpdr':      12,
}
_IDX_ICAO_BASE = 20   # indices 20-23 → 4 chars
_IDX_TAIL_BASE = 30   # indices 30-39 → 10 chars

_SUBSCRIPTIONS: list = [
    (_IDX['lat'],       'sim/flightmodel/position/latitude',           5),
    (_IDX['lon'],       'sim/flightmodel/position/longitude',          5),
    (_IDX['elev'],      'sim/flightmodel/position/elevation',          5),
    (_IDX['alt_ind'],   'sim/flightmodel/misc/h_ind',                  5),
    (_IDX['ias'],       'sim/flightmodel/position/indicated_airspeed', 5),
    (_IDX['gs'],        'sim/flightmodel/position/groundspeed',        5),
    (_IDX['hdg_true'],  'sim/flightmodel/position/psi',                5),
    (_IDX['hdg_mag'],   'sim/flightmodel/position/mag_psi',            5),
    (_IDX['on_ground'], 'sim/flightmodel/failures/onground_any',       5),
    (_IDX['paused'],    'sim/time/paused',                             2),
    (_IDX['com1'],      'sim/cockpit/radios/com1_freq_hz',             1),
    (_IDX['com2'],      'sim/cockpit/radios/com2_freq_hz',             1),
    (_IDX['xpdr'],      'sim/cockpit/radios/transponder_code',         1),
] + [
    (_IDX_ICAO_BASE + i, f'sim/aircraft/view/acf_ICAO[{i}]', 1)
    for i in range(4)
] + [
    (_IDX_TAIL_BASE + i, f'sim/aircraft/view/acf_tailnum[{i}]', 1)
    for i in range(10)
]

_IDX_TO_FIELD = {
    _IDX['lat']:       'lat',
    _IDX['lon']:       'lon',
    _IDX['elev']:      'elevation_m',
    _IDX['alt_ind']:   'alt_ind_ft',
    _IDX['ias']:       'ias_kts',
    _IDX['gs']:        'groundspeed_ms',
    _IDX['hdg_true']:  'heading_true',
    _IDX['hdg_mag']:   'heading_mag',
    _IDX['on_ground']: 'on_ground',
    _IDX['paused']:    'paused',
    _IDX['com1']:      'com1_raw',
    _IDX['com2']:      'com2_raw',
    _IDX['xpdr']:      'transponder',
}


@dataclass
class FlightState:
    lat: float = 0.0
    lon: float = 0.0
    elevation_m: float = 0.0
    alt_ind_ft: float = 0.0
    ias_kts: float = 0.0
    groundspeed_ms: float = 0.0
    heading_true: float = 0.0
    heading_mag: float = 0.0
    on_ground: float = 0.0
    paused: float = 0.0
    com1_raw: float = 0.0   # units: 10 kHz steps (12175 = 121.75 MHz)
    com2_raw: float = 0.0
    transponder: float = 0.0
    _icao_chars: list = field(default_factory=lambda: [0.0] * 4)
    _tail_chars: list = field(default_factory=lambda: [0.0] * 10)

    @property
    def gs_kts(self) -> float:
        return self.groundspeed_ms * 1.94384

    @property
    def com1_mhz(self) -> float:
        return self.com1_raw / 100.0

    @property
    def com2_mhz(self) -> float:
        return self.com2_raw / 100.0

    @property
    def acf_icao(self) -> str:
        return ''.join(chr(int(c)) for c in self._icao_chars if 32 < c < 127).strip()

    @property
    def tail_number(self) -> str:
        return ''.join(chr(int(c)) for c in self._tail_chars if 32 < c < 127).strip()

    @property
    def is_flight_loaded(self) -> bool:
        # Position (0,0) = X-Plane not in a flight / still at intro screen
        return abs(self.lat) > 0.01 or abs(self.lon) > 0.01


class XPlaneConnector:
    def __init__(self, xplane_host: str = '127.0.0.1',
                 xplane_port: int = 49000,
                 local_port: int = 49001):
        self._host = xplane_host
        self._port = xplane_port
        self._local_port = local_port
        self._sock: Optional[socket.socket] = None
        self._state = FlightState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> FlightState:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(('', self._local_port))
        self._sock.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        self._subscribe_all()
        log.info(f"Subscribed to X-Plane DataRefs at {self._host}:{self._port}")

    def stop(self):
        self._running = False
        self._unsubscribe_all()
        if self._sock:
            self._sock.close()

    # ------------------------------------------------------------------ #

    def _make_rref_packet(self, freq: int, index: int, path: str) -> bytes:
        # Format: b'RREF\x00' + int32(freq) + int32(index) + 400-byte path
        path_bytes = (path.encode('ascii') + b'\x00').ljust(400, b'\x00')[:400]
        return b'RREF\x00' + struct.pack('<ii', freq, index) + path_bytes

    def _subscribe_all(self):
        for idx, path, freq in _SUBSCRIPTIONS:
            self._sock.sendto(self._make_rref_packet(freq, idx, path),
                              (self._host, self._port))

    def _unsubscribe_all(self):
        for idx, path, _ in _SUBSCRIPTIONS:
            try:
                self._sock.sendto(self._make_rref_packet(0, idx, path),
                                  (self._host, self._port))
            except Exception:
                pass

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if not data.startswith(b'RREF'):
                continue

            payload = data[5:]  # strip 'RREF\x00'
            with self._lock:
                for offset in range(0, len(payload) - 7, 8):
                    idx = struct.unpack_from('<i', payload, offset)[0]
                    val = struct.unpack_from('<f', payload, offset + 4)[0]
                    self._apply(idx, val)

    def _apply(self, idx: int, val: float):
        s = self._state
        field = _IDX_TO_FIELD.get(idx)
        if field:
            setattr(s, field, float(val))
        elif _IDX_ICAO_BASE <= idx < _IDX_ICAO_BASE + 4:
            s._icao_chars[idx - _IDX_ICAO_BASE] = val
        elif _IDX_TAIL_BASE <= idx < _IDX_TAIL_BASE + 10:
            s._tail_chars[idx - _IDX_TAIL_BASE] = val
