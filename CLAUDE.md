# CLAUDE.md — nuheat-ha

Personal Home Assistant custom integration for Ramon's Nuheat electric floor-heating thermostat. Not a Clever Integrations product.

## Why this exists
Chemelex (nVent spin-off) migrated Nuheat to the Conductor platform around 2026-07-06. mynuheat.com went offline and HA core's `nuheat` integration broke ([home-assistant/core#175923](https://github.com/home-assistant/core/issues/175923)). This repo replaces it using the official OpenAPI.

## Rules
- Credentials live in `.env` only (see `.env.example`). Never print, log, or commit them. Never paste them into chat.
- Use the official developer client_id issued by Chemelex. Do not extract client credentials from the Conductor mobile app.
- Always `uv run python3` — never bare `python3` or `pip`.
- Public repo at `github.com/alzahalemnci/nuheat-ha` (not promoted) — this is the only copy. Never commit personal details: thermostat serials, home network names, backup/config-entry IDs, or Clever Integrations internals.
- Progress log is `notes/progress.md` — append a dated entry for every meaningful step.

## Layout
- `notes/progress.md` — running log, current blocker, next steps
- `notes/api/` — API findings, OpenID discovery doc, form screenshot
- `plans/plan.md` — build plan
- `custom_components/nuheat_conductor/` — the integration
