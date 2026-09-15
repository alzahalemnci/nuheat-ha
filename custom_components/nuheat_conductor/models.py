"""Data models for the Nuheat OpenAPI v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class Mode(IntEnum):
    """v2 `mode` values, verified against a live thermostat (not in the docs)."""

    AUTO = 1
    HOLD = 2
    MANUAL = 3


def to_api_temp(temp_c: float) -> int:
    """Convert °C to the API's integer hundredths of °C."""
    return round(temp_c * 100)


def from_api_temp(value: int) -> float:
    """Convert the API's integer hundredths of °C to °C."""
    return value / 100


@dataclass(frozen=True, slots=True)
class Thermostat:
    """A thermostat as reported by `GET /Thermostat`."""

    serial: str
    name: str
    online: bool
    heating: bool
    current_temp_c: float
    target_temp_c: float
    mode: Mode | None
    hold_until: datetime | None
    error_state: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Thermostat:
        """Build from an API response object."""
        serial = str(data["serialNumber"])
        raw_mode = data.get("mode")
        try:
            mode: Mode | None = Mode(raw_mode)
        except ValueError:
            _LOGGER.warning("Thermostat %s reported unknown mode %r", serial, raw_mode)
            mode = None
        hold_until = None
        if raw_hold := data.get("holdUntil"):
            try:
                hold_until = datetime.fromisoformat(raw_hold)
            except ValueError:
                _LOGGER.warning("Thermostat %s: unparseable holdUntil %r", serial, raw_hold)
        return cls(
            serial=serial,
            name=data.get("name") or serial,
            online=bool(data.get("online")),
            heating=bool(data.get("isHeating")),
            current_temp_c=from_api_temp(data["currentTemperature"]),
            target_temp_c=from_api_temp(data["setPointTemperature"]),
            mode=mode,
            hold_until=hold_until,
            error_state=data.get("errorState"),
        )
