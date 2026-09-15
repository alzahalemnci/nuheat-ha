"""Minimal SignalR (JSON protocol) client for Nuheat change notifications.

Only the subset the hub needs: handshake, one Subscribe invocation, Notify
invocations, pings and close. Notices carry no state and are duplicated and
reordered by the server, so each one is only a "re-read this thermostat" hint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .api import TokenGetter
from .const import (
    HUB_BACKOFF_MAX_SECONDS,
    HUB_BACKOFF_MIN_SECONDS,
    HUB_PING_SECONDS,
    HUB_RECONNECT_SECONDS,
    HUB_URL,
    NOTIFY_DEBOUNCE_SECONDS,
)
from .exceptions import NuheatApiError, NuheatAuthError

_LOGGER = logging.getLogger(__name__)

RECORD_SEPARATOR = "\x1e"
NOTIFICATION_TYPE_THERMOSTAT = 2

_MSG_INVOCATION = 1
_MSG_COMPLETION = 3
_MSG_PING = 6
_MSG_CLOSE = 7

# A connection that stayed up this long resets the backoff.
_STABLE_SECONDS = 60


def _frame(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":")) + RECORD_SEPARATOR


class NuheatEvents:
    """Keeps a hub connection alive and reports which thermostats changed."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_getter: TokenGetter,
        on_change: Callable[[str], None],
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
        *,
        hub_url: str = HUB_URL,
        debounce: float = NOTIFY_DEBOUNCE_SECONDS,
        reconnect_after: float = HUB_RECONNECT_SECONDS,
    ) -> None:
        """Initialize; call `run()` as a long-lived task."""
        self._session = session
        self._token_getter = token_getter
        self._on_change = on_change
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._hub_url = hub_url
        self._debounce = debounce
        self._reconnect_after = reconnect_after
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self.connected = False

    async def run(self) -> None:
        """Connect, listen, and reconnect with backoff until cancelled."""
        backoff = HUB_BACKOFF_MIN_SECONDS
        try:
            while True:
                started = time.monotonic()
                try:
                    await self._connect_once()
                except NuheatAuthError as err:
                    _LOGGER.warning("Notification hub rejected the token: %s", err)
                except (NuheatApiError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                    _LOGGER.debug("Notification hub connection ended: %s", err)
                finally:
                    if self.connected:
                        self.connected = False
                        self._on_disconnect()
                if time.monotonic() - started >= _STABLE_SECONDS:
                    backoff = HUB_BACKOFF_MIN_SECONDS
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, HUB_BACKOFF_MAX_SECONDS)
        finally:
            for handle in self._pending.values():
                handle.cancel()
            self._pending.clear()

    async def _connect_once(self) -> None:
        token = await self._token_getter()
        url = f"{self._hub_url}?access_token={quote(token, safe='')}"
        try:
            ws = await self._session.ws_connect(
                url, headers={"Authorization": f"Bearer {token}"}, autoping=True
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise NuheatAuthError("Hub handshake returned 401") from err
            raise NuheatApiError(f"Hub handshake returned {err.status}") from err

        async with ws:
            await ws.send_str(_frame({"protocol": "json", "version": 1}))
            reply = await ws.receive(timeout=20)
            if reply.type is not aiohttp.WSMsgType.TEXT:
                raise NuheatApiError(f"Unexpected handshake reply {reply.type!r}")
            for frame in filter(None, reply.data.split(RECORD_SEPARATOR)):
                if error := json.loads(frame).get("error"):
                    raise NuheatApiError(f"Hub handshake error: {error}")

            await ws.send_str(
                _frame({
                    "type": _MSG_INVOCATION,
                    "invocationId": "subscribe",
                    "target": "Subscribe",
                    "arguments": [[NOTIFICATION_TYPE_THERMOSTAT]],
                })
            )
            self.connected = True
            self._on_connect()

            pinger = asyncio.create_task(self._ping(ws))
            deadline = time.monotonic() + self._reconnect_after
            try:
                while (remaining := deadline - time.monotonic()) > 0:
                    try:
                        msg = await ws.receive(timeout=remaining)
                    except TimeoutError:
                        _LOGGER.debug("Reconnecting hub to rotate access token")
                        return
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        if not self._handle_text(msg.data):
                            return
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                        return
                    elif msg.type is aiohttp.WSMsgType.ERROR:
                        raise NuheatApiError(f"Hub socket error: {ws.exception()}")
            finally:
                pinger.cancel()

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(HUB_PING_SECONDS)
            await ws.send_str(_frame({"type": _MSG_PING}))

    def _handle_text(self, data: str) -> bool:
        """Process a text message; return False when the hub asked to close."""
        for raw in filter(None, data.split(RECORD_SEPARATOR)):
            try:
                message = json.loads(raw)
            except ValueError:
                _LOGGER.debug("Ignoring non-JSON hub frame: %.200s", raw)
                continue
            kind = message.get("type")
            if kind == _MSG_INVOCATION and message.get("target") == "Notify":
                for notice in message.get("arguments") or []:
                    if isinstance(notice, dict) and notice.get("type") == NOTIFICATION_TYPE_THERMOSTAT and notice.get("id"):
                        self._schedule(str(notice["id"]))
            elif kind == _MSG_COMPLETION and message.get("error"):
                raise NuheatApiError(f"Hub invocation failed: {message['error']}")
            elif kind == _MSG_CLOSE:
                _LOGGER.debug("Hub closed the connection: %s", message.get("error"))
                return False
        return True

    def _schedule(self, serial: str) -> None:
        # Fire a fixed delay after the first notice rather than sliding the timer,
        # so a steady trickle of redeliveries can't postpone the refresh indefinitely.
        if serial in self._pending:
            return
        loop = asyncio.get_running_loop()
        self._pending[serial] = loop.call_later(self._debounce, self._fire, serial)

    def _fire(self, serial: str) -> None:
        self._pending.pop(serial, None)
        self._on_change(serial)
