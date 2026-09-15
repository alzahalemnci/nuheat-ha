# Build Plan — `nuheat_conductor` Home Assistant Integration

**Owner:** Ramon (personal) · **Created:** 2026-09-13 · **Revised:** 2026-09-14 (after live auth, mode and SignalR tests) · **Status:** Phases 1–3 done; Phase 4 installed on Prod 2026-09-14, verification in progress (see `notes/progress.md`)

## Goal
Restore Home Assistant control of Ramon's **Nuheat Signature** floor thermostat through the official Nuheat OpenAPI (v2), after the Conductor migration broke HA core's `nuheat` integration.

**Shape:** a clean, native HA custom integration — standard `config_entries` + `application_credentials` OAuth2 + `DataUpdateCoordinator` + `climate` platform. Structured the way a core integration would be, so it could be submitted upstream later without a rewrite.

## Done means
- Thermostat appears in HA as a `climate` entity with live current temperature, target temperature, and heating state.
- Changes made in the Nuheat app show up in HA within seconds (push), and HA changes reach the thermostat immediately.
- Presets work: **Schedule / Temporary Hold / Manual**.
- Survives HA restarts and access-token expiry without re-login; when re-login *is* required (see D5), HA raises a reauth prompt instead of silently going unavailable.
- No credentials in git, logs, or diagnostics output.
- Passes HA's own `hassfest`-style expectations: manifest valid, strings/translations present, config flow + reauth tested.

