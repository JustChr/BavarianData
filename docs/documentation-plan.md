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
screenshots, requirements, the project-status note, the start-to-finish
quick path (the 4 setup steps), and a prominent link into the manual.
Everything else moves to Tier 2.

### Tier 2 — The manual (`docs/wiki/`, published twice)

The Markdown in `docs/wiki/` is the manual's single source, published to two
places:

- **The GitHub Wiki** (`bash scripts/publish-wiki.sh`), chosen first because it
  needs no build step and renders images natively.
- **The website** (`site/`, GitHub Pages, deployed by `.github/workflows/pages.yml`
  on every push to `main`). It exists because GitHub serves wikis with
  `X-Robots-Tag: none`: search engines and AI assistants never see the wiki, so
  a user searching for the problem this integration solves could not find it.
  The site renders the same files (links, screenshots and anchors rewritten on
  the way through) and adds only what the wiki can't hold — clean URLs, a search
  title and meta description per page (`site/src/data/pages.json`), hreflang,
  structured data, a sitemap, `llms.txt`, a page per BMW descriptor, and the
  real card running on a scrubbed capture of a real car.

Never write a page only for the site: a new wiki page needs its
`pages.json` entry (`tests/test_site_pages.py` fails without it), and that's all.

Organized on the **Diátaxis** model (Tutorial / How-to / Reference /
Explanation):

