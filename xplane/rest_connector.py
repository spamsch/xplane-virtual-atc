"""
X-Plane REST API connector (X-Plane 12.1.0+).

X-Plane exposes a local HTTP REST API on port 8086. This connector uses it
as an alternative to UDP RREF:
  - No UDP port binding required
  - String datarefs (acf_ICAO, tailnum) return full strings, not char arrays
  - Works even when UDP is blocked by a local firewall

Connection lifecycle:
  1. Probe GET /api/v1 every PROBE_INTERVAL seconds until "result": "ok"
  2. Discover numeric IDs for all needed datarefs (GET by exact name)
  3. Poll values by ID at POLL_HZ rate

Implements the same informal FlightDataSource protocol as XPlaneConnector:
  .state → FlightState (deepcopy, thread-safe)
  .start() / .stop()

Unit notes (same as UDP connector):
  - indicated_airspeed: kias (knots) — already in knots from X-Plane
  - groundspeed: m/s  → FlightState.gs_kts computed property converts
  - elevation: metres MSL
  - h_ind (indicated altitude): feet
  - com freq: 10 kHz units (12175 = 121.75 MHz)
"""

import base64
import copy
import json
import logging
import threading
import time
from typing import Callable, Optional

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:
    pass  # pragma: no cover

from xplane.connector import FlightState

log = logging.getLogger(__name__)

POLL_HZ       = 2      # target state poll rate when connected
PROBE_INTERVAL = 2.0   # seconds between connectivity probes

# Dataref name → FlightState field (or special key for string types)
_DATAREFS: dict[str, str] = {
    'sim/flightmodel/position/latitude':           'lat',
    'sim/flightmodel/position/longitude':          'lon',
    'sim/flightmodel/position/elevation':          'elevation_m',
    'sim/flightmodel/misc/h_ind':                  'alt_ind_ft',
    'sim/flightmodel/position/indicated_airspeed': 'ias_kts',
    'sim/flightmodel/position/groundspeed':        'groundspeed_ms',
    'sim/flightmodel/position/psi':                'heading_true',
    'sim/flightmodel/position/mag_psi':            'heading_mag',
    'sim/flightmodel/failures/onground_any':       'on_ground',
    'sim/time/paused':                             'paused',
    'sim/cockpit/radios/com1_freq_hz':             'com1_raw',
    'sim/cockpit/radios/com2_freq_hz':             'com2_raw',
    'sim/cockpit/radios/transponder_code':         'transponder',
    'sim/aircraft/view/acf_ICAO':                  '_acf_icao_str',
    'sim/aircraft/view/acf_tailnum':               '_tail_str',
    # Weather
    'sim/weather/barometer_sealevel_inhg':         'qnh_inhg',
    'sim/weather/wind_speed_kt':                   'wind_speed_kts',   # array[0]
    'sim/weather/wind_direction_degt':             'wind_dir_deg',     # array[0]
}

# Array datarefs that need ?index=N when fetching the value
_ARRAY_IDX: dict[str, int] = {
    'sim/weather/wind_speed_kt':       0,
    'sim/weather/wind_direction_degt': 0,
}


def _http_get(url: str, timeout: float = 2.0) -> dict:
    req = Request(url, headers={'Accept': 'application/json'})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_get_or_none(url: str, timeout: float = 2.0) -> Optional[dict]:
    """GET url and return parsed JSON, or None if the server says 404.

    X-Plane returns HTTP 404 (not an empty list) when a filter[name] query
    finds no matching datarefs. Callers that want to distinguish "not found
    yet" from hard errors should use this instead of _http_get.
    """
    try:
        return _http_get(url, timeout=timeout)
    except Exception as exc:
        # urllib raises HTTPError for non-2xx; check code attribute
        code = getattr(exc, 'code', None)
        if code == 404:
            return None       # dataref simply not registered yet
        raise               # propagate real network/timeout errors


def _extract_data(response: dict):
    """Extract the 'data' field from a REST response.

    All successful responses from X-Plane 12.1.1+ wrap their payload:
      {"data": <value_or_list>}
    Returns the unwrapped value (may be scalar, list, or dict).
    Returns None if the field is missing or the response looks like an error.
    """
    return response.get('data')


def _extract_datarefs(data: dict) -> list:
    """Return the list of dataref objects from a filter/list response."""
    items = _extract_data(data)
    if isinstance(items, list):
        return items
    return []


