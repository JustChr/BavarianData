---
name: live
description: Query the maintainer's live Home Assistant instance for real stored data — states, recorder history, the entity registry, current options, the evcc MQTT topics. Use when verifying coordinator, entity or flow behaviour, which the test suite cannot cover, or when a number needs proving over time. All calls here cost zero BMW quota.
---

# Read the live instance

Host, login and the MQTT broker address are in this project's memory
(`live-ha-instance-access`) — they are deliberately not written into this repo.
Read them from there.

**Everything below costs zero BMW REST quota.** The 50-requests/24 h budget is
only spent by calls that reach BMW.

## Token

No long-lived token needed; three curl calls:

1. `POST /auth/login_flow` `{"client_id":"<URL>/","handler":["homeassistant",null],"redirect_uri":"<URL>/"}` → `flow_id`
2. `POST /auth/login_flow/<flow_id>` `{"client_id":"<URL>/","username":…,"password":…}` → auth code
3. `POST /auth/token` (form) `grant_type=authorization_code&code=<code>&client_id=<URL>/` → `access_token` (~30 min)

Then `Authorization: Bearer <token>`.

## The calls worth knowing

| Want | Call |
| --- | --- |
| Any entity's state and attributes | `GET /api/states` |
| **Proof a value misbehaves over time** | `GET /api/history/period/<from-iso>?filter_entity_id=<e>&minimal_response&end_time=<to-iso>` |
| The actual stored sessions/trips | `POST /api/services/bavariandata/get_charging_sessions?return_response` `{}` |
| Per-cluster coverage | `POST /api/services/bavariandata/get_coverage_report?return_response` `{}` |
| What the evcc bridge *would* publish | `POST /api/services/bavariandata/get_evcc_config?return_response` `{}` |
| The integration's current options | `GET /api/diagnostics/config_entry/<entry_id>` → `data.config_entry.options` |
| BMW basic data (`drive_train`, `propulsion_type`…) | entity attribute `vehicle_basic_data` |

## Traps that have cost time

- **`/api/history/period/<start>` covers ONE DAY unless you pass `end_time`.** A
  45-day query without it silently returns nothing.
- **The entity registry has no REST list endpoint.** Use the websocket
  (`config/entity_registry/list`) via a small aiohttp script. It is the only way
  to see *disabled* entities, which never appear in `/api/states`.
- Repairs are websocket-only too: `{"type": "repairs/list_issues"}`.
- Windows Python cannot open Git Bash's `/tmp` — write intermediates to the
  session scratchpad.
- Diagnostics redacts `grid_energy_entity` and `price_entity`, so which wallbox
  meter is bound cannot be read that way.
- The coverage report holds the VIN. Don't leave it in a file.

## MQTT

The broker accepts anonymous subscribers. Subscribe to `bavariandata/#` to see
what the evcc bridge publishes; redact the VIN from topics and payloads before
writing anything down. **Never subscribe to `#`** beyond counting topic roots —
the broker carries the rest of the household. Retention only shows on a *fresh*
subscriber: a live subscriber sees `retain=0` even for retained publishes.

## Two things to say out loud

- The running integration is **whatever HACS build is installed**, not this
  working tree. Code changes take effect only after the user updates.
- **Restarting this HA stops the car charging** — a separate wallbox integration
  comes back with charging disabled about 60 s later and does not resume. Warn
  before asking for a restart, and tell the user afterwards.
