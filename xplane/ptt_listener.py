"""
Low-latency PTT (push-to-talk) listener over the X-Plane WebSocket API.

Why this exists separately from XPlaneRestConnector:
  Flight state is fine polled at 2 Hz, but PTT is a momentary signal. Polling
  it on the same 2 Hz loop adds up to ~500 ms latency on both the press and the
  release (clipping the front of a transmission) and can miss a quick tap that
  falls entirely between two polls. X-Plane's WebSocket API (12.1.4+) instead
  *pushes* an event the instant the state changes, so we subscribe and react.

What it can watch (auto-detected from the configured name):
  - A readable dataref, e.g. "xpilot/ptt" (xPilot publishes an int dataref that
    holds 1 for the whole press), or a joystick button like
    "sim/joystick/joystick_button_array[32]". This is the reliable choice for
    hold-to-talk: the value stays set while the key is held.
  - A command, e.g. "sim/operation/contact_atc_ptt". Commands are NOT readable
    as datarefs — they only exist on the WebSocket via command_subscribe_is_active,
    which is why pointing the old dataref-polling path at a command name never
    fired. Caveat: most ATC command key/button bindings fire as a one-shot pulse
    (begin+end in the same instant), so they can't drive a sustained PTT hold.
    The listener still subscribes, but logs a warning recommending a dataref.

The listener auto-discovers which kind the name is, subscribes accordingly, and
fires an async callback with a bool on every press/release edge.

Protocol (X-Plane local Web API, ws://host:port/api/v2):
  command_subscribe_is_active → command_update_is_active {"<id>": true/false}
  dataref_subscribe_values    → dataref_update_values    {"<id>": <value>}
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

import websockets

log = logging.getLogger(__name__)

# Re-discover ids and reconnect this many seconds after a failure / missing ref.
_RETRY_INTERVAL = 5.0


def _parse_index(name: str) -> tuple[str, Optional[int]]:
    """Split a trailing array subscript: "foo/bar[32]" → ("foo/bar", 32)."""
    if name.endswith(']') and '[' in name:
        base, bracket = name.rsplit('[', 1)
        try:
            return base, int(bracket[:-1])
        except ValueError:
            pass
    return name, None


class PTTListener:
    """
    Watches a PTT command or dataref over the X-Plane WebSocket and calls
    on_change(pressed: bool) on every edge.

    Args:
        host:       X-Plane host (almost always 127.0.0.1)
        port:       REST/WebSocket port (default 8086)
        ptt_source: command or dataref name, optionally with [index]
        on_change:  async callable invoked with True on press, False on release
    """

    def __init__(self,
                 host: str,
                 port: int,
                 ptt_source: str,
                 on_change: Callable[[bool], Awaitable[None]]):
        self._base_http = f'http://{host}:{port}'
        self._ws_url = f'ws://{host}:{port}/api/v2'
        self._source = ptt_source
        self._on_change = on_change
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._pressed = False

    @property
    def pressed(self) -> bool:
        return self._pressed

    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name='ptt-listener')
        log.info(f"PTT listener started — watching '{self._source}' via WebSocket")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Surface a release if we go down mid-press so the recorder can't stick.
        if self._pressed:
            self._pressed = False
            try:
                await self._on_change(False)
            except Exception:
                pass

    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        while self._running:
            try:
                kind, ref_id, index = await asyncio.to_thread(self._discover)
                if ref_id is None:
                    log.info(
                        f"PTT source '{self._source}' not found yet "
                        f"(plugin still loading?) — retrying in {_RETRY_INTERVAL:.0f}s"
                    )
                    await asyncio.sleep(_RETRY_INTERVAL)
                    continue
                await self._listen(kind, ref_id, index)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(f"PTT listener error ({exc}) — reconnecting in {_RETRY_INTERVAL:.0f}s")
                await asyncio.sleep(_RETRY_INTERVAL)

    def _discover(self) -> tuple[Optional[str], Optional[int], Optional[int]]:
        """Resolve the source name to (kind, id, index).

        kind is 'dataref' or 'command'. Datarefs win if a name resolves to both
        (a readable dataref is cheaper to subscribe and needs no index gymnastics
        on the command side). Runs in a worker thread — uses blocking urllib.
        """
        name, index = _parse_index(self._source)
        encoded = quote(name, safe='')

        ref = self._lookup(f'{self._base_http}/api/v3/datarefs?filter[name]={encoded}', name)
        if ref is not None:
            log.info(f"PTT source '{name}' is a dataref → id {ref}"
                     + (f" [index {index}]" if index is not None else ""))
            return 'dataref', ref, index

        ref = self._lookup(f'{self._base_http}/api/v2/commands?filter[name]={encoded}', name)
        if ref is not None:
            log.warning(
                f"PTT source '{name}' is a COMMAND (id {ref}), not a dataref. "
                f"Most key/button bindings fire ATC commands as a one-shot pulse "
                f"(press+release in the same instant), so hold-to-talk won't work. "
                f"Prefer a dataref that holds while pressed, e.g. XPLANE_PTT_DATAREF=xpilot/ptt "
                f"bound to the 'xPilot: Radio Push-to-Talk' command."
            )
            return 'command', ref, None

        return None, None, None

    @staticmethod
    def _lookup(url: str, name: str) -> Optional[int]:
        """GET a filtered list endpoint and return the id whose name matches exactly."""
        try:
            req = Request(url, headers={'Accept': 'application/json'})
            with urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read()).get('data', [])
        except Exception:
            return None
        for item in data:
            if item.get('name') == name:
                return item.get('id')
        return None

    async def _listen(self, kind: str, ref_id: int, index: Optional[int]) -> None:
        if kind == 'command':
            sub = {
                "req_id": 1, "type": "command_subscribe_is_active",
                "params": {"commands": [{"id": ref_id}]},
            }
            update_type = "command_update_is_active"
        else:
            dref: dict = {"id": ref_id}
            if index is not None:
                dref["index"] = index
            sub = {
                "req_id": 1, "type": "dataref_subscribe_values",
                "params": {"datarefs": [dref]},
            }
            update_type = "dataref_update_values"

        key = str(ref_id)
        async with websockets.connect(self._ws_url) as ws:
            await ws.send(json.dumps(sub))
            log.info(f"PTT subscribed ({kind} {ref_id}) — listening for edges")
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if msg.get("type") != update_type:
                    continue
                if key not in msg.get("data", {}):
                    continue
                await self._apply(msg["data"][key])

    async def _apply(self, value) -> None:
        # command → bool; dataref → number (or 1-element list when an index was given)
        if isinstance(value, list):
            value = value[0] if value else 0
        try:
            pressed = bool(value) if isinstance(value, bool) else float(value) > 0.5
        except (TypeError, ValueError):
            return
        if pressed == self._pressed:
            return
        self._pressed = pressed
        await self._on_change(pressed)
