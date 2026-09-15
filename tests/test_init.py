"""Tests for Nuheat Conductor setup, token refresh, and unload."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import time
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nuheat_conductor.const import API_BASE, DOMAIN, OAUTH2_TOKEN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import thermostat_json


@pytest.mark.usefixtures("mock_thermostat_api")
async def test_setup_and_unload(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    mock_events: AsyncMock,
) -> None:
    """Loads, starts the hub task, and unloads cleanly."""
    assert await integration_setup()
    assert config_entry.state is ConfigEntryState.LOADED
    assert mock_events.await_count == 1

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("expires_at", [time.time() - 60])
async def test_expired_token_is_refreshed_and_rotated(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """One-time refresh tokens: the rotated token must be persisted."""
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={"access_token": "new-access", "refresh_token": "refresh-2", "expires_in": 3600, "token_type": "Bearer"},
    )
    aioclient_mock.get(f"{API_BASE}/Thermostat", json=[thermostat_json()])

    assert await integration_setup()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data["token"]["access_token"] == "new-access"
    assert config_entry.data["token"]["refresh_token"] == "refresh-2"


@pytest.mark.parametrize(
    ("status", "expected_state", "expect_reauth"),
    [
        (400, ConfigEntryState.SETUP_ERROR, True),
        (500, ConfigEntryState.SETUP_RETRY, False),
    ],
    ids=["invalid_grant", "server_error"],
)
@pytest.mark.parametrize("expires_at", [time.time() - 60])
async def test_refresh_failure(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected_state: ConfigEntryState,
    expect_reauth: bool,
) -> None:
    """A dead refresh token (e.g. the 30-day cap) prompts reauth; outages retry."""
    aioclient_mock.post(OAUTH2_TOKEN, status=status, json={"error": "invalid_grant"})

    await integration_setup()
    assert config_entry.state is expected_state
    flows = [f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN]
    assert bool(flows) is expect_reauth
    if expect_reauth:
        assert flows[0]["context"]["source"] == "reauth"


async def test_api_401_triggers_reauth(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An access token the API rejects also leads to reauth."""
    aioclient_mock.get(f"{API_BASE}/Thermostat", status=401)
    await integration_setup()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_api_outage_retries(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 5xx at startup retries setup rather than failing."""
    aioclient_mock.get(f"{API_BASE}/Thermostat", status=503)
    await integration_setup()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
