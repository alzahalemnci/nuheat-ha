"""Tests for the Nuheat Conductor config flow."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.nuheat_conductor.config_flow import token_subject
from custom_components.nuheat_conductor.const import API_BASE, DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .conftest import ACCOUNT_SUB, CLIENT_ID, make_jwt

REDIRECT = "https://example.com/auth/external/callback"


async def _authorize(hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator, flow_id: str, url: str) -> None:
    """Assert the authorize URL, then simulate the browser callback."""
    state = config_entry_oauth2_flow._encode_jwt(hass, {"flow_id": flow_id, "redirect_uri": REDIRECT})
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == OAUTH2_AUTHORIZE
    assert query["client_id"] == CLIENT_ID
    assert query["response_type"] == "code"
    assert query["redirect_uri"] == REDIRECT
    assert query["state"] == state
    assert query["scope"] == "openid profile openapi offline_access"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200


def _mock_token(aioclient_mock: AiohttpClientMocker, sub: str = ACCOUNT_SUB) -> None:
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "access_token": make_jwt(sub),
            "refresh_token": "refresh-1",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile openapi offline_access",
        },
    )


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_full_flow(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Sign in creates an entry keyed by the account subject, titled by user name."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await _authorize(hass, hass_client_no_auth, result["flow_id"], result["url"])

    _mock_token(aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/Account", json={"userName": "user@example.com", "temperatureScale": "Fahrenheit"})

    with patch("custom_components.nuheat_conductor.async_setup_entry", return_value=True) as mock_setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == ACCOUNT_SUB
    assert result["title"] == "user@example.com"
    assert result["data"]["token"]["refresh_token"] == "refresh-1"
    assert len(mock_setup.mock_calls) == 1

    token_call = next(call for call in aioclient_mock.mock_calls if str(call[1]) == OAUTH2_TOKEN)
    body = token_call[2]
    assert body["client_secret"] == "test-client-secret"
    assert len(body["code_verifier"]) == 128


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_duplicate_account_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The same Nuheat account can't be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await _authorize(hass, hass_client_no_auth, result["flow_id"], result["url"])
    _mock_token(aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/Account", json={"userName": "user@example.com"})

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_account_read_failure_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """If the API won't answer with the new token, don't create a broken entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await _authorize(hass, hass_client_no_auth, result["flow_id"], result["url"])
    _mock_token(aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/Account", status=500)

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.parametrize(
    ("sub", "reason", "expected_refresh"),
    [(ACCOUNT_SUB, "reauth_successful", "refresh-1"), ("someone-else", "wrong_account", "refresh-old")],
)
@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    sub: str,
    reason: str,
    expected_refresh: str,
) -> None:
    """Reauth updates tokens for the same account and refuses a different one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_SUB,
        data={"auth_implementation": DOMAIN, "token": {"access_token": "old", "refresh_token": "refresh-old"}},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await _authorize(hass, hass_client_no_auth, result["flow_id"], result["url"])

    _mock_token(aioclient_mock, sub)
    aioclient_mock.get(f"{API_BASE}/Account", json={"userName": "user@example.com"})
    with patch("custom_components.nuheat_conductor.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert entry.data["token"]["refresh_token"] == expected_refresh


@pytest.mark.parametrize("token", ["not-a-jwt", "a.!!!.c", f"{make_jwt('x').split('.')[0]}.e30.sig"])
def test_token_subject_rejects_garbage(token: str) -> None:
    """Malformed tokens or ones without `sub` return None."""
    assert token_subject(token) is None


def test_token_subject() -> None:
    """Subject is read from the payload."""
    assert token_subject(make_jwt(ACCOUNT_SUB)) == ACCOUNT_SUB


async def test_options_flow_fahrenheit(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """°F user sets 60–85; stored in °C; defaults show the device range first."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema}
    assert defaults == {"min_temp": 41.0, "max_temp": 158.0}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"min_temp": 85, "max_temp": 60})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "min_not_below_max"}

    with patch("custom_components.nuheat_conductor.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"min_temp": 60, "max_temp": 85})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {"min_temp": 15.56, "max_temp": 29.44}
