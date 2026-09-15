# Nuheat Conductor for Home Assistant

A Home Assistant custom integration for Nuheat floor-heating thermostats, using the official Nuheat OpenAPI (v2) that came with Chemelex's move to the Conductor platform.

When Nuheat migrated to Conductor in July 2026, mynuheat.com went offline and Home Assistant's built-in `nuheat` integration stopped working ([home-assistant/core#175923](https://github.com/home-assistant/core/issues/175923)). This integration replaces it for my own thermostat.

> **Personal project.** It runs in my home and is shared as-is. It is not in HACS and is not affiliated with Nuheat, Chemelex or nVent. It has only been tested with one Nuheat Signature thermostat on one account. Issues and pull requests may go unanswered.

## What you get

- **One climate entity per thermostat**, showing floor temperature, setpoint and heating/idle.
- **Presets** that match the Nuheat app:
  | Preset | What it does |
  |---|---|
  | Schedule | Follows the thermostat's programmed schedule |
  | Temporary hold | Holds the setpoint until the next schedule event, then goes back to Schedule. The end time is in the `hold_until` attribute. |
  | Manual | Holds the setpoint until you change it |
- **Setpoint changes behave like the app**: in Manual they update the manual setpoint; otherwise they start a temporary hold.
- **Push updates**: changes made in the Nuheat app reach Home Assistant within a few seconds through Nuheat's SignalR change-notification hub. It also polls every 15 minutes as a safety net, and every 60 seconds while the hub is disconnected.
- **Adjustable setpoint range** under *Configure*, within the thermostat's own limits of 41–158 °F (5–70 °C).
- **Re-authentication prompt** if the Nuheat sign-in ever expires, so the entity doesn't just go unavailable.
- **Diagnostics** with credentials redacted.
- No third-party Python dependencies.

## Limitations

- **Cloud only.** The Signature thermostat has no local API, so this needs Nuheat's cloud.
- **No schedule editing.** The API's schedule endpoints are disabled; keep using the Nuheat app for schedules.
- **Heat only.** The API has no "off" mode.
- **Heating status lags.** Nuheat's servers report heating/idle about 40 seconds after the thermostat changes.

## Requirements

You need **your own API client from Chemelex**. The integration doesn't ship one and can't share mine.

1. Fill in the developer access form at the bottom of <https://go.chemelex.com/connected-home>.
2. For the redirect URL, use `https://my.home-assistant.io/redirect/oauth`.
3. Chemelex sends a client ID and client secret.

**Token lifetime:** by default the client may be set with a 30-day absolute refresh-token lifetime, which means signing in again every month. Chemelex agreed to set `AbsoluteRefreshTokenLifetime` to `0` on request, which removes that limit.

Tested on Home Assistant 2026.4.3. Signing in goes through [My Home Assistant](https://my.home-assistant.io/), so set your instance URL there first.

## Installation

1. Copy `custom_components/nuheat_conductor/` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Nuheat Conductor**.
4. When asked, enter the client ID and secret from Chemelex. They are stored under *Application credentials*.
5. Sign in to your Nuheat account and approve access.
6. Optional: open the integration's **Configure** dialog to narrow the setpoint range.

The domain is `nuheat_conductor`, so it won't clash with the built-in `nuheat` integration. Remove the broken built-in entry once this one works.

## Development

```bash
uv sync
uv run pytest
```

The tests use `pytest-homeassistant-custom-component`, pinned to the Home Assistant version above.

`tools/` has the scripts used to probe the API before the integration existed: an OAuth sign-in helper, an API client and a SignalR listener. They read credentials from `.env` (see `.env.example`).

## Repository layout

| Path | Contents |
|---|---|
| `custom_components/nuheat_conductor/` | The integration |
| `tests/` | Integration and API client tests |
| `tools/` | Standalone API probe scripts |
| `notes/api/` | API findings, public Swagger specs, and the saved developer guide |
| `notes/progress.md` | Build log |
| `plans/plan.md` | Design decisions and build plan |

## License

[MIT](LICENSE).
