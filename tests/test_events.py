"""Tests for the SignalR notification client against a local fake hub."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.nuheat_conductor import events as events_module
from custom_components.nuheat_conductor.events import RECORD_SEPARATOR, NuheatEvents

RS = RECORD_SEPARATOR

# The fake hub is a real loopback server; the HA harness blocks sockets by default.
pytestmark = pytest.mark.usefixtures("socket_enabled")

Script = Callable[[web.WebSocketResponse], Awaitable[None]]


def notify(serial: str, stamp: str) -> str:
    """A Notify invocation as the live hub sends it."""
    return json.dumps({"type": 1, "target": "Notify", "arguments": [{"type": 2, "id": serial, "timeStamp": stamp}]}) + RS


@asynccontextmanager
async def fake_hub(script: Script, *, status: int = 101) -> AsyncIterator[tuple[str, dict]]:
    """Serve a hub that performs the handshake, then runs `script`."""
    seen: dict = {"auth": [], "subscribe": [], "connections": 0}

    async def handler(request: web.Request) -> web.StreamResponse:
        if status != 101:
            return web.Response(status=status)
        seen["auth"].append(request.headers.get("Authorization"))
        seen["connections"] += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        handshake = await ws.receive_str()
        assert json.loads(handshake.rstrip(RS)) == {"protocol": "json", "version": 1}
        await ws.send_str("{}" + RS)
        seen["subscribe"].append(json.loads((await ws.receive_str()).rstrip(RS)))
        await script(ws)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/hub", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        yield f"ws://127.0.0.1:{server.port}/hub", seen
    finally:
        await server.close()


class Recorder:
    """Collects callbacks."""

    def __init__(self) -> None:
        self.changes: list[str] = []
        self.connects = 0
        self.disconnects = 0
        self.changed = asyncio.Event()

    def on_change(self, serial: str) -> None:
        self.changes.append(serial)
        self.changed.set()

    def on_connect(self) -> None:
        self.connects += 1

    def on_disconnect(self) -> None:
        self.disconnects += 1


async def _token() -> str:
    return "tok-1"


def make_client(session: aiohttp.ClientSession, url: str, rec: Recorder, **kwargs) -> NuheatEvents:
    return NuheatEvents(
        session, _token, rec.on_change, rec.on_connect, rec.on_disconnect, hub_url=url, debounce=0.05, **kwargs
    )


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_subscribes_and_collapses_duplicate_and_reordered_notices() -> None:
    """The burst seen live (dup + out-of-order) yields one refresh per serial."""
    release = asyncio.Event()

    async def script(ws: web.WebSocketResponse) -> None:
        await ws.send_str(notify("00000000", "2026-09-15T02:50:42.85+00:00"))
        await ws.send_str(notify("00000000", "2026-09-15T02:50:41.43+00:00") + notify("00000000", "2026-09-15T02:50:41.43+00:00"))
        await release.wait()

    rec = Recorder()
    async with fake_hub(script) as (url, seen), aiohttp.ClientSession() as session:
        client = make_client(session, url, rec)
        task = asyncio.create_task(client.run())
        await asyncio.wait_for(rec.changed.wait(), 2)
        await asyncio.sleep(0.2)
        assert rec.changes == ["00000000"]
        assert client.connected
        assert rec.connects == 1
        assert seen["auth"] == ["Bearer tok-1"]
        assert seen["subscribe"][0]["target"] == "Subscribe"
        assert seen["subscribe"][0]["arguments"] == [[2]]
        release.set()
        await _cancel(task)


async def test_non_thermostat_notices_ignored() -> None:
    """Only type 2 notices trigger refreshes."""
    release = asyncio.Event()

    async def script(ws: web.WebSocketResponse) -> None:
        await ws.send_str(json.dumps({"type": 1, "target": "Notify", "arguments": [{"type": 1, "id": "acct"}]}) + RS)
        await ws.send_str(json.dumps({"type": 6}) + RS)
        await ws.send_str(notify("999", "t"))
        await release.wait()

    rec = Recorder()
    async with fake_hub(script) as (url, _), aiohttp.ClientSession() as session:
        task = asyncio.create_task(make_client(session, url, rec).run())
        await asyncio.wait_for(rec.changed.wait(), 2)
        assert rec.changes == ["999"]
        release.set()
        await _cancel(task)


async def test_disconnect_reports_and_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server close → on_disconnect, then a fresh connection after backoff."""
    monkeypatch.setattr(events_module, "HUB_BACKOFF_MIN_SECONDS", 0.01)

    async def script(ws: web.WebSocketResponse) -> None:
        return

    rec = Recorder()
    async with fake_hub(script) as (url, seen), aiohttp.ClientSession() as session:
        task = asyncio.create_task(make_client(session, url, rec).run())
        for _ in range(100):
            if seen["connections"] >= 2:
                break
            await asyncio.sleep(0.02)
        assert seen["connections"] >= 2
        assert rec.disconnects >= 1
        await _cancel(task)


async def test_token_rotation_reconnects() -> None:
    """The socket is recycled before the bound token expires."""
    release = asyncio.Event()

    async def script(ws: web.WebSocketResponse) -> None:
        await release.wait()

    rec = Recorder()
    async with fake_hub(script) as (url, seen), aiohttp.ClientSession() as session:
        client = make_client(session, url, rec, reconnect_after=0.1)
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0.4)
        assert rec.disconnects >= 1
        release.set()
        await _cancel(task)


async def test_handshake_401_is_auth_error(caplog: pytest.LogCaptureFixture) -> None:
    """A rejected token is logged as a warning and retried, not raised."""

    async def script(ws: web.WebSocketResponse) -> None:
        return

    rec = Recorder()
    async with fake_hub(script, status=401) as (url, _), aiohttp.ClientSession() as session:
        task = asyncio.create_task(make_client(session, url, rec).run())
        await asyncio.sleep(0.2)
        assert "rejected the token" in caplog.text
        assert rec.connects == 0
        await _cancel(task)


async def test_hub_close_message_ends_connection() -> None:
    """A SignalR close frame drops the connection cleanly."""

    async def script(ws: web.WebSocketResponse) -> None:
        await ws.send_str(json.dumps({"type": 7, "error": "bye"}) + RS)
        await asyncio.sleep(1)

    rec = Recorder()
    async with fake_hub(script) as (url, _), aiohttp.ClientSession() as session:
        task = asyncio.create_task(make_client(session, url, rec).run())
        for _ in range(50):
            if rec.disconnects:
                break
            await asyncio.sleep(0.02)
        assert rec.disconnects == 1
        await _cancel(task)
