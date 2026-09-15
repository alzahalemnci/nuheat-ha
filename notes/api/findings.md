# Nuheat API Findings

Sources: https://api.mynuheat.com/ (public docs landing page), OpenID discovery doc (probed 2026-09-13).

## Identity (OAuth2 / OpenID Connect, IdentityServer4)
- Issuer: `https://identity.nam.mynuheat.com`
- `identity.mynuheat.com` still serves discovery but reports the `nam` issuer — use `nam`.
- Authorize: `/connect/authorize` — Token: `/connect/token`
- Grants advertised: `authorization_code`, `client_credentials`, `refresh_token`, `implicit`, `password`, `urn:ietf:params:oauth:grant-type:device_code`
- PKCE: `plain`, `S256`
- Relevant scopes: `openid`, `openapi`, `offline_access` (refresh tokens)
- Per docs: access token 1h, refresh token 15 days.
- Advertised grants are server-wide; what our client may use is set per-client by Chemelex.

## REST API — `https://api.mynuheat.com/api/v2/`
| Method | Path | Purpose |
|---|---|---|
| GET | `/Account` | Account info |
| GET | `/Thermostat` | List thermostats |
| GET | `/Thermostat/{serialNumber}` | One thermostat |
| PUT | `/Mode/Auto` | Resume schedule |
| PUT | `/Mode/Hold` | Hold temp (temporary) |
| PUT | `/Mode/Manual` | Permanent hold |

v1 (legacy) also exposes energy logs (day/week/month); schedule endpoints disabled. Request/response schemas are only visible to registered developers — fill in once access is granted.

## Swagger spec — public (2026-09-13)
`https://api.mynuheat.com/swagger/v2/swagger.json` and `/v1/` are reachable without auth. Saved as `swagger-v2.json` / `swagger-v1.json`. Swagger security scheme advertises implicit grant only (docs-explorer config; not necessarily our client's limit).

### v2 schemas
- `ThermostatModel`: `serialNumber` str, `name` str, `currentTemperature` int32, `online` bool, `isHeating` bool, `setPointTemperature` int32, `holdUntil` date-time, `mode` int32, `errorState` str
- `AccountModel`: `userName`, `temperatureScale`, `language`
- `ModeAuto`: `{serialNumber}`
- `ModeHold`: `{serialNumber, temperature int32, holdUntil date-time, temperatureType eTemperatureType}`
- `ModeManual`: `{serialNumber, temperature int32, temperatureType eTemperatureType}`
- `eTemperatureType`: enum `[0, 1]` — unlabeled; likely Celsius/Fahrenheit. Confirm in Phase 1.

Still unconfirmed until authenticated: temperature integer scale (old API used hundredths — e.g. 2200 = 22.00), `mode` int → Auto/Hold/Manual mapping, `errorState` values.

## Change notifications — SignalR (event-driven)
- Hubs: v2 `/v2/notificationsHost`, v1 `/v1/changenotifications`. Both return `401` on unauthenticated `negotiate` → live.
- Auth: access token in query string `?token=<access_token>`, scope `openapi`.
- Client invokes `Subscribe(int[] typeIds)` / `Unsubscribe(int[] typeIds)`.
- Server calls a single client method `Notify` with payload `[{"type":2,"id":"<serial>","timeStamp":"..."}]`.
- Types: 1 UserAccount (account id), 2 Thermostat (serial), 3 Schedule (serial), 4 Group (group id).
- Notifications carry **no state** — they signal "changed"; client must then GET the resource.
- Docs: "should be used for minimizing an amount of requests to OpenAPI."

## Rate limits
1,000 req / 10 s and 1,000,000 req / 7 days per user+client. `429` on breach. 30–60 s polling is comfortably inside.

## Status
Unauthenticated `GET /api/v2/Thermostat` → `401` (2026-09-13).
