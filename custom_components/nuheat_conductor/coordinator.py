"""Coordinator for Nuheat Conductor."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NuheatApi
from .const import DISCONNECTED_POLL_INTERVAL, DOMAIN, SAFETY_POLL_INTERVAL
from .events import NuheatEvents
from .exceptions import NuheatAuthError, NuheatError
from .models import Thermostat

_LOGGER = logging.getLogger(__name__)

type NuheatConfigEntry = ConfigEntry[NuheatCoordinator]


class NuheatCoordinator(DataUpdateCoordinator[dict[str, Thermostat]]):
    """Holds thermostat state; refreshed by hub notices, with a slow safety poll."""

    config_entry: NuheatConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NuheatConfigEntry, api: NuheatApi) -> None:
        """Initialize. Polls fast until the hub connects."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DISCONNECTED_POLL_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.events: NuheatEvents | None = None

    async def _async_update_data(self) -> dict[str, Thermostat]:
        try:
            thermostats = await self.api.async_get_thermostats()
        except NuheatAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NuheatError as err:
            raise UpdateFailed(str(err)) from err
        return {thermostat.serial: thermostat for thermostat in thermostats}

    async def async_refresh_thermostat(self, serial: str) -> None:
        """Re-read one thermostat and publish it."""
        if not self.data or serial not in self.data:
            await self.async_request_refresh()
            return
        try:
            thermostat = await self.api.async_get_thermostat(serial)
        except NuheatAuthError:
            self.config_entry.async_start_reauth(self.hass)
            return
        except NuheatError as err:
            # The safety poll will catch up; a failed targeted read isn't an outage.
            _LOGGER.debug("Refresh of thermostat %s failed: %s", serial, err)
            return
        self.async_set_updated_data({**self.data, serial: thermostat})

    @callback
    def handle_change(self, serial: str) -> None:
        """Hub reported a change for a thermostat."""
        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh_thermostat(serial), f"{DOMAIN} refresh {serial}"
        )

    @callback
    def handle_connect(self) -> None:
        """Hub connected: slow the poll and catch up on anything missed."""
        self.update_interval = SAFETY_POLL_INTERVAL
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} resync"
        )

    @callback
    def handle_disconnect(self) -> None:
        """Hub dropped: poll often until it's back."""
        self.update_interval = DISCONNECTED_POLL_INTERVAL