## Non-goals
- Schedule viewing or editing — v1 schedule endpoints return 405; the Nuheat app stays the schedule editor (Ramon: that's how it always worked).
- Energy log (v1 only).
- Multi-account / HACS / core submission (possible later).
- Local control — Signature has no local API.

---

## Verified facts (Phase 1, 2026-09-14 — detail in `notes/progress.md`)

| Area | Fact |
|---|---|
| Auth | `authorization_code` only. Client secret required, consent required, PKCE optional (S256). Scopes `openapi openid profile offline_access`. Redirect `https://my.home-assistant.io/redirect/oauth` (the only one registered). |
| Tokens | Access 3600 s. Refresh tokens **one-time use** (rotate every refresh), **15-day sliding, 30-day absolute** lifetime. |
| Temperatures | Integer hundredths of °C (2048 = 20.48 °C). App writes 61 °F as 1607 (60.93 °F). |
| `mode` (v2) | Own enum: **1 Auto, 2 Hold, 3 Manual**. |
| Hold | `PUT /Mode/Hold` without `holdUntil` → server sets it to the next schedule event, then reverts to Auto. Custom end must be within 23 h. There is **no Permanent Hold** in v2. |
| Manual | Indefinite; schedule disabled until set back to Auto. |
| App behaviour | Changing setpoint in the app = temporary Hold. App "Cancel" = back to Auto. |
| Writes | `PUT` returns 204, no body. Setpoint and mode reflect on read immediately; `isHeating` lags ~40 s. |
| Push | Hub `wss://api.mynuheat.com/v2/notificationsHost`. Token via `Authorization: Bearer` / `access_token` query (docs' `?token=` → 401). JSON protocol handshake, `Subscribe([2])`, server invokes `Notify` with `{type, id: serial, timeStamp}`. Latency 1–6 s. |
| Push quirks | Each change produces 2+ notices (whole-second then fractional timestamp, 20–55 s apart); notices get **redelivered** and arrive **out of order**. |

---

## Key decisions

### D1 — Domain `nuheat_conductor`, not `nuheat`
A custom `nuheat` would shadow core and inherit its broken config entries. Distinct domain avoids collisions. Remove any old core `nuheat` config entry from Prod before installing.

### D2 — Auth: HA `application_credentials` + OAuth2 config flow ✅ resolved
- `application_credentials.py` → `async_get_authorization_server` returning the identity server's authorize/token URLs.
- `config_flow.py` → `AbstractOAuth2FlowHandler` with `extra_authorize_data = {"scope": "openid profile openapi offline_access"}`.
- `OAuth2Session` handles refresh and persists the rotated refresh token into the config entry — required, because refresh tokens are one-time use.
- PKCE: use HA's PKCE-capable local implementation if the Prod HA version ships it; otherwise plain auth-code (the client allows it). Decide once the Prod HA version is known.
- The user enters Client ID/Secret once under Application Credentials; nothing credential-shaped lives in the repo.

### D3 — API client is HA-agnostic
`api.py` takes an `aiohttp.ClientSession` and an async token getter; knows nothing about HA. Unit-testable alone, and reusable by the probe tools.

### D4 — Push-driven via SignalR, safety poll ✅ confirmed
- **In-house minimal SignalR JSON client over `aiohttp` WebSockets** (~150 lines), not `pysignalr`. The probe proved the protocol subset needed is tiny (handshake, one invocation, `Notify`, pings, close). `pysignalr` 1.3.2 pins `websockets>=16,<17` plus `msgpack`/`orjson` — a dependency-conflict risk inside HA for no gain. Result: `requirements: []`.
- **On any `Notify` for a known serial** → debounce ~3 s, then `GET /Thermostat/{serial}` and `async_set_updated_data`. Notices are a "something changed" signal only — never infer state or ordering from them (redeliveries, out-of-order).
- After every HA-initiated write: immediate refresh; the thermostat's own follow-up notice (20–55 s later) picks up the lagging `isHeating`.
- Safety poll **15 min**, plus full refresh on every (re)connect.
- Token is bound at connect → proactively reconnect with a fresh token at ~55 min.
- Reconnect backoff 5 s → 5 min; while disconnected, poll every 60 s.
- `iot_class: cloud_push`.

### D5 — The 30-day absolute refresh-token cap
Chemelex configured `absoluteRefreshTokenLifetime` = 30 days. Even with constant use, the refresh chain dies at day 30 → HA **will need re-login monthly**.
- Integration: on `invalid_grant` raise `ConfigEntryAuthFailed` → HA shows a Reauthenticate card; one click through the Nuheat login restores it. No data loss, the entity just goes unavailable until then.
- Ask Chemelex to set absolute lifetime to 0 (sliding only) for this client — the sliding 15 days would then never lapse while HA is running. **Ramon's call whether to ask.**

### D6 — Presets and setpoint semantics
| HA preset | API | Notes |
|---|---|---|
| `Schedule` | `PUT /Mode/Auto` | mode 1 |
| `Temporary Hold` | `PUT /Mode/Hold` (no `holdUntil`) | mode 2; reverts at next schedule event. Expose `holdUntil` as an entity attribute. |
| `Manual` | `PUT /Mode/Manual` | mode 3; indefinite. Replaces old core's "Permanent Hold", which v2 doesn't have. |

- `set_temperature`: if current preset is Manual → `Mode/Manual` with new temp; otherwise → `Mode/Hold` (matches the app).
- `hvac_modes: [HEAT]` only (no off via API). `hvac_action` HEATING/IDLE from `isHeating`.
- Entity works in °C internally (`temperature_unit` °C, precision tenths); HA converts for °F display. `target_temperature_step` 0.5 °C.
- Min/max: not in the v2 model → **open question**, determined in Phase 2 by probing (see below), never guessed.

---

## Phases

### Phase 0 — Decide the path ✅
- [x] Signature confirmed; cloud API is the only route.

### Phase 1 — Auth + discovery probe ✅ (2026-09-14)
- [x] Credentials issued, auth-code login + refresh proven (`tools/auth_probe.py`, `tools/api.py`).
- [x] Thermostat list, temperature scale, mode enum, Hold/Manual/Auto writes proven on the real thermostat with Ramon watching the app.
- [x] SignalR push proven (`tools/signalr_probe.py`).

### Phase 2 — Project scaffolding + API client
1. `uv init` → `pyproject.toml` with dev deps: `pytest`, `pytest-asyncio`, `aioresponses`, `pytest-homeassistant-custom-component` (pinned to the Prod HA version).
2. `custom_components/nuheat_conductor/api.py`, `models.py`, `exceptions.py`:
   - `NuheatApi.async_get_thermostats()`, `async_get_thermostat(serial)`, `async_set_auto(serial)`, `async_set_hold(serial, temp_c)`, `async_set_manual(serial, temp_c)`.
   - `Thermostat` dataclass: `serial`, `name`, `online`, `current_temp_c`, `target_temp_c`, `heating`, `mode` (`Mode.AUTO|HOLD|MANUAL`), `hold_until`, `error_state`. Hundredths ↔ °C conversion lives only here.
   - Exceptions: `NuheatAuthError` (401 / `invalid_grant`), `NuheatRateLimited` (429 + `Retry-After`), `NuheatApiError`.
3. `events.py` — `NuheatEvents(session, token_getter, on_change)`: aiohttp WS, handshake, `Subscribe([2])`, ping, debounce, token-rotation reconnect, backoff.
4. **Min/max probe** (live, Ramon watching, ≥30 s between writes): Manual at a clearly-too-low and too-high value, record whether the API rejects (4xx body) or clamps (read-back), restore to Auto. Result sets `min_temp`/`max_temp`.
5. Tests: redacted fixtures from real responses (`tests/fixtures/`), parsing/conversion/error mapping, fake hub for events (notify → debounced callback, duplicate/out-of-order notices → single refresh, disconnect → backoff, token rotation).

### Phase 3 — Home Assistant integration
| File | Responsibility |
|---|---|
| `manifest.json` | `domain`, `name`, `config_flow: true`, `dependencies: ["application_credentials"]`, `iot_class: cloud_push`, `requirements: []`, `codeowners`, `version` |
| `const.py` | domain, authorize/token URLs, API/hub base, scopes, intervals |
| `application_credentials.py` | authorization server |
| `config_flow.py` | OAuth2 flow; `unique_id` from the id_token `sub` (or `/Account`) so the account can't be added twice; reauth step |
| `__init__.py` | `OAuth2Session`, `NuheatApi`, coordinator, start/stop `NuheatEvents`, forward to `climate`, unload cleanly |
| `coordinator.py` | `DataUpdateCoordinator[dict[str, Thermostat]]`, 15 min interval; push-driven refresh; 60 s while disconnected; `NuheatAuthError` → `ConfigEntryAuthFailed`, others → `UpdateFailed` |
| `climate.py` | one entity per thermostat, per D6; `available` = coordinator OK **and** `online` |
| `diagnostics.py` | redacted coordinator data + connection state |
| `strings.json`, `translations/en.json` | config flow, reauth, preset names |

Tests (`pytest-homeassistant-custom-component`): config flow success / duplicate / reauth, auth failure → reauth, climate state from fixtures, each preset + set_temperature service call hits the right endpoint.

**Gate:** all tests green, then a **local HA run on the laptop** (HA dev container / venv) against the real account: add integration, verify entity, one preset change, one app-side change seen via push. Ramon confirms before Phase 4.

### Phase 4 — Install on home HA
Manual install over SSH. Ask before any Pi operation.
1. Check HA version and whether a core `nuheat` config entry exists; note serial, delete the entry.
2. Copy `custom_components/nuheat_conductor/` to `/config/custom_components/`.
3. Restart HA; wait until the restart is proven complete before checking (HA log line).
4. Settings → Application Credentials → add Client ID/Secret; Devices & Services → Add Nuheat Conductor → sign in.
5. Verify: values match the app; setpoint and each preset from HA; app-side change appears within seconds; HA restart → entity returns without re-login.
6. Leave 48 h; check logs for auth, 429, and reconnect churn.
7. Push to `alzahalemnci/nuheat-ha` — **only on Ramon's explicit go-ahead.**

---

## Risks
| Risk | Impact | Mitigation |
|---|---|---|
| 30-day absolute refresh cap (D5) | Monthly re-login | Clean reauth flow; optionally ask Chemelex to lift the cap |
| Refresh-token rotation lost (crash mid-refresh) | Forced re-login | Rely on HA's `OAuth2Session` persistence; never refresh outside it |
| SignalR hub changes/drops | Stale state up to 15 min | 60 s polling while disconnected; reconnect backoff |
| v2 schema drift | Parsing breaks | Parsing isolated in `models.py`; defensive parsing with logged warnings |
| Rate limits (docs have burst/rate section) | 429s | Push-first design keeps calls low; honour `Retry-After` |
| Core ships a fixed `nuheat` | Redundant | Switch back and archive; check core#175923 before each phase |

## Open questions
1. Prod HA version — pins test harness, decides PKCE (D2). Needs a Pi check.
2. Min/max setpoint — Phase 2 step 4.
3. Ask Chemelex to remove the 30-day absolute refresh cap? — Ramon.
4. First WS attempt 404'd with negotiate id + `token` param; bare `access_token` connect works — confirm in `events.py` tests against the live hub.
