# Progress Log

## Current status
**Live and verified on Prod 2026-09-14.** `nuheat_conductor` 0.1.0: two-way live check passed, HA restart survived without reauth. Remaining: 48 h log watch, GitHub push (Ramon's call). **2026-09-15: Chemelex granted AbsoluteRefreshTokenLifetime = 0** (requested by Ramon). Refresh tokens now expire only after 15 days unused. The existing Prod sign-in doesn't need redoing: IdentityServer4 applies the absolute cap from live client config at each refresh (`UpdateRefreshTokenAsync`: `if (client.AbsoluteRefreshTokenLifetime > 0 && newLifetime > …)`), so the chain is uncapped from its next hourly refresh. Checked against an older IS4 source tag, not Chemelex's build — confirmation point stays: no reauth prompt on Prod by 2026-10-14.

**Setpoint range (2026-09-14, commit `9f78d95`):** thermostat accepts 41–158 °F (5–70 °C, per the Nuheat app; not in the API). Integration defaults to that; Configure dialog narrows it. Ramon's preference set on Prod: **60–85 °F** (stored 15.56 / 29.44 °C). Verified: entity min_temp 60 / max_temp 85; HA rejects out-of-range setpoints before calling the API. 50 tests pass.

**Prod verification (2026-09-14, watcher polling HA every 2 s)**
| HA time | Event | Source |
|---|---|---|
| 23:26:13 | setpoint 73 °F, temporary_hold, holdUntil 09:30Z | Ramon in Nuheat app → hub push |
| 23:26:47 | hvac_action heating (+34 s, thermostat lag) | hub push |
| 23:28:37 | floor 70 °F | hub push |
| 23:28:39 | preset schedule, setpoint 60 °F — app showed 60 | Ramon in HA → API |
| 23:29:14 | `ha core restart` | — |
| 23:30:10 | entity back: schedule, 60 °F, idle; hub_connected true, last_update_success true | refresh token survived restart |

---

## 2026-09-14 — Phases 2–4: build, tests, Prod install

**Build** (commit `bc83c53`)
- Native HA integration: `application_credentials` (PKCE via `LocalOAuth2ImplementationWithPkce` subclass + client secret), OAuth2 config flow (unique_id = access-token `sub`, title = `/Account` userName, reauth with wrong-account guard), coordinator (hub-driven targeted refresh, 15 min safety poll, 60 s while disconnected), in-house aiohttp SignalR client, climate entity (Schedule / Temporary hold / Manual), diagnostics.
- `requirements: []`. Python 3.14.3 via uv; tests pinned to `pytest-homeassistant-custom-component==0.13.324` (= HA 2026.4.3, Prod's version). **47 tests pass.**
- Harness note: loopback fake-hub tests need the `socket_enabled` fixture.
- Min/max setpoint: not published anywhere (v1 swagger, web guide) → HA climate defaults (7–35 °C) until probed.

**Prod install** (home HA, 2026.4.3)
- Partial backup of the homeassistant folder first, plus the nightly full backup.
- Copied to `/config/custom_components/nuheat_conductor` over SSH (tar), `ha core check` OK, restart 23:13:22 → 23:16:47.
- Removed broken core `nuheat` entry (was stuck `setup_in_progress`; no automations/dashboards referenced it).
- Application credential "Chemelex OpenAPI" created via websocket (values read from `.env`, never printed).
- Config flow → Nuheat consent (Ramon approved) → my.home-assistant.io → callback → entry created, state loaded.
- Entity registered as `climate.bathroom` (same id as the old core entity); device assigned to area `bathroom`.
- Diagnostics: hub_connected true, update_interval 0:15:00, last_update_success true. Logs clean.
- Gotchas: SSH add-on's SUPERVISOR_TOKEN gets 401 on `supervisor/core/api` (no homeassistant_api access). Without the HA frontend, an OAuth config flow needs a manual `POST /api/config/config_entries/flow/<id>` after the callback to create the entry.

Ramon's app actions during the SignalR test: set 61 °F (→ temporary Hold), waited for the notice, then pressed **Cancel** on the hold (→ back to Auto/schedule). No other touches — every extra notice was server-side (confirmations/redelivery).

## Open questions
- `holdUntil` timezone handling when we supply one (≤23 h rule).
- First SignalR attempt (negotiate + `id` + `token`/`access_token` query) got WS 404; retry with `access_token` only worked both via negotiate and direct. Pin down during build — likely the extra `token` param or a non-sticky backend.

---

## 2026-09-14 — SignalR change-notification test (PASS)

Tool: `tools/signalr_probe.py` (`uv run --with websockets`). Ramon changed the setpoint in the phone app.

- **Auth:** negotiate with `?token=` (as the docs say) → 401. `Authorization: Bearer` → 200 (WebSockets/SSE/LongPolling). Socket: `wss://api.mynuheat.com/v2/notificationsHost?access_token=…` + Bearer header; negotiate can be skipped. Handshake `{"protocol":"json","version":1}` → `{}`. `Subscribe([2])` → completion, result null.
- **Notify frame:** `{"type":1,"target":"Notify","arguments":[{"type":2,"id":"00000000","timeStamp":"…"}]}`, no state payload.

| Received | Event timestamp (UTC) | State on re-read |
|---|---|---|
| 22:48:55 | 02:48:49 (whole second) | app set 61 °F → mode 2 Hold, setpoint **1607** (60.9 °F), holdUntil next schedule event |
| 22:49:10 | 02:49:09.8969137 (fractional) | unchanged |
| 22:49:35 | 02:49:09.8969137 (**duplicate** of previous) | unchanged |
| 22:49:49 | 02:49:49 (whole second) | Ramon's change back |
| 22:50:43 | 02:50:42.8506756 (fractional) | 22:50:47 mode 1 Auto, setpoint 1560, holdUntil null — back to baseline |
| 22:50:57 ×2 | 02:50:41.4320816 (older than previous, sent twice) | — |
| 22:51:05 | 02:50:49 (whole second, 16 s after the state was already baseline) | — |

Listener closed cleanly at 22:51:30 (DONE, no disconnects over 3 min with 15 s pings).

**Findings**
- Delivery latency ~1–6 s. Event-driven design (plan D4) is confirmed.
- One app change → a whole-second notice, then a fractional-second follow-up 20–55 s later (probably thermostat confirmation). Server can **redeliver** an identical notice, and delivers **out of order** (22:50:57: two copies of 02:50:41.43, after 02:50:42.85 had already arrived). → Timestamps are not a reliable sequence. Treat every notice as "something changed": debounce a few seconds, then re-read state. Never apply state from notice order.
- **Setpoint changes in the app create a temporary Hold** (mode 2 until next schedule event); they don't edit the schedule.
- App writes °F→°C inexactly (61 °F stored as 1607 = 60.93 °F) → HA must round °F display to whole degrees.

---

## 2026-09-14 — Phase 1 live mode test (thermostat 00000000, Ramon watching the app)

Tool: `tools/api.py` (refresh-token rotation → `.env`, access token cached in `.access_token`). ≥30 s between changes, per Ramon.

| Time (EDT) | Action | HTTP | Read-back |
|---|---|---|---|
| 22:41:10 | baseline | 200 | mode 1, setpoint 1560, holdUntil null |
| 22:41:14 | PUT /Mode/Manual temp 2222 (72 °F), temperatureType 0 | 204 | 22:41:46 mode **3**, setpoint 2222, heating false → 22:41:56 heating **true** (app showed Manual 72, heating) |
| 22:41:58 | PUT /Mode/Hold temp 2222, holdUntil null | 204 | 22:42:34 mode **2**, holdUntil **2026-09-15T09:30:00+00:00** (next schedule event) |
| 22:42:47 | PUT /Mode/Auto | 204 | 22:43:23 mode **1**, setpoint 1560 (schedule value restored), holdUntil null, heating false — thermostat back to baseline |

**Findings**
- v2 `mode` is its own enum: **1 Auto, 2 Hold, 3 Manual**. It is neither v1 eOperatingMode nor eScheduleMode.
- Hold without `holdUntil` does NOT stay forever: the server fills in the next schedule event and it reverts. v2 has no Permanent Hold; **Manual is the only indefinite override.**
- Setpoint and mode reflect immediately; `isHeating` lags ~40 s behind the app. Poll/refresh after writes must tolerate that.
- Writes return 204 with no body — state must be re-read (or wait for the SignalR notify).
- Refresh-token grant works; client uses one-time refresh tokens, rotated token persisted.

---

## 2026-09-14 — Credentials granted, auth smoke test passed

**Client config** (Chemelex-issued client config JSON, gitignored)
- Grant: `authorization_code` **only** (no password/device_code). PKCE optional (S256 supported). Client secret required. Consent required.
- Scopes: `openapi openid profile offline_access`. Redirect URI: `https://my.home-assistant.io/redirect/oauth` (only one).
- Access token 3600 s. Values copied into `.env`.
- Plan consequence: auth branch resolves to HA `application_credentials` + `config_entry_oauth2_flow`. Password branch is dead.

**Smoke test** — `tools/auth_probe.py url` → browser login + consent → `exchange`
- Token OK, refresh token saved to `.env` as `NUHEAT_REFRESH_TOKEN`.
- `GET /api/v2/Thermostat` → one thermostat: serial `00000000`, name `bathroom`, online, `currentTemperature` 2048, `setPointTemperature` 1560, `mode` 1, not heating. Saved to `api/thermostat-sample.json`.
- Temps look like hundredths °C (20.48 °C room / 15.60 °C setpoint) — confirm against the app/thermostat display.
- Gotcha: my.home-assistant.io rewrites the landing URL to `/redirect/_change/?redirect=oauth%2F%3Fcode=...` — the code is nested. Irrelevant inside HA (it forwards to the instance callback), but the probe script now unwraps it.

**Target instance:** home HA — Ramon confirmed.

**Web guide** (`https://api.nam.mynuheat.com/`, text saved to `api/openapi-webguide.txt`; NAM swagger is semantically identical to our saved copy)
- Operating modes: **Auto** (schedule) and **Manual** (schedule disabled, only setpoint). v1 `eOperatingMode` enum [1, 2].
- Schedule modes within Auto: **1 Auto**, **2 Hold** (temporary; defaults to next schedule event, custom end must be within 23 h, then reverts to Auto), **3 Permanent Hold** (never reverts). v1 `eScheduleMode` enum [1, 2, 3].
- Examples use `setPointTemp: 3000` (= 30.00 °C) → confirms integer hundredths °C.
- v2 `eTemperatureType`: 0 = Absolute setpoint, 1 = Relative (added to current temp).
- v1 Schedule endpoints are disabled (HTTP 405) — schedules can't be read or edited through the API.
- Ramon confirms the thermostat is in Auto (following its schedule, which holds 60 °F) → observed `mode: 1` = Auto under either enum.

---

## 2026-09-13 — Investigation + developer access request

**Background research**
- Nuheat Signature app and mynuheat.com shut down at migration (~2026-07-06/07). Official statement said third-party APIs would migrate "at the same time"; Control4, Crestron, and HA all went down.
- HA core issue #175923 open; no fix or community custom component found.
- Conductor launched May 2025 with Apple Home, Google Home, Alexa. Matter/HomeKit mechanism unconfirmed.

**API probing** (no credentials used)
- `GET https://api.mynuheat.com/api/v2/Thermostat` → `401` (API is live, needs bearer token).
- OpenID discovery at `https://identity.nam.mynuheat.com/.well-known/openid-configuration` saved to `api/openid-configuration.json`. Details in `api/findings.md`.
- Every grant type requires a client_id; none is public. Decided against pulling one from the mobile app (fragile, ToS).

**Developer access**
- Form: HubSpot form at bottom of https://go.chemelex.com/connected-home (screenshot `api/developer-access-form.png`).
- Suggested values: client name `home-assistant-nuheat`, redirect URL `https://my.home-assistant.io/redirect/oauth`.
- Ramon submitted it 2026-09-13.

**Thermostat model**
- Ramon confirmed: **Nuheat Signature**. No HomeKit/Matter local option — cloud OpenAPI is the only path.

**Next**
1. Ramon: create `.env` with username/password.
2. When Chemelex replies: add client_id/secret to `.env`, run auth smoke test, list thermostats.
3. Build integration per `plans/plan.md`.

**Event-driven path found**
- Docs have a "Change Notifications" section: SignalR hubs `/v2/notificationsHost` (+ v1). Both answer `401` unauthenticated → live. Notify payload is `{type, id, timeStamp}` only — fetch state after.
- Swagger spec is public: `swagger-v1.json` / `swagger-v2.json` saved. v2 `ThermostatModel` + Mode request bodies documented in `api/findings.md` — Phase 1 now only confirms scale/enum meanings.
- Plan D4 switched from polling to SignalR events + 15 min safety poll (polling-only fallback if SignalR fails).

**Plan written** — `plans/plan.md` expanded into full build plan: decisions (domain `nuheat_conductor`, auth branch on Chemelex grant, HA-agnostic API client, 60 s polling), Phases 1–4 with gates, entity mapping, risks.