def _decode_string(value) -> str:
    """Decode a REST 'data' value: base64 bytes, plain string, or char-code array."""
    if isinstance(value, str):
        # X-Plane encodes byte-type datarefs as base64. Try to decode, but only
        # accept the result if it survives strict ASCII decoding (no replacement chars)
        # and contains only printable printable ASCII (32–126). Plain identifier strings
        # like "C172" or "D-EIYD" decode to garbage bytes that fail this check, so they
        # fall through and are returned verbatim.
        try:
            padded = value + '=' * ((-len(value)) % 4)
            raw = base64.b64decode(padded)
            decoded = raw.decode('ascii').rstrip('\x00').strip()
            if decoded and all(32 <= ord(c) < 127 for c in decoded):
                return decoded
        except Exception:
            pass
        return value.rstrip('\x00').strip()
    if isinstance(value, list):
        return ''.join(chr(int(c)) for c in value if 32 < c < 127).strip()
    return str(value).strip()


def _str_to_chars(s: str, length: int) -> list:
    chars = [float(ord(c)) for c in s[:length]]
    chars += [0.0] * (length - len(chars))
    return chars


def encode_fixed_string(s: str, length: int) -> str:
    """Base64-encode a string for a fixed-length char-array dataref.

    X-Plane char datarefs (e.g. acf_tailnum is char[40]) are fixed-width, and a
    REST PATCH overwrites only the bytes it's given — it does NOT clear the rest.
    Writing a short value over a longer one therefore leaves the old tail's
    trailing bytes intact ("D-EIYD" then "ABC" reads back as "ABCYD"). Null-pad
    to the full width so the whole array is overwritten. Truncates if longer.
    """
    raw = s.encode('ascii')[:length].ljust(length, b'\x00')
    return base64.b64encode(raw).decode()


