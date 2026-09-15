"""Fixtures for Nuheat Conductor tests."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Generator
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nuheat_conductor.const import API_BASE, DOMAIN, OAUTH2_SCOPES
from homeassistant.components.application_credentials import (
    DOMAIN as APPLICATION_CREDENTIALS_DOMAIN,
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
ACCOUNT_SUB = "11111111-2222-3333-4444-555555555555"
SERIAL = "00000000"


def make_jwt(sub: str) -> str:
    """Unsigned JWT with just a subject — enough for unique_id extraction."""

    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc({'sub': sub})}.sig"


def thermostat_json(**overrides: Any) -> dict[str, Any]:
    """A thermostat response shaped like the live API's (captured 2026-09-14)."""
    data = {
        "serialNumber": SERIAL,
        "name": "bathroom",
        "currentTemperature": 2048,
        "online": True,
        "isHeating": False,
        "setPointTemperature": 1560,
        "holdUntil": None,
        "mode": 1,
        "errorState": None,
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load custom_components/ in every test."""


@pytest.fixture
def expires_at() -> float:
    """Token expiry; override to force a refresh."""
    return time.time() + 3600


@pytest.fixture
def config_entry(expires_at: float) -> MockConfigEntry:
    """A configured account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id=ACCOUNT_SUB,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": make_jwt(ACCOUNT_SUB),
                "refresh_token": "refresh-1",
                "scope": " ".join(OAUTH2_SCOPES),
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": expires_at,
            },
        },
    )


@pytest.fixture
async def setup_credentials(hass: HomeAssistant) -> None:
    """Store the Chemelex client credential."""
    assert await async_setup_component(hass, APPLICATION_CREDENTIALS_DOMAIN, {})
    await async_import_client_credential(hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN)


@pytest.fixture
def mock_events() -> Generator[AsyncMock]:
    """Keep the hub task from opening a real socket during HA tests."""
    with patch("custom_components.nuheat_conductor.events.NuheatEvents.run", new_callable=AsyncMock) as run:
        yield run


@pytest.fixture
def mock_thermostat_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Default API responses for a healthy thermostat."""
    aioclient_mock.get(f"{API_BASE}/Thermostat", json=[thermostat_json()])
    aioclient_mock.get(f"{API_BASE}/Thermostat/{SERIAL}", json=thermostat_json())
    return aioclient_mock


@pytest.fixture
def integration_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    setup_credentials: None,
    mock_events: AsyncMock,
) -> Callable[[], Awaitable[bool]]:
    """Set up the config entry."""
    config_entry.add_to_hass(hass)

    async def run() -> bool:
        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return result

    return run
