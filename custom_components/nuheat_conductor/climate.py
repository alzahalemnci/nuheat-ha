"""Climate platform for Nuheat Conductor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    DEVICE_MAX_TEMP_C,
    DEVICE_MIN_TEMP_C,
    DOMAIN,
    PRESET_MANUAL,
    PRESET_SCHEDULE,
    PRESET_TEMPORARY_HOLD,
)
from .coordinator import NuheatConfigEntry, NuheatCoordinator
from .exceptions import NuheatError
from .models import Mode, Thermostat

PARALLEL_UPDATES = 1

MODE_TO_PRESET = {
    Mode.AUTO: PRESET_SCHEDULE,
    Mode.HOLD: PRESET_TEMPORARY_HOLD,
    Mode.MANUAL: PRESET_MANUAL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NuheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity per thermostat."""
    coordinator = entry.runtime_data
    async_add_entities(NuheatClimate(coordinator, serial) for serial in coordinator.data)


class NuheatClimate(CoordinatorEntity[NuheatCoordinator], ClimateEntity):
    """A Nuheat floor-heating thermostat."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_preset_modes = [PRESET_SCHEDULE, PRESET_TEMPORARY_HOLD, PRESET_MANUAL]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: NuheatCoordinator, serial: str) -> None:
        """Initialize for one thermostat serial."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = serial
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=coordinator.data[serial].name,
            manufacturer="Nuheat",
            serial_number=serial,
        )

    @property
    def _thermostat(self) -> Thermostat:
        return self.coordinator.data[self._serial]

    @property
    def available(self) -> bool:
        """Unavailable when the API is failing or the thermostat is offline."""
        return super().available and self._serial in self.coordinator.data and self._thermostat.online

    @property
    def min_temp(self) -> float:
        """User preference if set, else the thermostat's floor."""
        return self.coordinator.config_entry.options.get(CONF_MIN_TEMP, DEVICE_MIN_TEMP_C)

    @property
    def max_temp(self) -> float:
        """User preference if set, else the thermostat's ceiling."""
        return self.coordinator.config_entry.options.get(CONF_MAX_TEMP, DEVICE_MAX_TEMP_C)

    @property
    def current_temperature(self) -> float:
        """Floor temperature."""
        return self._thermostat.current_temp_c

    @property
    def target_temperature(self) -> float:
        """Setpoint."""
        return self._thermostat.target_temp_c

    @property
    def hvac_action(self) -> HVACAction:
        """Heating or idle (lags the app by up to ~40 s server-side)."""
        return HVACAction.HEATING if self._thermostat.heating else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Schedule, temporary hold, or manual."""
        mode = self._thermostat.mode
        return MODE_TO_PRESET.get(mode) if mode is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When a temporary hold ends."""
        hold_until = self._thermostat.hold_until
        return {"hold_until": hold_until.isoformat() if hold_until else None}

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a setpoint the way the Nuheat app does: a temporary hold, unless in manual."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        if self._thermostat.mode is Mode.MANUAL:
            await self._call(self.coordinator.api.async_set_manual(self._serial, temperature))
        else:
            await self._call(self.coordinator.api.async_set_hold(self._serial, temperature))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch between schedule, temporary hold and manual."""
        api = self.coordinator.api
        target = self._thermostat.target_temp_c
        if preset_mode == PRESET_SCHEDULE:
            await self._call(api.async_set_auto(self._serial))
        elif preset_mode == PRESET_TEMPORARY_HOLD:
            await self._call(api.async_set_hold(self._serial, target))
        elif preset_mode == PRESET_MANUAL:
            await self._call(api.async_set_manual(self._serial, target))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Only heat exists; the API has no off."""
        if hvac_mode != HVACMode.HEAT:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="hvac_mode_not_supported")

    async def _call(self, request: Any) -> None:
        try:
            await request
        except NuheatError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_refresh_thermostat(self._serial)
