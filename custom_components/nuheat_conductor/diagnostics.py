"""Diagnostics for Nuheat Conductor."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import NuheatConfigEntry

TO_REDACT = {"token", "access_token", "refresh_token", "id_token", "unique_id", "title", "auth_implementation"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NuheatConfigEntry) -> dict[str, Any]:
    """Return redacted entry data plus live coordinator state."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "hub_connected": coordinator.events.connected if coordinator.events else None,
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
        "thermostats": {serial: asdict(thermostat) for serial, thermostat in (coordinator.data or {}).items()},
    }
