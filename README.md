# Nuheat Conductor for Home Assistant

Home Assistant custom integration for Nuheat thermostats on the Chemelex Conductor platform, using the official Nuheat OpenAPI v2. It replaces the built-in `nuheat` integration, which stopped working after the migration ([home-assistant/core#175923](https://github.com/home-assistant/core/issues/175923)).

Provides a climate entity per thermostat with Schedule / Temporary hold / Manual presets and push updates over SignalR.

## Requirements

- A client ID and secret from Chemelex, requested at <https://go.chemelex.com/connected-home>. Use `https://my.home-assistant.io/redirect/oauth` as the redirect URL.
- Home Assistant 2026.4.3 or later.

## Installation

1. Copy `custom_components/nuheat_conductor/` into `config/custom_components/` and restart Home Assistant.
2. **Settings → Devices & services → Add integration → Nuheat Conductor**.
3. Enter the Chemelex client ID and secret, then sign in to your Nuheat account.

## API documentation

- [Developer guide](https://api.nam.mynuheat.com/)
- [Swagger UI](https://api.mynuheat.com/swagger/index.html) ([v2 spec](https://api.mynuheat.com/swagger/v2/swagger.json))

## Development

```bash
uv sync
uv run pytest
```

`tools/` has standalone API probe scripts that read credentials from `.env` (see `.env.example`).

## License

[MIT](LICENSE)
