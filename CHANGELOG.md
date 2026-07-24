# Changelog

All notable changes to BavarianData are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A curated changelog is kept from v0.9.0-beta.6 onward and backfilled to the last
stable release (v0.8.1); releases before that used auto-generated notes.

## [Unreleased]

## [0.9.0-beta.9] - 2026-07-24

### Fixed
- **Already-imported home charges now show "Home" instead of "Away".** beta.8
  taught new imports to resolve a zone, but sessions imported by an earlier build
  kept their unresolved location, and a re-import wouldn't overwrite it — so a
  charge at home stayed labelled "Away" forever. On startup those legacy records
  are now re-resolved locally (no BMW request, no quota) and a re-import upgrades
  an unresolved location in place rather than skipping it.
- **Public charges now show BMW's address.** When a charge's location matches no
  Home Assistant zone, BMW's own `formattedAddress` (which the charging-history
  API already returns) is kept as the label and shown on the card, in the CSV/
  HTML export, and as a sensor attribute — no reverse geocoding, and still no raw
  coordinates stored.
- **Trips are now detected from the live GPS position stream.** On vehicles that
  don't stream a live `isMoving` / ignition signal or a fresh completed-trip
  batch (e.g. the i5, even with those descriptors selected in Data Selection),
  no trip was ever recorded. Trip detection now opens on GPS movement, sums the
  drive's distance from the position track when no odometer or BMW
  `travelledDistance` is available, and closes after the car has been stationary
  — so drives finally appear in the trip list and the monthly distance sensor.
- **Imported BMW charging now counts toward the monthly energy total.** An
  imported session carries only BMW's measured grid energy (`grid_kwh`), never
  the stream-integrated battery-side figure, so it was silently excluded from
  the "charging energy this month" sensor. The monthly summary now counts the
  measured grid figure — the same rule the Energy-dashboard statistics and the
  CSV/HTML export already use.
- **A stale "last trip end" field can no longer close a live trip.** BMW repeats
  the previous drive's `trip.segment.end.*` fields (with their old timestamp) in
  every telematic snapshot; these are now ignored unless their timestamp is
  recent, so a parked, charging car no longer looks like a just-finished trip.

## [0.9.0-beta.8] - 2026-07-24

### Changed
- **Imported charging sessions now resolve their location to a Home Assistant
  zone** (Home, Work, …) from BMW's coordinates, the same way live-recorded
  sessions do — so the card shows "Home" for a home charge instead of "Away".
  Only the resolved zone is stored; the raw latitude/longitude BMW returns are
  used for the lookup and then dropped, matching the live path's privacy stance.
- **Card charging view:** the collapsed session row now leads with the energy
  (kWh) instead of the price; the per-session cost moves into the expanded
  detail. The power-curve chart is now stepped — each sampled power holds until
  the next reading rather than being drawn as a diagonal ramp between samples.

## [0.9.0-beta.7] - 2026-07-24

### Added
- **`fetch_charging_history` now imports into the local history**, instead of
  only logging BMW's response. Past charges — including ones from before the
  integration was installed, or from while Home Assistant was down — appear on
  the card's charging view and in the monthly summaries. BMW's measured grid
  energy, SoC, odometer, location and power curve are mapped into our session
  records; a charge that also streamed live is enriched in place (no duplicate),
  and cost is backfilled for a fixed tariff. Re-running the import is idempotent.

### Fixed
- **The Lovelace card showed "not charging" while the car was charging.** The
  overview picked the wrong status entity (`charging.hvStatus`) over the
  authoritative `charging.status` (the "Ladestatus" the integration itself uses
  to detect sessions), and it did not recognise BMW's uncatalogued
  `chargingactive` value. The card now prefers `charging.status`, treats
  `chargingactive`/`charging_in_progress` as active, and renders them as a clean
  localized "Charging" label. The detection heuristic also no longer misreads an
  `inactive` value as charging.
- **Card entity auto-selection ignored multi-word keywords on non-English
  installs.** The picker matched keys like "charging status", "electric range"
  or "connection status" only against the localized friendly name, never the
  English descriptor path — so on a German (or other) install it fell back to
  arbitrary tie-breaks. The descriptor is now also matched word-normalized,
  fixing selection of the charging-status, range, plug and time-to-full tiles
  regardless of Home Assistant's language.