```
Home  (landing / nav)
├─ Getting started            [Tutorial]
│   ├─ Which cars work (by drivetrain, owner-confirmed models)
│   ├─ 1. BMW portal setup (client ID + scopes)
│   ├─ 2. Install from the Marketplace (HACS before 2026.11)
│   ├─ 3. Add & authorize the integration
│   ├─ 4. Choose which data to stream (cluster picker + activator)
│   └─ 5. Add the card to a dashboard (no dashboard is auto-created)
├─ The dashboard card         [How-to + Reference]
│   ├─ Placing the card (Add card → BavarianData Card)
│   ├─ Overview view
│   ├─ Charging history view
│   ├─ Battery health view
│   ├─ Trips / driving journal view
│   ├─ Trip map view (view: map)
│   ├─ Tires (cluster: tire)
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

### Setup wizard screens — from `config_flow.py` and `flow_activator.py`

| Step (`async_step_…`) | Screen | Status | Shot |
| --- | --- | --- | --- |
| `user` | Guided/manual chooser (menu) | ✅ `bavariandata-setup-chooser` | — |
| `guided_wait` | Guided: waiting for activation (progress, **https only**) | ❌ needs https HA to reach | 📷 |
| `guided_paste` | Guided: activation + paste screen (http HA) | ✅ `bavariandata-setup-guided` | — |
| `guided_done` | Guided: activation confirmation | ❌ needs a real activation | 📷 |
| served `/bavariandata/onboarding` | Bookmarklet helper page (drag + console fallback) | ✅ `bavariandata-setup-bookmarklet` | — |
| `manual` | Client ID entry + portal recap | ✅ `bavariandata-setup-manual` | — |
| `authorize` | Device link + user code | 🟡 | 📷 |
| `authorize_failed` | Access-denied recovery | 🟡 | 📷 |
| `tokens` | Token exchange (auto-advance) | 🟡 | — |
| `select_clusters` | Cluster picker (manual path), then the activator | 🟢 Getting-Started-3 → "Manual path" + Getting-Started-4 | 📷 (`bavariandata-cluster-picker`) |
| `activate_stream_wait` / `_paste` / `_done` | Manual path: the same activator screens as reconfigure (one shared mixin; the paste box may be left empty at first-time setup) | 🟢 Getting-Started-4 → "Picking clusters and switching them on" | 📷 shared with the reconfigure rows below |
| `reauth` | Re-authorize with BMW | 🟡 | 📷 |
| `reconfigure` | Change Client ID (HA's *Reconfigure*; same BMW account only) | 🟢 Getting-Started-1 + Settings-Reference | 📷 |

### BMW portal steps — external, shown in Getting-Started 1/3/4

These live on BMW's MyBMW / CarData portal, not in our code. Screenshots are
© BMW AG, redacted, and reproduced only to document setup (see `_Footer.md`).

| Portal screen | Page | Status | Shot |
| --- | --- | --- | --- |
| MyBMW account → Vehicle overview | GS-1 | ✅ | `bmw-portal-account` |
| Vehicle overview + CarData tile | GS-1 | ✅ | `bmw-portal-vehicle-overview` |
| Technical access — Client ID + scopes | GS-1 | ✅ | `bmw-portal-cardata-client` |
| CarData Stream + Change data selection | GS-4 | ✅ | `bmw-portal-change-data-selection` |
| Stream setup — selection of vehicle data | GS-3 | ✅ | `bmw-portal-stream-setup` |
| Bookmarklet running (reading fields) | GS-3 | ✅ | `bmw-portal-bookmarklet-running` |
| Bookmarklet done (fields active + copy box) | GS-3 | ✅ | `bmw-portal-bookmarklet-done` |

### Configure (options) menu — 14 actions from `async_step_init`

| Action | Screen | Status | Shot |
| --- | --- | --- | --- |
| `action_select_clusters` | Choose streamed data (cluster picker) | 🟡 | 📷 |
| `activate_stream_wait` | Reconfigure: run activator, waiting (progress) | ❌ new — needs shot | 📷 |
| `activate_stream_paste` | Reconfigure: paste-result fallback (http HA) | ❌ new — needs shot | 📷 |
| `activate_stream_done` | Reconfigure: activation confirmation | ❌ new — needs shot | 📷 |
| `init` | Configure menu: the nine settings + the two submenus below | 🟢 Settings-Reference → "The Configure menu" (EN + DE) | 📷 (have, 2026-11-04: `bavariandata-configure-menu.png`, with Share live data and Units) |
| `fetch_now` | Submenu "Fetch from BMW now": Discover vehicles + the five fetches (all spend quota) | 🟢 Settings-Reference → "The Configure menu" (EN + DE) | 📷 owed |
| `connection` | Submenu "Connection": re-authorize, refresh tokens, reset container, debug logging | 🟢 Settings-Reference → "The Configure menu" (EN + DE) | 📷 owed |
| `action_refresh_tokens` | Refresh tokens | ❌ | 📷 |
| `action_reauth` | Re-authorize with BMW | 🟡 | 📷 |
| `action_reset_container` | Reset telematics container | ❌ | 📷 |
| `action_fetch_mappings` | Fetch vehicle mappings | 🟡 | — |
| `action_fetch_basic` | Fetch basic data (attributes `vehicle_basic_data`/`_raw` on the vehicle image + device tracker only) | 🟡 — the attributes are 🟢 in Feature-Entities-and-Devices (EN + DE) | — |
| `action_fetch_telematic` | Fetch telematic data | 🟡 | — |
| `action_fetch_charging_history` | Fetch charging history | 🟡 | — |
| `action_fetch_tyre` | Fetch tire diagnosis | 🟡 | — |
| `action_fetch_location_charging` | Fetch location charging settings | 🟡 | — |
| `action_fetch_image` | Vehicle image (picture source: BMW CarData or BMW's configurator, `image_source`) | 🟢 Settings-Reference → "Vehicle image" + Feature-Entities-and-Devices + The-Dashboard-Card (EN + DE) | 📷 (have, 2026-10-14: `bavariandata-vehicle-image.png`; card: `card-hero-spin.png`) |
| `vehicle_image` | Vehicle image as a menu once the configurator is the source: Picture source (`image_source` → the `action_fetch_image` form) · Upholstery | 🟢 Settings-Reference → "Vehicle image" (EN + DE) | 📷 (have, 2026-11-06: `bavariandata-vehicle-image-menu.png`) |
| `upholstery_vehicle` | Upholstery: which vehicle (only with 2+ cars) | 🟢 Settings-Reference → "Vehicle image" (EN + DE) | 📷 (picker shared) |
| `action_upholstery` | Vehicle image → Upholstery: one car's upholstery from its model's list; Submit redraws that car only | 🟢 Settings-Reference → "Vehicle image" + The-Dashboard-Card → "Overview" (EN + DE) | 📷 (have, 2026-11-06: `bavariandata-upholstery.png`) |
| `action_data_refresh` | Automatic data refresh | 🟢 Settings-Reference → "Automatic data refresh" | 📷 |
| `charging_vehicle` | Charging costs: which vehicle (only with 2+ cars) | 🟢 Settings-Reference → "Charging costs & history" + Feature-Multiple-Cars-and-Accounts (EN + DE) | 📷 (have, staged two-car: `bavariandata-charging-costs-second-car.png`; picker shared) |
| `action_charging_costs` | Charging costs & history (per car with 2+ cars: meter and losses; price etc. in "All vehicles") | 🟡 | 📷 (have, 2026-10-11: `bavariandata-charging-costs.png`) |
| `bridge_vehicle` | MQTT bridge: which vehicle (only with 2+ cars) | 🟢 Settings-Reference → "MQTT bridge (evcc & more)" + Feature-Multiple-Cars-and-Accounts (EN + DE) | 📷 (have, 2026-11-03, staged two-car: `bavariandata-evcc-bridge-second-car.png`, All vehicles open; picker shared) |
| `action_evcc_bridge` | MQTT bridge (evcc & more) (per car with 2+ cars: evcc on/off, share the whole stream; leave out the position, prefix and retained in "All vehicles"; the evcc snippet follows only when evcc is on) | 🟢 Feature-evcc-and-Wallbox-Bridge (incl. "Sharing the whole stream, with other apps") + Settings-Reference → "MQTT bridge (evcc & more)" | 📷 (have, 2026-11-03: `bavariandata-evcc-bridge.png`) |
| `evcc_snippet` | evcc configuration (generated, paste-ready) | 🟢 Feature-evcc-and-Wallbox-Bridge → "Setting it up" | 📷 (have, 2026-10-14: `bavariandata-evcc-snippet.png`, staged from the live YAML with the VIN replaced: the bridge was off) |
| `share_live_data` | Share live data (submenu: MQTT bridge, ABRP) | 🟢 Settings-Reference → "The Configure menu" (EN + DE) | 📷 (have, 2026-11-04: `bavariandata-share-live-data.png`, `-de.png`) |
| `abrp_vehicle` | ABRP: which vehicle (only with 2+ cars) | 🟢 Settings-Reference → "ABRP (A Better Routeplanner)" + Feature-Multiple-Cars-and-Accounts (EN + DE) | 📷 (needed) |
| `action_abrp` | ABRP (A Better Routeplanner) (always per car: send, token, position; own API key shared, in "All vehicles" with 2+ cars; switching on checks the token) | 🟢 Settings-Reference → "ABRP (A Better Routeplanner)" (EN + DE) | 📷 (have, 2026-11-04: `bavariandata-abrp.png`, `-de.png`; token fields empty) |
| `trips_vehicle` | Trips: which vehicle (only with 2+ cars) | 🟢 Settings-Reference → "Trips" + Feature-Multiple-Cars-and-Accounts → "Telling the cars apart" (EN + DE) | 📷 (have, staged two-car: `bavariandata-vehicle-picker.png`) |
| `action_trips` | Trips (per car with 2+ cars; shared settings in the collapsed "All vehicles" section) | 🟡 | 📷 (have, 2026-11-09: `bavariandata-configure-trips.png`, `bavariandata-configure-trips-second-car.png`) |
| `action_mileage` → `mileage_vehicle` (2+ cars) → `mileage_lease` / `mileage_yearly` | Mileage allowance (always per car, even with one car) | 🟢 Settings-Reference → "Mileage allowance" + Feature-Mileage (EN + DE) | 📷 (have, 2026-10-14: `bavariandata-mileage-allowance.png`, `-lease.png` with example figures, `-yearly.png`) |
| `action_units` | Units: distance, fuel, temperature, tire pressure (account-wide; Automatic by default; explicit choices also set the sensors' display units) | 🟢 Settings-Reference → "Units" (EN + DE) | 📷 `bavariandata-units.png` (EN + DE) |
| `action_debug_logging` | Debug logging toggle | 🟡 | 📷 |

### Settings (option keys) — the fields inside those screens

| Option key | Screen | Status |
| --- | --- | --- |
| `price_mode` (none/fixed/entity) | Charging costs & history | 🟡 |
| `price_fixed` | Charging costs & history | 🟡 |
| `price_entity` | Charging costs & history | 🟡 |
| `price_currency` | Charging costs & history | ❌ |
| `grid_energy_entity` (wallbox) | Charging costs & history | 🟢 Feature-Charging-History-and-Cost → "Grid energy vs. battery energy" + Feature-evcc-and-Wallbox-Bridge (inbound half, incl. when a reading is refused, and "It also drives the state-of-charge estimate") |
| `charging_loss_percent` | Charging costs & history | 🟡 |
| `history_retain_months` | Charging costs & history | 🟡 |
| `statistics_import` | Charging costs & history | 🟡 |
| `charge_locations` (Remember charging locations; off deletes coordinates) | Charging costs & history | 🟢 Settings-Reference + Feature-Charging-History-and-Cost → "Remember charging locations" (EN + DE) |
| `refresh_on_start` | Automatic data refresh | 🟢 Settings-Reference → "Automatic data refresh" + Feature-Charging-History-and-Cost → "Restarting while the car is charging" |
| `bridge_enabled` | MQTT bridge (evcc & more) | 🟢 Settings-Reference + Feature-evcc-and-Wallbox-Bridge (incl. "Away from home, the status is withheld") |
| `mileage_kind`, `mileage_start`, `mileage_end`, `mileage_allowance_km`, `mileage_start_km`, `mileage_extra_price`, `mileage_unused_refund`, `mileage_tolerance_km` (lease only, section `lease_costs`; #325: Feature-Mileage → "What it costs at the return", EN + DE; `-lease.png` needs a reshoot with the collapsed section) | Mileage allowance (stored under `vehicles[VIN]`) | 🟢 Settings-Reference → "Mileage allowance" + Feature-Mileage |
| `unit_distance`, `unit_fuel`, `unit_temperature`, `unit_pressure` | Units | 🟢 Settings-Reference → "Units" (EN + DE) |
| `bridge_stream_enabled` | MQTT bridge (evcc & more), per car | 🟢 Settings-Reference + Feature-evcc-and-Wallbox-Bridge → "Sharing the whole stream, with other apps" |
| `bridge_stream_hide_position` | MQTT bridge (evcc & more), all vehicles | 🟢 Settings-Reference + Feature-evcc-and-Wallbox-Bridge → "Sharing the whole stream, with other apps" |
| `bridge_topic_prefix` | MQTT bridge (evcc & more) | 🟢 Settings-Reference + Feature-evcc-and-Wallbox-Bridge |
| `bridge_retain` | MQTT bridge (evcc & more) | 🟢 Settings-Reference + Feature-evcc-and-Wallbox-Bridge |
| `abrp_enabled`, `abrp_token`, `abrp_position` | ABRP (A Better Routeplanner), always per car (stored under the VIN even with one car) | 🟢 Settings-Reference → "ABRP (A Better Routeplanner)" (EN + DE) |
| `configurator_upholstery` | Vehicle image → Upholstery, always per car (stored under the VIN even with one car; absent means BMW's default) | 🟢 Settings-Reference → "Vehicle image" (EN + DE) |
| `abrp_api_key` | ABRP (A Better Routeplanner), all vehicles | 🟢 Settings-Reference → "ABRP (A Better Routeplanner)" (EN + DE) |
| `trip_work_zone` | Trips | 🟡 |
| `trip_default_class` (default type) | Trips | ✅ |
| `trip_commute_gap` (commute stop tolerance) | Trips | ✅ |
| `trip_geocode` | Trips | 🟡 |
| `trip_track` (route recording) | Trips | ✅ |
| `trip_weather` (weather along the route; also the day's temperature at the parked car, marked on the efficiency chart) | Trips, all vehicles | 🟢 Settings-Reference + Feature-Trips → "Weather along the route (opt-in)" + Feature-Efficiency-and-Range → "Consumption by outside temperature" + Feature-Export → CSV (EN + DE), screenshot `bavariandata-configure-trips.png` |
| `trip_detection` (live / end-of-trip reports) | Trips | 🟢 Settings-Reference + Feature-Trips → "Older cars…" (EN + DE), screenshot `bavariandata-configure-trip-detection.png` |
| `trip_phone_tracker` / `trip_phone_in_car` and `_2` (route from a phone, up to two) | Trips → Route from a phone | 🟢 Settings-Reference + Feature-Trips → "The route from a phone" (EN + DE), screenshot `bavariandata-configure-trips-phone.png` |
| `trip_debug` (trip-capture diagnostics) | Trips | ✅ |
| `vehicles` (per-car settings by VIN: trips, wallbox meter + losses, evcc publish, ABRP) | Trips, Charging costs, evcc bridge, ABRP | 🟢 Settings-Reference → "Trips", "Charging costs & history", "evcc / wallbox bridge" (EN + DE) |
| `debug_log` | Debug logging | 🟡 |
| `mqtt_keepalive` (hidden override) | — | ❌ |
| `diagnostic_log_interval` (hidden override) | — | ❌ |

### Dashboard card views — from `bavariandata-card.js`

| Config | View | Status | Shot |
| --- | --- | --- | --- |
| *(default)* | Overview — top line (open → charging → driving → waiting → low → ready), charge bar with target/gain/used, range with the real range, tiles that follow the situation; drivetrain-aware (electric / plug-in hybrid with tank line / petrol-diesel / bare, `drivetrain:` override) | 🟢 The-Dashboard-Card → "Overview" + YAML reference (EN + DE) | 📷 (have, 2026-11-09, site demo with situations staged in the browser: `card-overview-ready/charging/driving/open.png`, `card-overview-ready-dark.png`; hybrid, petrol, plain `card-overview-hybrid/petrol/plain.png` from the test fixtures) |
| `view: charging` | Charging history — month figures (kWh, cost, €/kWh, sessions/away), energy-source bar, kWh per day, sessions with from→to bar and cost, live charge on top, curve with its average | 🟢 The-Dashboard-Card → "Charging history" (EN + DE) | 📷 (have, 2026-11-09: `card-charging-month.png`, site demo) |
| `view: charging` → **Places** tab | Charging places: visits, AC/DC, average power, typical stop; expanded best visit, minutes per 10 %, preconditioned split, overlaid curves; map with remembered locations | 🟢 The-Dashboard-Card → "Charging history" + Feature-Charging-History-and-Cost → "Charging places" (EN + DE) | 📷 needed (no away charge on the i5 yet) |
| `view: trips` | Driving journal — by day with day totals, type dot, weather, consumption colored against the month, type filter from the split, **List \| Map** tab | 🟢 The-Dashboard-Card → "Trips" (EN + DE) | 📷 (have, 2026-11-09: `card-trips-days.png`, `card-trips-map.png`, site demo) |
| `view: map` | Trip map (trip ends clustered), alone or as the Trips view's Map tab | 🟢 The-Dashboard-Card → "Trip map" (EN + DE) | 📷 (have: `bavariandata-map.png`, `card-trips-map.png`) |
| `view: health` | Battery health | 🟡 | 📷 |
| `view: efficiency` | Efficiency & real range — comparison bars (yours / the car's / mild weather), why it reads higher than trips | 🟢 The-Dashboard-Card + Feature-Efficiency-and-Range | 📷 (have, 2026-11-09: `card-efficiency-bars.png`, site demo; the temperature chart only in the older `card-efficiency.png`) |
| `view: mileage` | Mileage overview + allowance | 🟢 The-Dashboard-Card → "Mileage" + Feature-Mileage (EN + DE) | 📷 (have, 2026-10-14: `card-mileage.png`, live overview with an example lease) |
| `view: fuel` | Fuel log for petrol/diesel/PHEV: tank now, refuels detected from tank jumps, l/100 km between refuels, BMW OBFCM lifetime figure; shown by `charging`/`efficiency` on a petrol or diesel car | 🟢 The-Dashboard-Card → "Fuel" (EN + DE) | 📷 (have, 2026-10-18: `card-fuel.png`, rendered from the shipped card; refuels staged through `history/fuel.py`) |
| `view: equipment` | Factory equipment by name, with each option's picture where BMW has one (from the configurator picture source) | 🟢 The-Dashboard-Card → "Equipment" (EN + DE) | 📷 (have: `card-equipment.png`, with the option pictures, i5) |
| `hub: true` | Overview with the rooms under it (Charging, Trips, Efficiency, Car; Fuel on petrol/diesel), each row live while charging or driving; a tap opens the room in the same card with a back arrow | 🟢 The-Dashboard-Card → "The hub" + YAML reference (EN + DE) | 📷 (have, 2026-11-09: `card-hub.png`, `card-hub-dark.png`, `card-hub-room.png`, site demo) |
| `view: car` | The car itself: banner, top-down drawing with closures and tire states, pressures at the corners (tap for tire detail), Check Control, service, 12 V, battery health, lease | 🟢 The-Dashboard-Card → "The car" + YAML reference (EN + DE) | 📷 (have, 2026-11-09: `card-car.png`, site demo, no lease line) |
| `view: glance` · `view: row` · `view: garage` | Small cards: a half-width tile, one line, every car of every account; `navigation_path:` opens another dashboard on a tap; sizes for sections dashboards | 🟢 The-Dashboard-Card → "Small cards and the garage" + YAML reference (EN + DE) | 📷 (have, 2026-11-09: `card-glance.png`, `card-glance-charging.png`, `card-row.png`, `card-garage.png`, site demo) |
| `tiles:` | Overview tile grid chosen by the user: ready-made tiles (`odometer` … `battery_12v`) or any entity of the car, in order, from the editor's **Tiles** picker or YAML `{entity, name, icon}`; petrol/diesel and bare cars default to tires, location, distance this month | Overview | 🟢 The-Dashboard-Card → "Choosing the tiles" + YAML reference (EN + DE) | 📷 (have: `card-editor-tiles.png` + `card-overview-tiles.png`, live i5 on beta.4, 2026-10-25) |
| Overview hero with the configurator picture (spin, lights/night, top-down inset while something is open, interior panorama; angle/mode/look remembered per browser) | Overview | 🟢 The-Dashboard-Card → "Overview" (EN + DE) | 📷 (have: `card-hero-spin.png`, `card-hero-inside.png` (the hero of the older interior shot, cropped); the badges and `card-hero-badges.png` are gone with the redesign) |
| `cluster: tire` | Tires (pressure + wear) | 🟡 | 📷 (have) |
| `shape:` (tire + closures) | Top-down car drawn per model — BMW sedan, or MINI Countryman picked from basic data's `model_name`; `shape:` override | 🟢 — The-Dashboard-Card → "The car's shape" + YAML reference | 📷 (have, rendered from the shipped card: no Countryman to capture live) |
| `cluster: closures` | Security & closures — side doors/windows placed by steering side (BMW basic data, `steering:` override) | 🟡 — the steering side and its override are 🟢 in The-Dashboard-Card → "Security & closures" + YAML reference | 📷 (have: left-hand drive, and `card-closures-rhd.png` — `steering: right`, door and windows staged open in the browser only) |
| `cluster: events` | Vehicle events — current Check Control messages first, cleared ones folded under "Earlier messages", then the teleservice timestamps | 🟢 The-Dashboard-Card → "Single-cluster list" | 📷 (have, 2026-09-27, cleared message under "Earlier messages", expanded) |
| `cluster: <other>` | Single-cluster list | 🟡 | 📷 |
| Coming from an older card (every existing config in the new look) | — | 🟢 The-Dashboard-Card → "Coming from an older card" (EN + DE) | — |
| YAML options (`device`, `vin`, entity overrides) | — | 🟡 | — |

### Services — 21 from `services.yaml`

Targeting (the **Vehicle** picker `device_id`, `vin`, `entry_id`; one-car services refuse a call naming no car once there are two): 🟢 Services-Reference intro + Feature-Multiple-Cars-and-Accounts (EN + DE).

| Service | Quota | Status |
| --- | --- | --- |
| `fetch_telematic_data` | spends | 🟡 |
| `fetch_vehicle_mappings` | spends | 🟡 |
| `fetch_basic_data` | spends | 🟡 |
| `fetch_charging_history` | spends | 🟡 |
| `fetch_tyre_diagnosis` | spends | 🟡 |
| `fetch_location_charging_settings` | spends | 🟡 |
| `fetch_vehicle_image` | spends | 🟡 |
| `get_charging_sessions` | free | 🟢 Services-Reference (whole-day `from`/`to`) |
| `get_charging_places` | free | 🟢 Services-Reference + Feature-Charging-History-and-Cost → "Charging places" (EN + DE) |
| `get_trips` | free | 🟢 Services-Reference (`open_trips`, whole-day `from`/`to`) |
| `get_driving_summary` | free | 🟢 Services-Reference + Feature-Trips → "How consumption is measured" |
| `get_efficiency` | free | 🟢 Services-Reference + Feature-Efficiency-and-Range |
| `get_mileage_report` | free | 🟢 Services-Reference + Feature-Mileage |
| `get_fuel_history` | free | 🟢 Services-Reference + The-Dashboard-Card → "Fuel" |
| `get_evcc_config` | free | 🟢 Services-Reference + Feature-evcc-and-Wallbox-Bridge |
| `set_trip_class` | free | 🟡 |
| `set_charge_cost` | free | 🟢 Services-Reference + Feature-Charging-History-and-Cost → "Entering what a charge cost" + The-Dashboard-Card (EN + DE) |
| `mark_trip_start` | free | 🟢 Services-Reference + Feature-Trips → "Older cars…" (EN + DE) |
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
| `soc_estimate`, `soc_rate` | Extrapolated SoC helpers | 🟡 — what it is vs BMW's SoC fields is 🟢 in Feature-Entities-and-Devices → "Which state-of-charge entity to use"; the wallbox-meter mode and its attributes (`estimate_source`, `meter_sessions`, `meter_percent_per_kwh`, `meter_overhead_kw`) are 🟢 in Feature-evcc-and-Wallbox-Bridge → "It also drives the state-of-charge estimate" |
| `charge_eta` | Minutes to the charge target at home, from the wallbox meter | 🟢 Feature-evcc-and-Wallbox-Bridge → "Time to the charge target" + Feature-Entities-and-Devices + The-Dashboard-Card |
| `charged_energy_total`, `charged_energy_session` | Energy integration | 🟡 |
| `charging_energy_month` | Monthly total | 🟡 |
| `charging_cost_month`, `charging_cost_session`, `charging_cost_per_100km` | Cost | 🟡 |
| `battery_health` | Learned capacity | 🟡 |
| `real_range` | Measured consumption → range now, vs BMW's own estimate | 🟢 Feature-Efficiency-and-Range + Feature-Entities-and-Devices + The-Dashboard-Card |
| Look-alike BMW fields: which range and which state-of-charge entity to use; `unknown` (no value yet, what each derived entity waits for, Real Range's `status`) vs `unavailable` (integration not running) | 🟢 Feature-Entities-and-Devices → "Which range entity to use" + "Which state-of-charge entity to use" + "Unknown or unavailable?" + Feature-Efficiency-and-Range → "The Real Range sensor" + Troubleshooting-and-FAQ → "One sensor reads unknown or looks stuck" (EN + DE; issue #62) |
| `driving_distance_month` | Monthly distance + split | 🟡 |
| `trip_in_progress` (binary_sensor) | The drive under way, with live attributes | 🟢 Feature-Trips → "Seeing the drive that's happening now" + Feature-Entities-and-Devices + The-Dashboard-Card |
| `charging_cable` (binary_sensor, `plug` class) | Whether a cable is in, by the evcc bridge's plug rule; for other integrations (SpotNav#2); attrs `timestamp`/`source`/`from_before_restart` (#317) | 🟢 Feature-Entities-and-Devices → derived table (EN + DE) |
| `tyre_status`, `tyre_<position>` (×4) | Tire wear / condition from the REST diagnosis | 🟢 Feature-Entities-and-Devices + The-Dashboard-Card |
| `api_quota_remaining` | Diagnostic | 🟡 |
| `connection_status`, `last_message`, `last_telematic_api` | Diagnostics | ❌ |
| `car` (device_tracker) | Location | ❌ |
| `last_vehicle_report` (disabled by default; the car's own time of its last odometer report) | Diagnostic for logbooks | 🟢 Feature-Entities-and-Devices → derived table (EN + DE) |
| GPS latitude / longitude sensors (disabled by default, opt-in for add-ons that only take `sensor.*`) | Position as plain sensors | 🟢 Feature-Entities-and-Devices → "Device tracker" (EN + DE) |
| `vehicle_image` (image) | Cached render | 🟡 |
| `service` (calendar, disabled by default; service items as all-day events, distance-only items projected from the odometer log's 90-day pace) | Service calendar | 🟢 Feature-Entities-and-Devices → "Service calendar" (EN + DE) | 📷 (have, 2026-10-14: `bavariandata-service-calendar.png`) |
| `mileage_allowance` (sensor, disabled by default; balance against the allowance's plan, km) | Mileage allowance balance | 🟢 Feature-Entities-and-Devices → derived table + Feature-Mileage (EN + DE) |

### Cross-cutting features

| Feature | Status |
| --- | --- |
| Install: the Marketplace (HA 2026.11+), HACS before it, beta switch, add from link | 🟢 Getting-Started-2-Install (all sections) |
| Charging history & cost | 🟡 |
| Charged-energy accuracy (burst sampling, the SoC ceiling) | 🟢 Feature-Charging-History-and-Cost → "How accurate is it?" |
| A car that never streams SoC (no arc, no ceiling; issue #6) | 🟢 Feature-Charging-History-and-Cost → "No start/end SoC on a session" |
| Battery health (learning method, sample rules, why it can stall) | 🟢 Feature-Battery-Health → "What counts as a sample" + "Learning mode" |
| Restarting mid-charge (session survives; the `interrupted` flag) | 🟢 Feature-Charging-History-and-Cost → "Restarting while the car is charging" + Feature-Battery-Health → "What counts as a sample" |
| Solar & energy sources (the PV/battery/grid split, what it costs) | 🟢 Feature-Charging-History-and-Cost → "Where the energy came from" + Settings-Reference → "Solar & energy sources" (screen shot 2026-09-12) + The-Dashboard-Card; the Configure menu shot is refreshed to all fifteen actions (superseded: the bridge makes it sixteen) |
| Efficiency & real range (the window rule, the two sides of the charger, the measured charging loss) | 🟢 Feature-Efficiency-and-Range + The-Dashboard-Card — the `view: efficiency` card shot 2026-09-12, showing the comparison against the car's own prediction |
| Mileage & allowance (daily odometer log seeded from charging sessions, month interpolation, 90-day pace, lease vs yearly limit, lease end in the service calendar) | 🟢 Feature-Mileage (EN + DE) |
| evcc / wallbox bridge — outbound SoC over MQTT (unknown publishes nothing, the heartbeat, switch-off clears) and inbound wallbox metering | 🟢 Feature-evcc-and-Wallbox-Bridge + Settings-Reference → "evcc / wallbox bridge" + Services-Reference → `get_evcc_config`; the Configure menu shot is current (18 actions, re-shot 2026-10-14) |
| Trips / Fahrtenbuch (+ legal caveat) | 🟡 |
| Consumption: plug-side balance, battery-side average, the 3 % SoC gate | 🟢 Feature-Trips → "How consumption is measured" + The-Dashboard-Card |
| Month window on the trips & charging card views | 🟢 The-Dashboard-Card → "One month at a time" (both views) |
| Commute chains (a stop on the way) + default type | 🟢 Feature-Trips → "Classification" + Settings-Reference → "Trips" |
| Deferred close when the position stream goes quiet | 🟢 Feature-Trips → "When the position stream goes quiet" |
| Older cars that report only at trip end (iDrive 6, i3): no trips, other values still update; community logbook linked | 🟢 Feature-Trips → "Older cars that report only at the end of a trip" (EN + DE) |
| Live "trip in progress" (entity + hero badge + trips row) | 🟢 Feature-Trips → "Seeing the drive that's happening now" + The-Dashboard-Card |
| Petrol / diesel / plug-in hybrid: battery-only entities not created (and removed on upgrade), battery card views explain themselves, fuel volume with statistics, hybrid trips withhold kWh/100 km, hybrids get no ledger consumption / real range / cost per 100 km (entities removed on upgrade, `status: plug_in_hybrid`) | 🟢 Feature-Entities-and-Devices → "Derived & diagnostic entities" + The-Dashboard-Card → "Overview" + Feature-Trips → "How consumption is measured" + Feature-Efficiency-and-Range → "Plug-in hybrids" |
| Removing a car (device Delete wipes its data) + car no longer on the account (Repairs warning) | 🟢 Feature-Multiple-Cars-and-Accounts → "Removing a car" (EN + DE) |
| An options screen with empty labels after an update (the browser keeps an integration's flow texts until reloaded; hard refresh / reset the app's frontend cache) | 🟢 Troubleshooting-and-FAQ → "A Configure screen shows empty lines after an update" (EN + DE; #287) |
| Fields the portal lists but a car never sends (iDrive 6 tire diagnosis, MINI tank level, Cooper SE tire pressure); placeholder answers create no sensor | 🟢 Troubleshooting-and-FAQ → "A field the portal lists never shows up" + Supported-Cars rows (EN + DE; #174, #180) |
| BMW Motorrad unsupported (Repairs warning) | 🟢 Troubleshooting-and-FAQ → "Does BavarianData work with a BMW motorcycle?" + Getting-Started-1 → "Requirements" + README |
| Energy dashboard & long-term statistics (incl. the home-only `charging_energy_home` series the dashboard should use) | 🟡 Feature-Energy-and-Statistics → "Adding to the Energy dashboard" (EN + DE) |
| Export (CSV / HTML report) | 🟡 |
| Charging events (`bavariandata_charging_*`) | 🟡 |
| Device triggers (arrived at / left a zone, trip ended anywhere, parked unlocked, parked with a window open, plugged in but not charging, charging started / completed / stopped before the target, preconditioning started / finished, alarm went off, new Check Control message) — offered only when the car can fire them; live values only, nothing fires from a restart; edges only on a fresh streamed value; the first Check Control list only seeds; alarm unverified on a real alarm; `trigger.data` per type | 🟢 Feature-Automations → "Device triggers" (both languages); 📷 `automation-device-triggers.png` (both languages; predates the six new triggers — reshoot once installed live) |
| Trigger events (`bavariandata_zone_*`, `bavariandata_trip_ended`, `bavariandata_situation`, `bavariandata_charging_interrupted`, `bavariandata_charging_paused`/`_resumed` (finished at once, pauses after 2 min, one session), `bavariandata_preconditioning`, `bavariandata_alarm`, `bavariandata_check_control`) | 🟢 Feature-Automations → "Events" |
| Vehicle-report event (`bavariandata_vehicle_report`: new odometer reading held 5 min; first reading only seeds; no repeat, no restart replay) | 🟢 Feature-Automations → "Events" (EN + DE) |
| Automation blueprints (9: stop at target, notify complete, charging on the lock screen, cable → switch, arriving home, rain + open window, frost + unplugged, tire losing pressure, car left open at night) | 🟡 Feature-Automations → "Starter blueprints" (EN + DE): installed and updated by the integration (hand-edited left alone; copies imported by URL show twice and are updated too), phones from a list with *Older setting*, cars-only picker, *Car picture* on the charging-complete message (opt-in; screenshot pending), the lock-screen card (bar up to the target, refresh every 5 min and on a power change but never within 3 min (500 notifications a day per phone), a new charge ends a left-over card, *Wallbox power*, symbols instead of words, the result's duration, ⏸ while a stop short of the target is held, *Open on tap*: start page or a dashboard) with its charging, result and Dynamic Island (compact, held open, result) screenshots; arriving home states that intent is unknown, the start-zone filter and Waze/Google/Proximity as sources |
| Assist / LLM: the BavarianData LLM API (six tools: status, range check (battery and tank on a PHEV or fuel car), charging, driving month, mileage, efficiency), how to tick it in the agent and the MCP server, what the AI sees (no VIN, position, trip ids or curves) | 🟢 Feature-Assist-and-AI (EN + DE); Feature-Automations keeps a pointer |
| State of charge while charging (BMW's SoC goes quiet mid-charge; the estimate, the meter model, which entity, FAQ) | 🟢 Feature-State-of-Charge (EN + DE), linked from Entities, Troubleshooting, the site's landing FAQ |
| ABRP live data (what is sent, setup with the app's token path, free account = one car, position, troubleshooting) | 🟢 Feature-ABRP (EN + DE) + Settings-Reference → "ABRP (A Better Routeplanner)" |
| Smart charging with other integrations (EV Smart Charging, Predbat: which entity goes where, the predicted SoC as the planner's input, the iX3 target caveat, home-only cable blueprint) | 🟢 Feature-Smart-Charging (EN + DE) |
| API quota + Repairs issue | 🟡 |
| Daily REST refresh (container + tire, 2 req/day **per vehicle**) | 🟢 Feature-API-Quota → "The daily refresh" + Services-Reference |
| Multi-vehicle accounts: every car is refreshed, not just one (issue #13) | 🟢 Feature-API-Quota → "The daily refresh" + Services-Reference (`fetch_telematic_data` without a `vin`) |
| One entry per account (a second entry for the same account is refused: one stream, one quota), two accounts side by side, and how to name the car in the card / services / automations / the bridge | 🟢 Feature-Multiple-Cars-and-Accounts (all sections) + Troubleshooting → "This BMW account is already set up in Home Assistant" + Settings-Reference → "Debug logging" (the log level is shared) |
| A car added to the account after setup: entities arrive on the stream by themselves, its name/model are fetched on first sight, its coverage grace window is its own, its Data Selection still has to be ticked per car; discovery after Choose streamed data / re-auth, and the corner cases (parked car, primary only, quota, no auto-retry, re-add keeps history) | 🟢 Feature-Multiple-Cars-and-Accounts → "Adding a car later" (incl. "Corner cases") + Troubleshooting → "A new car shows up as its VIN, with no model" |
| Descriptor-coverage self-test (the report lists every gap; the Repairs warning fires only for a silent cluster, never for events, a cluster the drivetrain can't fill, or a lone cluster still silent after 30 days — the car doesn't have it) | 🟢 Services-Reference → `get_coverage_report` + Getting-Started-4 → "Did it work?" + Troubleshooting → "No data arriving" (names both repairs) |
| Clean uninstall / fresh-install reset | 🟡 Troubleshooting → "Removing BavarianData completely" + `docs/clean-install.md` |
| Card auto-registration (Lovelace resource; YAML-mode caveat) | 🟢 The-Dashboard-Card intro + Troubleshooting → "Every card shows Configuration error after a reload" |
| No dashboard is auto-created — the user places the card (issue #4) | 🟢 README quick start step 5 + Home → "Start here" + Getting-Started-4 outro + The-Dashboard-Card intro + Troubleshooting → "No dashboard appears after setup" |
| Card missing in the companion app only — app cache / proxy path (issue #4) | 🟢 The-Dashboard-Card intro + Troubleshooting → "Custom element doesn't exist … in the mobile app" |
| Card shows key names instead of text — its English strings file (`bavariandata-card-strings-en.js`) didn't load: cache / proxy path | 🟢 Troubleshooting → "The card shows names like `no_vehicle_title` instead of text" (EN + DE) |
| Card missing because one of its parts (`bavariandata-card-<part>.js`, console: *could not load its parts*) didn't load: proxy path | 🟢 Troubleshooting → "Custom element doesn't exist", test 2 (EN + DE) |
| Diagnostics download (what it holds, what it redacts, no quota cost) | 🟢 Troubleshooting → "Download diagnostics" + "Where to get help" |
| What appears in logs — masked VIN at default level, full VIN only under debug | 🟢 Troubleshooting → "Debug logging" + Settings-Reference → "Debug logging" |
| Display units are the user's choice; values are stored as the car sends them (issue #7) | 🟢 Troubleshooting → "A sensor shows an absurd value, or reads unknown after an update" |
| Which lock entity to automate on — streamed `door.status` vs REST-only `door.lock.status` (issue #8) | 🟢 Feature-Entities-and-Devices → "Which lock entity to use" |
| Switching from the dead BMW Connected Drive integration (bimmer_connected, blocked by BMW on 29 Sep 2025): what replaces it, what's lost (commands), entity equivalents | 🟢 Coming-from-bimmer_connected (EN + DE) + website home ("Switching from Connected Drive") |
| Which cars work: no model list, the portal-tile check, what each drivetrain gets, models owners have confirmed | 🟢 Supported-Cars (EN + DE) + README requirements |
| The website: live card demo on a scrubbed real car, per-descriptor data reference (curated notes on the featured descriptors in `site/src/data/descriptor-notes.json`; the rest noindex), search | 🟢 `site/` (renders `docs/wiki`; demo data from `site/scripts/capture_demo.py`) |
| Eleven shipped languages (English, English (UK), Deutsch, Français, Italiano, Español, Nederlands, Polski, Português, Čeština, Svenska): names follow HA's language, entity IDs never do; field names outside English are BMW's own per-language catalogue names; English is US because BMW's field names are (PR #24) | 🟢 Feature-Entities-and-Devices → "Entity names and your language" (EN + DE) |

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
5. 🟡 Screenshot pass — captured via the Playwright workflow and committed to
   `main`: the HA-side shots (cluster picker, Configure menu, charging-costs
   settings, charging & battery-health card views), the **4 setup shots**
   (chooser, manual, guided, bookmarklet) and the **7 BMW-portal shots** (account →
   vehicle overview → CarData client → stream setup → bookmarklet running/done;
   cropped, PII pixelated). Remaining are deliberately deferred because they
   cannot be captured safely: `authorize` / `authorize_failed` (submitting a client
   id **deletes** the existing entry with that id), `guided_wait` (https-only, and
   the test instance is http), `guided_done` / `activate_stream_done` (need a real
   activation), and the **trip-in-progress** hero badge / live trips row (only exist while the
   car is actually being driven — grab them opportunistically on a real drive).
   The Trips and Map card views are shot from the website's demo (made-up
   places), the Trips settings screen with its work zone redacted.
6. ✅ German pass — every page has a German counterpart in `docs/wiki/de/`,
   named `DE-<English page>.md` and published flat beside the English ones (a wiki
   page name is global). The sidebar carries both languages and each page links to
   its counterpart. German sections repeat the **English** section ids as
   `<a id>` anchors, so cross-links read the same in both languages.
   `tests/test_wiki_links.py` fails on a broken page or section link, a missing
   counterpart, or a page missing from the sidebar. UI labels follow the German
   `translations/de.json` and the card's German strings; screenshots stay English.
   **Keep them in lockstep: a change to an English page lands in its German page
   in the same change.**

### Publishing the staged Wiki — done

The English manual lives in **`docs/wiki/`** (the source of truth, reviewable in
PRs) and is **published**. Sync it with
[`scripts/publish-wiki.sh`](../scripts/publish-wiki.sh) after any change; the
script no-ops when the wiki is already in sync, so it is safe to run on every
commit. Screenshots referenced by the pages must be **committed to `main`** for
the raw-URL images to load.

(Historical note, in case a future repo hits it: GitHub provisions the wiki git
remote only after the **first page is created in the web UI** — until then a
clone/push to `…/BavarianData.wiki.git` 404s and there is no API to force it.)

### Status after this rollout

Every 🟡 row in the matrix is now written English prose in `docs/wiki/`, and the
❌ rows are covered too (incl. `get_coverage_report`, which the README table used
to omit — the README no longer carries a services table at all). Remaining work
is the screenshot pass above; the German pass is done.
