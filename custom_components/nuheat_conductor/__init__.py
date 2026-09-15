"""The Nuheat Conductor integration."""

from __future__ import annotations

from aiohttp import ClientError

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NuheatApi
from .const import DOMAIN
from .coordinator import NuheatConfigEntry, NuheatCoordinator
from .events import NuheatEvents
from .exceptions import NuheatApiError, NuheatAuthError

PLATFORMS: list[Platform] = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: NuheatConfigEntry) -> bool:
    """Set up Nuheat Conductor from a config entry."""
    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    except config_entry_oauth2_flow.ImplementationUnavailableError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="oauth2_implementation_unavailable",
        ) from err
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def _access_token() -> str:
        # The session persists each rotated refresh token into the entry; Chemelex
        # issues one-time refresh tokens, so refreshing anywhere else would log us out.
        try:
            await oauth_session.async_ensure_token_valid()
        except config_entry_oauth2_flow.OAuth2TokenRequestReauthError as err:
            raise NuheatAuthError("Refresh token rejected") from err
        except (config_entry_oauth2_flow.OAuth2TokenRequestError, ClientError) as err:
            raise NuheatApiError(f"Token refresh failed: {err}") from err
        return oauth_session.token[CONF_ACCESS_TOKEN]

    websession = async_get_clientsession(hass)
    coordinator = NuheatCoordinator(hass, entry, NuheatApi(websession, _access_token))
    await coordinator.async_config_entry_first_refresh()

    coordinator.events = NuheatEvents(
        websession,
        _access_token,
        coordinator.handle_change,
        coordinator.handle_connect,
        coordinator.handle_disconnect,
    )
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.events.run(), f"{DOMAIN} notification hub")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NuheatConfigEntry) -> bool:
    """Unload a config entry; the hub task is cancelled with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
