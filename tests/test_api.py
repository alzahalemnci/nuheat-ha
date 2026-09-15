"""Tests for the HA-agnostic API client and models."""

from __future__ import annotations

from datetime import UTC, datetime

import aiohttp
from aioresponses import CallbackResult, aioresponses
import pytest

from custom_components.nuheat_conductor.api import NuheatApi
from custom_components.nuheat_conductor.const import API_BASE
from custom_components.nuheat_conductor.exceptions import (
    NuheatApiError,
    NuheatAuthError,
    NuheatRateLimitedError,
)
from custom_components.nuheat_conductor.models import Mode, Thermostat, to_api_temp

from .conftest import SERIAL, thermostat_json


async def _token() -> str:
    return "tok"


@pytest.fixture
async def api() -> NuheatApi:
    """Client on a real session with aioresponses intercepting."""
    session = aiohttp.ClientSession()
    yield NuheatApi(session, _token)
    await session.close()


def test_model_parses_live_shape() -> None:
    """Hundredths of °C and the v2 mode enum."""
    t = Thermostat.from_api(thermostat_json(mode=2, holdUntil="2026-09-15T09:30:00+00:00", isHeating=True))
    assert t.serial == SERIAL
    assert t.current_temp_c == 20.48
    assert t.target_temp_c == 15.6
    assert t.mode is Mode.HOLD
    assert t.heating is True
    assert t.hold_until == datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


def test_model_tolerates_unknown_mode_and_bad_hold(caplog: pytest.LogCaptureFixture) -> None:
    """An undocumented mode or timestamp shouldn't break parsing."""
    t = Thermostat.from_api(thermostat_json(mode=9, holdUntil="not-a-date"))
    assert t.mode is None
    assert t.hold_until is None
    assert "unknown mode" in caplog.text


@pytest.mark.parametrize(("temp_c", "expected"), [(22.22, 2222), (16.11, 1611), (15.555, 1556)])
def test_to_api_temp(temp_c: float, expected: int) -> None:
    """°C to hundredths."""
    assert to_api_temp(temp_c) == expected


async def test_get_thermostats_sends_bearer(api: NuheatApi) -> None:
    """Bearer token header and list parsing."""
    seen: dict = {}

    def capture(url, **kwargs):
        seen.update(kwargs["headers"])
        return CallbackResult(payload=[thermostat_json()])

    with aioresponses() as mocked:
        mocked.get(f"{API_BASE}/Thermostat", callback=capture)
        thermostats = await api.async_get_thermostats()
    assert [t.serial for t in thermostats] == [SERIAL]
    assert seen["Authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    ("method", "path", "call", "body"),
    [
        ("PUT", "/Mode/Auto", lambda a: a.async_set_auto(SERIAL), {"serialNumber": SERIAL}),
        (
            "PUT",
            "/Mode/Hold",
            lambda a: a.async_set_hold(SERIAL, 22.22),
            {"serialNumber": SERIAL, "temperature": 2222, "temperatureType": 0, "holdUntil": None},
        ),
        (
            "PUT",
            "/Mode/Manual",
            lambda a: a.async_set_manual(SERIAL, 22.22),
            {"serialNumber": SERIAL, "temperature": 2222, "temperatureType": 0},
        ),
    ],
)
async def test_writes_match_proven_payloads(api: NuheatApi, method, path, call, body) -> None:
    """Payloads are exactly what the live thermostat accepted."""
    with aioresponses() as mocked:
        mocked.put(f"{API_BASE}{path}", status=204)
        assert await call(api) is None
        request = mocked.requests[(method, aiohttp.client.URL(f"{API_BASE}{path}"))][0]
    assert request.kwargs["json"] == body


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (401, {}, NuheatAuthError),
        (429, {"Retry-After": "30"}, NuheatRateLimitedError),
        (500, {}, NuheatApiError),
        (404, {}, NuheatApiError),
    ],
)
async def test_error_mapping(api: NuheatApi, status, headers, error) -> None:
    """HTTP errors map to typed exceptions."""
    with aioresponses() as mocked:
        mocked.get(f"{API_BASE}/Thermostat/{SERIAL}", status=status, headers=headers, body="nope")
        with pytest.raises(error) as info:
            await api.async_get_thermostat(SERIAL)
    if error is NuheatRateLimitedError:
        assert info.value.retry_after == 30


async def test_transport_error(api: NuheatApi) -> None:
    """Connection failures become NuheatApiError."""
    with aioresponses() as mocked:
        mocked.get(f"{API_BASE}/Account", exception=aiohttp.ClientConnectionError("down"))
        with pytest.raises(NuheatApiError):
            await api.async_get_account()