## [0.9.0-beta.6] - 2026-07-24

### Added
- **Descriptor coverage self-test.** A new `get_coverage_report` service (costs
  no BMW API quota) lists, per vehicle, which descriptors from the stream
  clusters you enabled have never actually arrived — so you can tell whether a
  missing entity is down to your BMW Data Selection, your car, or a bug. A
  dismissible repair issue is raised once a gap is genuinely overdue (7-day
  grace period), never a notification.
- **Selectors for every service action.** Action fields (VIN, config entry, date
  ranges, limits, month) now render in Home Assistant's visual action editor
  with proper pickers, instead of being reachable only in YAML mode.

### Fixed
- **`fetch_charging_history` failed with HTTP 400 when run without a date
  range.** BMW requires `from`/`to`; the service now defaults to the last 30
  days when they are omitted and sends the ISO 8601 UTC timestamps BMW expects.
  The `from`/`to` fields also get date/time pickers.
- **Duplicate unique-ID warning after a restart** for the monthly charging
  summary sensors (e.g. "energy this month"), which also spawned a phantom
  duplicate sensor. The restore path now recreates only the correct entity.
- **Vehicle location went unavailable after every restart or upgrade** until the
  car next streamed a GPS position (often hours away). The device tracker is now
  recreated immediately and its last known position restored, so the map shows
  where the car was parked straight away.

## [0.9.0-beta.5] - 2026-07-23

### Fixed
- hassfest validation: manifest keys sorted into the required order.

## [0.9.0-beta.4] - 2026-07-23

### Fixed
- hassfest validation: declare the `recorder` dependency in the manifest, now
  required because the history layer writes long-term statistics.

## [0.9.0-beta.3] - 2026-07-23

### Added
- **Statistics backfill & month export (history layer Phase 4).** The new
  `import_statistics` service rebuilds long-term statistics from recorded
  history, so charging and driving from before the install — or from while Home
  Assistant was down — appear on the Energy dashboard. The new `export_history`
  service returns a month of charging sessions and/or trips as CSV files or a
  self-contained, printable HTML report. Both read the local store and cost no
  BMW API quota.

## [0.9.0-beta.2] - 2026-07-23

### Added
- **Trips / Fahrtenbuch with a month-in-review (history layer Phase 3).** Trips
  are recorded locally with their endpoints stored as place names, never
  coordinates. The `get_trips` service lists them, and `get_driving_summary`
  aggregates a month — distance, the business/private/commute split, consumption,
  recuperation, a driving-style score, top destinations, and (with a tariff
  configured) an estimated driving cost.
- Tagged debug tracing across the history layer
  (`[trip]`/`[charge]`/`[health]`/`[stats]`/`[history]`) to make the opt-in debug
  log readable.

## [0.9.0-beta.1] - 2026-07-23

### Added
- **History layer — the foundation (Phases 0–2).** A local history store that
  records **charging sessions with real cost** (Phase 1) and surfaces them in a
  new **charging-history card view**, plus a **battery-health estimate** learned
  from wide-SoC charges with its own view (Phase 2). All local — no BMW API
  quota.
- German translations for the derived entities and the setup/config flow.

## [0.8.1] - 2026-07-21

### Added
- Options-flow toggle to enable debug logging (opt-in, since debug output can
  contain VIN/GPS).

### Fixed
- GPS and length sensors could fail to be added because of an `AttributeError`
  reading `_attr_device_class`; the read is now guarded.
- Invalid energy `state_class` (`measurement` → `None`) corrected through the
  descriptor metadata pipeline, restoring Energy-dashboard compatibility.
- Device tracker crash (`_update_name`) and a blocking manifest read during
  setup.

### Changed
- Minimum supported Home Assistant raised to **2026.3** (needed for self-served
  brand icons). Untracked `.venv` and dropped the obsolete `brands/` staging dir.
- A clean MQTT `rc=0` disconnect is now logged at debug instead of warning.
- README images use absolute `raw.githubusercontent.com` URLs so they render on
  the HACS info screen.
