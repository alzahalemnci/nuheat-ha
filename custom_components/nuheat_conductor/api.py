"""Nuheat OpenAPI v2 client. Knows nothing about Home Assistant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .const import API_BASE
from .exceptions import NuheatApiError, NuheatAuthError, NuheatRateLimitedError
from .models import Thermostat, to_api_temp

TokenGetter = Callable[[], Awaitable[str]]

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class NuheatApi:
    """Thin async wrapper over the v2 REST endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_getter: TokenGetter,
        base_url: str = API_BASE,
    ) -> None:
        """Initialize with a shared session and an async access-token source."""
        self._session = session
        self._token_getter = token_getter
        self._base_url = base_url

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        token = await self._token_getter()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}", headers=headers, json=payload, timeout=_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise NuheatAuthError("Access token rejected")
                if resp.status == 429:
                    retry = resp.headers.get("Retry-After")
                    raise NuheatRateLimitedError(int(retry) if retry and retry.isdigit() else None)
                if resp.status >= 400:
                    body = await resp.text()
                    raise NuheatApiError(f"{method} {path} -> HTTP {resp.status}: {body[:200]}")
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NuheatApiError(f"{method} {path} failed: {err}") from err

    async def async_get_account(self) -> dict[str, Any]:
        """Return the account (userName, temperatureScale, language)."""
        return await self._request("GET", "/Account")

    async def async_get_thermostats(self) -> list[Thermostat]:
        """Return every thermostat on the account."""
        data = await self._request("GET", "/Thermostat")
        return [Thermostat.from_api(item) for item in data or []]

    async def async_get_thermostat(self, serial: str) -> Thermostat:
        """Return one thermostat."""
        return Thermostat.from_api(await self._request("GET", f"/Thermostat/{serial}"))

    async def async_set_auto(self, serial: str) -> None:
        """Follow the schedule."""
        await self._request("PUT", "/Mode/Auto", {"serialNumber": serial})

    async def async_set_hold(self, serial: str, temp_c: float) -> None:
        """Hold a temperature until the next schedule event (server decides the end)."""
        await self._request(
            "PUT",
            "/Mode/Hold",
            {"serialNumber": serial, "temperature": to_api_temp(temp_c), "temperatureType": 0, "holdUntil": None},
        )

    async def async_set_manual(self, serial: str, temp_c: float) -> None:
        """Hold a temperature indefinitely, schedule disabled."""
        await self._request(
            "PUT",
            "/Mode/Manual",
            {"serialNumber": serial, "temperature": to_api_temp(temp_c), "temperatureType": 0},
        )
