"""Config flow for Nuheat Conductor."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig, NumberSelectorMode
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import NuheatApi
from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    DEVICE_MAX_TEMP_C,
    DEVICE_MIN_TEMP_C,
    DOMAIN,
    OAUTH2_SCOPES,
)
from .exceptions import NuheatError


def token_subject(access_token: str) -> str | None:
    """Return the `sub` claim of a JWT access token without verifying it.

    Only used as a stable account id for the unique_id; the token itself was
    just issued to us over TLS by the identity server.
    """
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except IndexError, ValueError, AttributeError:
        return None
    return str(sub) if sub else None


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Sign in to the Nuheat OpenAPI."""

    DOMAIN = DOMAIN

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NuheatOptionsFlow:
        """Setpoint range preferences."""
        return NuheatOptionsFlow()

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request a refresh token along with API access."""
        return {"scope": " ".join(OAUTH2_SCOPES)}

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create or update the entry once tokens are issued."""
        access_token: str = data[CONF_TOKEN][CONF_ACCESS_TOKEN]
        if not (subject := token_subject(access_token)):
            self.logger.error("Access token has no subject claim")
            return self.async_abort(reason="unknown")

        async def _token() -> str:
            return access_token

        try:
            account = await NuheatApi(async_get_clientsession(self.hass), _token).async_get_account()
        except NuheatError as err:
            self.logger.warning("Could not read Nuheat account: %s", err)
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(subject)
        if self.source != SOURCE_REAUTH:
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=account.get("userName") or "Nuheat", data=data)

        self._abort_if_unique_id_mismatch(reason="wrong_account")
        return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth after the refresh token stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm reauth."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()


class NuheatOptionsFlow(OptionsFlowWithReload):
    """Narrow the setpoint range HA offers, within what the thermostat accepts.

    Shown in the user's unit system; stored in °C to match the entity.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit min/max setpoint."""
        unit = self.hass.config.units.temperature_unit
        to_c = lambda value: round(TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS), 2)  # noqa: E731
        from_c = lambda value: round(TemperatureConverter.convert(value, UnitOfTemperature.CELSIUS, unit), 1)  # noqa: E731

        errors: dict[str, str] = {}
        if user_input is not None:
            min_c, max_c = to_c(user_input[CONF_MIN_TEMP]), to_c(user_input[CONF_MAX_TEMP])
            if min_c >= max_c:
                errors["base"] = "min_not_below_max"
            else:
                return self.async_create_entry(data={CONF_MIN_TEMP: min_c, CONF_MAX_TEMP: max_c})

        options = self.config_entry.options
        selector = NumberSelector(
            NumberSelectorConfig(
                min=from_c(DEVICE_MIN_TEMP_C),
                max=from_c(DEVICE_MAX_TEMP_C),
                step=1 if unit == UnitOfTemperature.FAHRENHEIT else 0.5,
                unit_of_measurement=unit,
                mode=NumberSelectorMode.BOX,
            )
        )
        schema = vol.Schema({
            vol.Required(CONF_MIN_TEMP, default=from_c(options.get(CONF_MIN_TEMP, DEVICE_MIN_TEMP_C))): selector,
            vol.Required(CONF_MAX_TEMP, default=from_c(options.get(CONF_MAX_TEMP, DEVICE_MAX_TEMP_C))): selector,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