class XPlaneRestConnector:
    """
    Polls X-Plane's local REST API (port 8086) for flight data.

    Starts a daemon thread that probes until connected, then polls at 2 Hz.
    Thread-safe: all state reads go through the .state property which returns
    a deepcopy.

    Args:
        host:             X-Plane host (almost always 127.0.0.1)
        port:             REST API port (default 8086)
        ptt_dataref:      Optional PTT dataref path, e.g.
                          "sim/joystick/joystick_button_array[32]"
        on_connected:     Callable fired (from the background thread) when
                          X-Plane first responds to a probe.
        on_disconnected:  Callable fired when a previously-connected poll fails.
    """

    def __init__(self,
                 host: str = '127.0.0.1',
                 port: int = 8086,
                 ptt_dataref: str = '',
                 on_connected: Optional[Callable[[], None]] = None,
                 on_disconnected: Optional[Callable[[], None]] = None):
        self._base = f'http://{host}:{port}'
        self._ptt_dataref = ptt_dataref
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        self._state = FlightState()
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._ids: dict[str, int] = {}       # dataref name → numeric API id
        self._ptt_id: Optional[int] = None   # numeric id for PTT dataref
        self._ptt_idx: Optional[int] = None  # array index within PTT dataref
        self._thread: Optional[threading.Thread] = None
        self._logged_flight: bool = False    # have we logged the first valid state?
        self._last_acf: str = ''             # track aircraft identity changes
        self._ptt_retry_at: float = 0.0      # monotonic time for next PTT re-discovery
        self._discover_retry_at: float = 0.0 # monotonic time for next dataref re-discovery

    # ------------------------------------------------------------------ #
    # FlightDataSource protocol

    @property
    def state(self) -> FlightState:
        with self._lock:
            return copy.deepcopy(self._state)

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='xplane-rest')
        self._thread.start()
        log.info(f"X-Plane REST connector started — probing {self._base}")

    def stop(self) -> None:
        self._running = False

    def write_dataref(self, name: str, value) -> bool:
        """PATCH a dataref value. Returns True on success.

        value must already be in the correct REST format:
          - scalar float/int  → plain Python number
          - string dataref    → base64-encoded bytes (str)
        """
        ref_id = self._ids.get(name)
        if ref_id is None:
            log.warning(f"write_dataref: '{name}' not in discovered IDs — cannot write")
            return False
        try:
            payload = json.dumps({'data': value}).encode()
            req = Request(
                f'{self._base}/api/v3/datarefs/{ref_id}/value',
                data=payload,
                method='PATCH',
                headers={
                    'Accept':       'application/json',
                    'Content-Type': 'application/json',
                },
            )
            with urlopen(req, timeout=3.0):
                pass
            log.debug(f"write_dataref {name} = {repr(value)!s:.60}")
            return True
        except Exception as exc:
            log.warning(f"write_dataref {name} failed: {exc}")
            return False

    # ------------------------------------------------------------------ #
    # Background thread

    def _run(self) -> None:
        while self._running:
            if self._connected:
                try:
                    self._poll()
                    time.sleep(1.0 / POLL_HZ)
                except Exception as exc:
                    log.warning(f"REST poll error — disconnected: {exc}")
                    self._connected = False
                    with self._lock:
                        self._state = FlightState()
                    self._ids.clear()
                    self._ptt_id = None
                    if self._on_disconnected:
                        try:
                            self._on_disconnected()
                        except Exception:
                            pass
            else:
                self._probe()

    def _probe(self) -> None:
        # GET /api/capabilities was added in X-Plane 12.1.4 (API v2).
        # Fall back to GET /api/v1/datarefs/count for older 12.1.1 installations.
        version = None
        try:
            data = _http_get(f'{self._base}/api/capabilities', timeout=2.0)
            if 'x-plane' in data:
                version = data['x-plane'].get('version', 'unknown')
        except Exception:
            pass

        if version is None:
            try:
                data = _http_get(f'{self._base}/api/v1/datarefs/count', timeout=2.0)
                if 'data' in data:
                    version = 'unknown (v1 only)'
            except Exception:
                pass

        if version is not None:
            log.info(f"X-Plane {version} REST API connected at {self._base}")
            self._discover()
            self._connected = True
            if self._on_connected:
                try:
                    self._on_connected()
                except Exception:
                    pass
            return
        time.sleep(PROBE_INTERVAL)

    def _resolve_dataref(self, name: str) -> bool:
        """Resolve one dataref name to its numeric session id (cached in _ids).
        Returns True if found."""
        try:
            data = _http_get(f'{self._base}/api/v3/datarefs?filter[name]={name}')
            for ref in _extract_datarefs(data):
                if ref.get('name') == name:
                    self._ids[name] = ref['id']
                    log.debug(f"  dataref {name} → id {ref['id']} ({ref.get('value_type', '?')})")
                    return True
        except Exception as exc:
            log.debug(f"Discovery failed for {name}: {exc}")
        return False

    def _discover(self) -> None:
        """Look up the numeric session ID for every needed dataref.

        X-Plane exposes the REST API before the flight-model datarefs are
        registered — at the main menu, before a flight loads, a first pass can
        resolve nothing (0/18). That's expected; _poll() keeps retrying the
        missing ones via _retry_dataref_discovery() until the flight loads, so
        position (and therefore airport detection) recovers on its own."""
        for name in _DATAREFS:
            self._resolve_dataref(name)
        found   = sum(1 for n in _DATAREFS if n in self._ids)
        missing = [n for n in _DATAREFS if n not in self._ids]
        log.info(f"Dataref discovery: {found}/{len(_DATAREFS)} resolved")
        if missing:
            log.info(
                f"  {len(missing)} not registered yet (X-Plane may be at the menu, "
                f"no flight loaded) — retrying every 5 s until they appear"
            )

        # PTT dataref (optional; may include array index like "...[32]")
        if self._ptt_dataref:
            ptt_name = self._ptt_dataref
            ptt_idx: Optional[int] = None
            if '[' in ptt_name:
                ptt_name, bracket = ptt_name.split('[', 1)
                try:
                    ptt_idx = int(bracket.rstrip(']'))
                except ValueError:
                    pass
            try:
                from urllib.parse import quote as _quote
                encoded = _quote(ptt_name, safe='')
                data = _http_get_or_none(f'{self._base}/api/v3/datarefs?filter[name]={encoded}')
                if data is None:
                    log.info(
                        f"PTT dataref '{ptt_name}' not yet registered — "
                        f"will retry every 10 s (FlyWithLua may still be loading)"
                    )
                else:
                    for ref in _extract_datarefs(data):
                        if ref.get('name') == ptt_name:
                            self._ptt_id = ref['id']
                            self._ptt_idx = ptt_idx
                            log.info(
                                f"PTT dataref {ptt_name} → id {self._ptt_id}"
                                + (f" [index {ptt_idx}]" if ptt_idx is not None else "")
                            )
                            break
                    else:
                        log.info(
                            f"PTT dataref '{ptt_name}' not yet registered — "
                            f"will retry every 10 s"
                        )
            except Exception as exc:
                log.warning(f"PTT discovery failed unexpectedly: {exc}")

    def _retry_ptt_discovery(self) -> None:
        """Re-attempt PTT discovery. Called from the poll loop when not yet found."""
        if not self._ptt_dataref or self._ptt_id is not None:
            return
        now = time.monotonic()
        if now < self._ptt_retry_at:
            return
        self._ptt_retry_at = now + 10.0   # retry at most every 10 s

        ptt_name = self._ptt_dataref
        ptt_idx: Optional[int] = None
        if '[' in ptt_name:
            ptt_name, bracket = ptt_name.split('[', 1)
            try:
                ptt_idx = int(bracket.rstrip(']'))
            except ValueError:
                pass
        try:
            from urllib.parse import quote as _quote
            encoded = _quote(ptt_name, safe='')
            data = _http_get_or_none(f'{self._base}/api/v3/datarefs?filter[name]={encoded}')
            if data is not None:
                for ref in _extract_datarefs(data):
                    if ref.get('name') == ptt_name:
                        self._ptt_id  = ref['id']
                        self._ptt_idx = ptt_idx
                        log.info(
                            f"PTT dataref found (late): {ptt_name} → id {self._ptt_id}"
                            + (f" [index {ptt_idx}]" if ptt_idx is not None else "")
                        )
                        return
            log.debug(f"PTT re-discovery: '{ptt_name}' not yet registered")
        except Exception as exc:
            log.debug(f"PTT re-discovery error: {exc}")

    def _retry_dataref_discovery(self) -> None:
        """Re-resolve flight datarefs that weren't registered at connect time —
        discovery often runs while X-Plane is still at the menu, before a flight
        loads, so the first pass resolves nothing. Throttled to every 5 s."""
        missing = [n for n in _DATAREFS if n not in self._ids]
        if not missing:
            return
        now = time.monotonic()
        if now < self._discover_retry_at:
            return
        self._discover_retry_at = now + 5.0
        newly = sum(1 for n in missing if self._resolve_dataref(n))
        if newly:
            have = sum(1 for n in _DATAREFS if n in self._ids)
            log.info(
                f"Dataref discovery (late): resolved {newly} more "
                f"— {have}/{len(_DATAREFS)} now available"
            )

    def _poll(self) -> None:
        """Fetch all cached datarefs and update FlightState atomically."""
        self._retry_ptt_discovery()
        self._retry_dataref_discovery()

        with self._lock:
            old = copy.deepcopy(self._state)

        new = FlightState()
        # Carry forward string data so a transient failure doesn't blank callsign
        acf_icao = old.acf_icao
        tail     = old.tail_number

        for name, field in _DATAREFS.items():
            ref_id = self._ids.get(name)
            if ref_id is None:
                continue
            try:
                idx = _ARRAY_IDX.get(name)
                url = f'{self._base}/api/v3/datarefs/{ref_id}/value'
                if idx is not None:
                    url += f'?index={idx}'
                data  = _http_get(url)
                value = _extract_data(data)
                if value is None:
                    continue

                if field == '_acf_icao_str':
                    acf_icao = _decode_string(value)
                elif field == '_tail_str':
                    tail = _decode_string(value)
                else:
                    setattr(new, field, float(value))
            except Exception:
                pass  # keep previous value for this field

        new._icao_chars = _str_to_chars(acf_icao, 4)
        new._tail_chars = _str_to_chars(tail, 10)

        # Log first valid state and aircraft identity changes
        if new.is_flight_loaded:
            acf_id = f"{acf_icao}/{tail}" if tail else acf_icao
            if not self._logged_flight:
                log.info(
                    f"First valid state received: "
                    f"{acf_icao or '?'} {tail or ''} "
                    f"at {new.lat:.4f},{new.lon:.4f} "
                    f"alt {new.alt_ind_ft:.0f} ft "
                    f"hdg {new.heading_mag:.0f}° "
                    f"{'on ground' if new.on_ground > 0.5 else 'airborne'}"
                )
                self._logged_flight = True
                self._last_acf = acf_id
            elif acf_id != self._last_acf and acf_icao:
                log.info(f"Aircraft changed: {self._last_acf} → {acf_id}")
                self._last_acf = acf_id
        else:
            # Flight unloaded (back to menu) — reset so we log again on next load
            if self._logged_flight:
                log.info("Flight unloaded (position reset to 0,0)")
                self._logged_flight = False

        # PTT button — use ?index= to fetch just the one element from the array
        if self._ptt_id is not None:
            try:
                if self._ptt_idx is not None:
                    url = (f'{self._base}/api/v3/datarefs/{self._ptt_id}'
                           f'/value?index={self._ptt_idx}')
                else:
                    url = f'{self._base}/api/v3/datarefs/{self._ptt_id}/value'
                data  = _http_get(url)
                value = _extract_data(data)
                if value is not None:
                    new.ptt_pressed = float(value)
            except Exception:
                pass

        with self._lock:
            self._state = new
