# User documentation plan

How we document **every** screen, card, feature, setting and service in
BavarianData for end users — and how we keep it from drifting again.

This is an internal planning doc, not the manual itself. The manual it describes
lives in the GitHub Wiki (see *Structure* below).

## Why this exists

All user-facing documentation currently lives in one ~494-line `README.md` that
does six jobs at once: HACS shop-window, install tutorial, card reference,
feature explainer, services table, and troubleshooting guide. It is well
written but can no longer be **complete without becoming unreadable**, and there
is no systematic check that every screen/setting/card is actually covered.

Concretely, the v0.9.0 wave (trips/Fahrtenbuch, battery health, long-term
statistics, CSV/HTML export, the descriptor-coverage self-test, and the newer
card views `health` / `trips` / `closures` / `tire`) outgrew the README's
structure. The [coverage matrix](#coverage-matrix) below already surfaces a
concrete gap: **`get_coverage_report` ships in `services.yaml` but is absent
from the README services table.**

## Structure — three tiers

### Tier 1 — Shop window (`README.md` + `info.md`, kept lean, ~150 lines)

What a HACS visitor needs *before* installing: what it is, the three hero
screenshots, requirements, the experimental-status note, the start-to-finish
quick path (the 4 setup steps), and a prominent link into the manual.
Everything else moves to Tier 2.

### Tier 2 — The manual (GitHub Wiki)

Chosen over a `docs/` site or MkDocs/Pages because it needs no build step
(matches the project's no-bundler ethos), is editable without a PR cycle,
renders images natively, and keeps the repo clean. Tradeoff — wiki content is
not versioned with releases — is acceptable for a read-only integration whose
docs describe current `main`.

Organized on the **Diátaxis** model (Tutorial / How-to / Reference /
Explanation):

```
Home  (landing / nav)
├─ Getting started            [Tutorial]
│   ├─ 1. BMW portal setup (client ID + scopes)
│   ├─ 2. Install via HACS
│   ├─ 3. Add & authorize the integration
│   └─ 4. Choose which data to stream (cluster picker + snippet)
├─ The dashboard card         [How-to + Reference]
│   ├─ Overview view
│   ├─ Charging history view
│   ├─ Battery health view
│   ├─ Trips / driving journal view
│   ├─ Tire pressures (cluster: tire)
│   ├─ Security & closures (cluster: closures)
│   ├─ Single-cluster lists
│   └─ Full YAML option reference
├─ Features                   [Explanation]
│   ├─ Entities & devices (one per VIN, attributes, derived sensors)
│   ├─ Charging history & cost
│   ├─ Battery health (how it's learned)
│   ├─ Trips & the Fahrtenbuch caveat
│   ├─ Energy dashboard & long-term statistics
│   ├─ Export (CSV / HTML report)
│   ├─ Events & automation blueprints
│   └─ API quota
├─ Settings reference         [Reference]  — every Configure screen
├─ Services reference         [Reference]  — both tables + examples
├─ Troubleshooting & FAQ      [How-to]
└─ Reference                  — links into docs/reference/
```

### Tier 3 — Deep reference (`docs/reference/`, mostly generated)

`telematics-fields.md` and the API notes already live here and are
pipeline-generated. The manual links *into* them rather than duplicating —
this is where "docs stay correct automatically" comes from.

## Coverage matrix

This is the definition of "documented everything." Every row is derived from
code, so it is auditable. Status = state in the **new manual** (not the current
README). Legend: ✅ done · 🟡 partial (prose exists in README, needs
restructure) · ❌ missing · 📷 needs screenshot.

### Setup wizard screens — from `config_flow.py`

| Step (`async_step_…`) | Screen | Status | Shot |
| --- | --- | --- | --- |
| `user` | Guided/manual chooser (menu) | ❌ new — needs shot | 📷 |
| `guided_wait` | Guided: open setup page, waiting for activation (progress) | ❌ new — needs shot | 📷 |
| `guided_paste` | Guided: paste-result fallback (plain-HTTP HA) | ❌ new — needs shot | 📷 |
| `guided_done` | Guided: activation confirmation | ❌ new — needs shot | 📷 |
| served `/bavariandata/onboarding` | Bookmarklet helper page (drag + console fallback) | ❌ new — needs shot | 📷 |
| `manual` | Client ID entry + portal recap | 🟡 | 📷 |
| `authorize` | Device link + user code | 🟡 | 📷 |
| `authorize_failed` | Access-denied recovery | 🟡 | 📷 |
| `tokens` | Token exchange (auto-advance) | 🟡 | — |
| `select_clusters` | Cluster picker (manual path) | 🟡 | 📷 |
| `cluster_snippet` | Generated console snippet (manual path) | 🟡 | 📷 |
| `reauth` | Re-authorize with BMW | 🟡 | 📷 |

### Configure (options) menu — 14 actions from `async_step_init`

| Action | Screen | Status | Shot |
| --- | --- | --- | --- |
| `action_select_clusters` | Choose streamed data (cluster picker) | 🟡 | 📷 |
| `activate_stream_wait` | Reconfigure: run activator, waiting (progress) | ❌ new — needs shot | 📷 |
| `activate_stream_paste` | Reconfigure: paste-result fallback (http HA) | ❌ new — needs shot | 📷 |
| `activate_stream_done` | Reconfigure: activation confirmation | ❌ new — needs shot | 📷 |
| `action_refresh_tokens` | Refresh tokens | ❌ | 📷 |
| `action_reauth` | Re-authorize with BMW | 🟡 | 📷 |
| `action_reset_container` | Reset telematics container | ❌ | 📷 |
| `action_fetch_mappings` | Fetch vehicle mappings | 🟡 | — |
| `action_fetch_basic` | Fetch basic data | 🟡 | — |
| `action_fetch_telematic` | Fetch telematic data | 🟡 | — |
| `action_fetch_charging_history` | Fetch charging history | 🟡 | — |
| `action_fetch_tyre` | Fetch tyre diagnosis | 🟡 | — |
| `action_fetch_location_charging` | Fetch location charging settings | 🟡 | — |
| `action_fetch_image` | Fetch vehicle image | 🟡 | — |
| `action_charging_costs` | Charging costs & history | 🟡 | 📷 |
| `action_trips` | Trips | 🟡 | 📷 |
| `action_debug_logging` | Debug logging toggle | 🟡 | 📷 |

### Settings (option keys) — the fields inside those screens

| Option key | Screen | Status |
| --- | --- | --- |
| `price_mode` (none/fixed/entity) | Charging costs & history | 🟡 |
| `price_fixed` | Charging costs & history | 🟡 |
| `price_entity` | Charging costs & history | 🟡 |
| `price_currency` | Charging costs & history | ❌ |
| `grid_energy_entity` (wallbox) | Charging costs & history | 🟡 |
| `charging_loss_percent` | Charging costs & history | 🟡 |
| `history_retain_months` | Charging costs & history | 🟡 |
| `statistics_import` | Charging costs & history | 🟡 |
| `trip_work_zone` | Trips | 🟡 |
| `trip_geocode` | Trips | 🟡 |
| `debug_log` | Debug logging | 🟡 |
| `mqtt_keepalive` (hidden override) | — | ❌ |
| `diagnostic_log_interval` (hidden override) | — | ❌ |

### Dashboard card views — from `bmw-cardata-card.js`

| Config | View | Status | Shot |
| --- | --- | --- | --- |
| *(default)* | Overview | 🟡 | 📷 (have) |
| `view: charging` | Charging history | 🟡 | 📷 |
| `view: trips` | Driving journal | 🟡 | 📷 |
| `view: health` | Battery health | 🟡 | 📷 |
| `cluster: tire` | Tire pressures | 🟡 | 📷 (have) |
| `cluster: closures` | Security & closures | 🟡 | 📷 (have) |
| `cluster: <other>` | Single-cluster list | 🟡 | 📷 |
| YAML options (`device`, `vin`, entity overrides) | — | 🟡 | — |

### Services — 15 from `services.yaml`

| Service | Quota | Status |
| --- | --- | --- |
| `fetch_telematic_data` | spends | 🟡 |
| `fetch_vehicle_mappings` | spends | 🟡 |
| `fetch_basic_data` | spends | 🟡 |
| `fetch_charging_history` | spends | 🟡 |
| `fetch_tyre_diagnosis` | spends | 🟡 |
| `fetch_location_charging_settings` | spends | 🟡 |
| `fetch_vehicle_image` | spends | 🟡 |
| `get_charging_sessions` | free | 🟡 |
| `get_trips` | free | 🟡 |
| `get_driving_summary` | free | 🟡 |
| `set_trip_class` | free | 🟡 |
| `export_history` | free | 🟡 |
| `import_statistics` | free | 🟡 |
| **`get_coverage_report`** | free | ❌ **missing from README** |
| `activate_stream_fields` | free | 🟢 Services-Reference + README + reference doc |

### Entities — descriptor + derived

Descriptor sensors/binary sensors are generated and documented in
`docs/reference/telematics-fields.md` (link, don't duplicate). Derived/diagnostic
entities (from `tools/derived_entities.json`) need explicit prose:

| Entity | Kind | Status |
| --- | --- | --- |
| `soc_estimate`, `soc_rate` | Extrapolated SoC helpers | 🟡 |
| `soc_estimate_testing` | Diagnostic variant | ❌ |
| `charged_energy_total`, `charged_energy_session` | Energy integration | 🟡 |
| `charging_energy_month` | Monthly total | 🟡 |
| `charging_cost_month`, `charging_cost_session`, `charging_cost_per_100km` | Cost | 🟡 |
| `battery_health` | Learned capacity | 🟡 |
| `driving_distance_month` | Monthly distance + split | 🟡 |
| `api_quota_remaining` | Diagnostic | 🟡 |
| `connection_status`, `last_message`, `last_telematic_api` | Diagnostics | ❌ |
| `car` (device_tracker) | Location | ❌ |
| `vehicle_image` (image) | Cached render | 🟡 |

### Cross-cutting features

| Feature | Status |
| --- | --- |
| Charging history & cost | 🟡 |
| Battery health (learning method) | 🟡 |
| Trips / Fahrtenbuch (+ legal caveat) | 🟡 |
| Energy dashboard & long-term statistics | 🟡 |
| Export (CSV / HTML report) | 🟡 |
| Charging events (`bavariandata_charging_*`) | 🟡 |
| Automation blueprints (2) | 🟡 |
| API quota + Repairs issue | 🟡 |
| Descriptor-coverage self-test | ❌ |

## Conventions

- **Screenshots**: reuse the existing Playwright workflow (cached chromium,
  shadow-DOM element shots, force-English trick). One shot per screen/view,
  consistent viewport, the "Wattfried" demo car for continuity with the README
  heroes. Store under `screenshots/`, reference via absolute
  `raw.githubusercontent.com` URLs (HACS/wiki can't resolve relative paths).
- **EN/DE parity**: entity/field names are already bilingual via the pipeline.
  For prose, ship **English first, German as a fast-follow** rather than
  blocking every page on translation; the matrix tracks the gap honestly.
- **Generated vs hand-written**: entity and field tables should be *generated*
  (extend `tools/` to emit a manual-ready entity list) so they can't drift —
  same principle already governing `telematics-fields.md` and translations.

## Maintenance rule

A feature isn't done until its coverage-matrix row is green. Any new
`action_*` step, card view, service, or option gets its row + page in the same
PR. (Proposed addition to `CLAUDE.md`.)

## Rollout order

1. ✅ Build this coverage matrix — done (above).
2. ✅ Slim `README.md` to Tier 1 (494 → ~110 lines; links into the Wiki).
3. ✅ Migrate README prose into Wiki pages (cards, services, troubleshooting).
4. ✅ Write the genuinely-missing pages (coverage self-test, reset container,
   refresh tokens, diagnostics entities, device tracker, hidden overrides,
   `get_coverage_report`). The full English manual is staged in `docs/wiki/`.
5. 🟡 Screenshot pass — **5 of ~9 captured** against the live HA instance via the
   Playwright workflow (cluster picker, Configure menu, charging-costs settings,
   charging & battery-health card views; in `screenshots/`). Remaining are
   deliberately deferred: the two onboarding screens (`config-flow-user` /
   `-authorize`) and `config-flow-cluster-snippet` would disturb the live
   single-stream / persist state, and `card-trips` has no data yet. The `Trips`
   settings screen was captured but showed a real work-zone name (redact first).
6. ⬜ German pass.

### Publishing the staged Wiki — blocked on a one-time manual step

The English manual lives in **`docs/wiki/`** (source, reviewable in PRs). It
cannot be pushed yet: GitHub does not provision the wiki git remote (and offers
no API for it) until the **first page is created via the web UI** — a clone/push
to `…/BavarianData.wiki.git` currently 404s. Once someone creates one page at
<https://github.com/JustChr/BavarianData/wiki>, run
[`scripts/publish-wiki.sh`](../scripts/publish-wiki.sh) to sync everything.
The screenshots referenced by the pages must also be **committed to `main`** for
the raw-URL images to load. Until published, the README's Wiki links 404.

### Status after this rollout

Every 🟡 row in the matrix is now written English prose in `docs/wiki/`, and the
❌ rows are covered too (incl. `get_coverage_report`, which the README table used
to omit — the README no longer carries a services table at all). Remaining work
is the two ⬜ passes above: screenshots and German.
