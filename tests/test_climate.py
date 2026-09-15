"""Tests for the Nuheat Conductor climate entity."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nuheat_conductor.const import API_BASE
from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import SERIAL, thermostat_json

ENTITY_ID = "climate.bathroom"


def _puts(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, dict]]:
    return [(str(call[1]).removeprefix(API_BASE), call[2]) for call in aioclient_mock.mock_calls if call[0] == "PUT"]


def _serve(aioclient_mock: AiohttpClientMocker, **overrides) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API_BASE}/Thermostat", json=[thermostat_json(**overrides)])
    aioclient_mock.get(f"{API_BASE}/Thermostat/{SERIAL}", json=thermostat_json(**overrides))
    for path in ("Auto", "Hold", "Manual"):
        aioclient_mock.put(f"{API_BASE}/Mode/{path}", status=204)


async def test_state(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Entity mirrors the thermostat; ids match what the old core entity used."""
    _serve(aioclient_mock)
    await integration_setup()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes["current_temperature"] == 20.5
    assert state.attributes[ATTR_TEMPERATURE] == 15.6
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
    assert state.attributes[ATTR_PRESET_MODE] == "schedule"
    assert state.attributes[ATTR_PRESET_MODES] == ["schedule", "temporary_hold", "manual"]
    assert state.attributes["hold_until"] is None

    assert entity_registry.async_get(ENTITY_ID).unique_id == SERIAL
    device = device_registry.async_get_device(identifiers={("nuheat_conductor", SERIAL)})
    assert device.manufacturer == "Nuheat"
    assert device.serial_number == SERIAL


@pytest.mark.parametrize(
    ("mode", "expected_path", "expected_body"),
    [
        (1, "/Mode/Hold", {"serialNumber": SERIAL, "temperature": 2200, "temperatureType": 0, "holdUntil": None}),
        (2, "/Mode/Hold", {"serialNumber": SERIAL, "temperature": 2200, "temperatureType": 0, "holdUntil": None}),
        (3, "/Mode/Manual", {"serialNumber": SERIAL, "temperature": 2200, "temperatureType": 0}),
    ],
    ids=["from_schedule", "from_hold", "from_manual"],
)
async def test_set_temperature(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    mode: int,
    expected_path: str,
    expected_body: dict,
) -> None:
    """Like the app: temporary hold, unless already manual."""
    _serve(aioclient_mock, mode=mode)
    await integration_setup()

    _serve(aioclient_mock, mode=3 if mode == 3 else 2, setPointTemperature=2200)
    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 22}, blocking=True
    )
    assert _puts(aioclient_mock) == [(expected_path, expected_body)]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_TEMPERATURE] == 22.0


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        ("schedule", ("/Mode/Auto", {"serialNumber": SERIAL})),
        ("temporary_hold", ("/Mode/Hold", {"serialNumber": SERIAL, "temperature": 1560, "temperatureType": 0, "holdUntil": None})),
        ("manual", ("/Mode/Manual", {"serialNumber": SERIAL, "temperature": 1560, "temperatureType": 0})),
    ],
)
async def test_set_preset(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    preset: str,
    expected: tuple[str, dict],
) -> None:
    """Each preset hits its endpoint, holding the current setpoint."""
    _serve(aioclient_mock)
    await integration_setup()
    _serve(aioclient_mock)
    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_SET_PRESET_MODE, {ATTR_ENTITY_ID: ENTITY_ID, ATTR_PRESET_MODE: preset}, blocking=True
    )
    assert _puts(aioclient_mock) == [expected]


async def test_write_failure_raises(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected write surfaces as a service error."""
    _serve(aioclient_mock)
    await integration_setup()
    aioclient_mock.clear_requests()
    aioclient_mock.put(f"{API_BASE}/Mode/Hold", status=400, text="bad temperature")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 22}, blocking=True
        )


async def test_hvac_off_rejected(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """No off mode exists."""
    _serve(aioclient_mock)
    await integration_setup()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: ENTITY_ID, ATTR_HVAC_MODE: HVACMode.OFF}, blocking=True
        )


async def test_hub_notice_refreshes_entity(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """An app-side change arrives via the hub and updates the entity."""
    _serve(aioclient_mock)
    await integration_setup()

    _serve(aioclient_mock, mode=2, setPointTemperature=1607, isHeating=True, holdUntil="2026-09-15T09:30:00+00:00")
    config_entry.runtime_data.handle_change(SERIAL)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_PRESET_MODE] == "temporary_hold"
    assert state.attributes[ATTR_TEMPERATURE] == 16.1
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert state.attributes["hold_until"] == "2026-09-15T09:30:00+00:00"


async def test_connect_and_disconnect_switch_poll_interval(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """15 min safety poll while pushed; 60 s while the hub is down."""
    _serve(aioclient_mock)
    await integration_setup()
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 60
    coordinator.handle_connect()
    await hass.async_block_till_done()
    assert coordinator.update_interval.total_seconds() == 900
    coordinator.handle_disconnect()
    assert coordinator.update_interval.total_seconds() == 60


async def test_setpoint_range_defaults_to_device(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without preferences the thermostat's own 5–70 °C range applies."""
    _serve(aioclient_mock)
    await integration_setup()
    attrs = hass.states.get(ENTITY_ID).attributes
    assert (attrs["min_temp"], attrs["max_temp"]) == (5.0, 70.0)


async def test_setpoint_range_preference_enforced(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Options narrow the range and HA rejects setpoints outside it without calling the API."""
    hass.config_entries.async_update_entry(config_entry, options={"min_temp": 15.56, "max_temp": 29.44})
    _serve(aioclient_mock)
    await integration_setup()
    attrs = hass.states.get(ENTITY_ID).attributes
    assert (attrs["min_temp"], attrs["max_temp"]) == (15.6, 29.4)

    _serve(aioclient_mock)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TEMPERATURE: 35}, blocking=True
        )
    assert _puts(aioclient_mock) == []


async def test_offline_thermostat_unavailable(
    hass: HomeAssistant,
    integration_setup: Callable[[], Awaitable[bool]],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """`online: false` makes the entity unavailable."""
    _serve(aioclient_mock, online=False)
    await integration_setup()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
