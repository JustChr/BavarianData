# Changelog

All notable changes to BavarianData are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A curated changelog is kept from v0.9.0-beta.6 onward and backfilled to the last
stable release (v0.8.1); releases before that used auto-generated notes.

## [Unreleased]

### Added
- **Recorded routes now carry timing.** With **Record route** (`trip_track`) on,
  each GPS fix in a trip's track is stored with the number of seconds since the
  trip started (`track` points become `[lat, lon, t]`), so an upcoming map can
  replay a drive in real time, colour it by pace and show where the car stopped.
  Only affects opted-in recording; routes captured before this update keep their
  two-element `[lat, lon]` points and read back without timing (their times
  can't be backfilled).

### Changed
- **The dashboard card is now the "BavarianData Card".** It was previously shown
  as the "BMW CarData Card" (element `custom:bmw-cardata-card`); the card, its
  element (`custom:bavariandata-card`) and its bundled file have been renamed to
  match the integration's name. Existing dashboards keep working: the old
  `custom:bmw-cardata-card` element is still registered as a hidden alias, so no
  card needs to be re-added. The old name simply no longer appears in the card
  picker.

## [0.9.1] - 2026-07-26

### Added
- **Optional route recording for trips.** A new **Record route** toggle under
  **Configure → Trips** (off by default) makes each new trip additionally store
  its GPS track — the polyline of coordinates along the drive — so a map can show
  where the car went. It is the only setting that persists raw coordinates, is
  independent of address resolution, and takes effect on the next trip that
  starts. The track is served through `bavariandata.get_trips` (a `track` list of
  `[lat, lon]` points) and is never included in the CSV / printable export, which
  stays place-names-only. Trips keep storing named places only when the toggle is
  off, exactly as before.

## [0.9.1-beta.7] - 2026-07-26

### Changed
- **Changing your streamed data is now one click, like guided setup.**
  **Configure → Choose streamed data** no longer hands you a console snippet to
  paste on the portal's Data Selection page. Instead it reuses the guided-setup
  activator: pick your clusters, then run the **Activate BMW data** bookmarklet on
  the portal's stream-setup page and Home Assistant turns the fields on for you
  and continues automatically (on an http instance it falls back to the same
  Copy-and-paste screen guided setup uses). The activator is **additive** — it
  adds the chosen clusters to your live stream but never removes a field you
  already stream; to stop streaming a field, untick it under Data Selection in the
  portal.

## [0.9.1-beta.6] - 2026-07-26

### Fixed
- **MINI vehicles are now handled in guided setup.** The activator recognised only
  a `bmw` portal address, so it would refuse to run on the MINI portal
  (`mini.at`); it now accepts both, and the setup wording is brand-neutral
  ("BMW or MINI"). BMW and MINI share the same CarData backend — the API path is
  `/utilities/bmw/api/cd/…` on both — so a MINI's own portal and MINI-branded
  sign-in page are correct and work identically.

## [0.9.1-beta.5] - 2026-07-25

### Fixed
- **Guided setup no longer hangs on an http Home Assistant.** The browser
  activator can only auto-report to an **https** Home Assistant (an http instance
  blocks the https→http POST as mixed content). The guided flow now detects this
  and, on an http instance, goes **straight to a self-contained paste screen**
  (open the setup page → run the bookmarklet → Copy → paste) instead of a webhook
  wait that could never complete. On https it still auto-continues; the wait's
  fallback timeout dropped from 10 to 5 minutes.

## [0.9.1-beta.4] - 2026-07-25

### Changed
- **Guided setup is now one click, not a copy-paste.** The guided path no longer
  hands you a console snippet to paste. Instead Home Assistant serves a small
  setup page with a one-click **"Activate BMW data"** bookmarklet; you run it on
  the BMW portal and it activates the stream **in your own browser** (no password
  or session ever leaves it), then **reports the result straight back to Home
  Assistant via a webhook** — so setup continues automatically, with nothing to
  copy. A paste fallback remains for Home Assistant instances reached over plain
  HTTP (where the browser blocks the automatic report).
- **The activator is now bulletproof.** It reads BMW's per-vehicle *streamable
  catalogue* and only turns on fields BMW will accept (fixes an HTTP 500 when a
  descriptor wasn't streamable for that car), is **additive** (never removes a
  field you already had) and **idempotent** (re-running a fully-set car does
  nothing), has **per-request timeouts** so a stalled request can't hang it, shows
  live progress, and detects when you're on the wrong page.

## [0.9.1-beta.3] - 2026-07-25

### Added
- **Guided setup (one-snippet onboarding).** Setup now opens with a Guided vs.
  Manual choice. Guided asks which data clusters you want, then hands you a single
  snippet to run on the BMW portal: it discovers your **Client ID** (no more
  copying it by hand), checks your vehicle mapping, and **activates the stream
  fields** for those clusters — all in your browser, so no password or session
  ever leaves it. You paste back a short, non-secret result and the flow fills in
  the Client ID and continues to device authorization. The classic Manual path
  (paste a Client ID) is unchanged. New module `onboarding.py`; portal route
  documented in
  [`docs/reference/stream-attribute-activation.md`](docs/reference/stream-attribute-activation.md).
- **Activate stream fields in one call** — a new `bavariandata.activate_stream_fields`
  service replays the exact request the BMW portal's stream-setup page sends when
  you save *Datenauswahl ändern*, replacing your whole streamed-attribute
  selection at once instead of ticking checkboxes. Stream selection has no CarData
  API and is gated by your browser session (behind BMW's bot-defense), so the
  service takes a **captured portal session** (`base_url`/`locale`/`mapped_vehicle_id`/`cookie`)
  and is a manual, occasional tool — it can't run unattended and spends no API
  quota. Attributes default to your chosen streamed-data clusters. The captured
  cookie is never logged; the service reports requested/accepted counts. See
  [`docs/reference/stream-attribute-activation.md`](docs/reference/stream-attribute-activation.md).

### Fixed
- **Long-term statistics no longer log a deprecation warning.** The statistics
  backfill now sets `unit_class` on each external-statistics series (energy for
  kWh, distance for km, none for currency), which Home Assistant requires from
  2026.11 — silences the "doesn't specify unit_class" warning.
- **Trip/charge close timers now run on the event loop.** The debounced trip- and
  charge-close timer callbacks were plain functions, so Home Assistant dispatched
  them to a worker thread, where `async_create_task` / `async_dispatcher_send` are
  not thread-safe (HA logged a thread-safety warning and warned of possible
  corruption). Both are now `@callback`, so they run inline on the loop.

## [0.9.1-beta.2] - 2026-07-25

### Fixed
- **Trip distance now uses BMW's odometer, not just the GPS track.** The i5
  streams its cumulative odometer as `vehicle.vehicle.travelledDistance` (km),
  but the code only ever read `vehicle.vehicle.mileage` (which the i5 doesn't
  stream), so every trip fell back to the GPS haversine track — which undercounts
  winding roads. The odometer is now read from either descriptor, giving BMW's
  own exact distance on cars that stream it; sub-kilometre trips that don't tick
  the (1 km-resolution) odometer still fall back to GPS.
- **Trips no longer fragment mid-drive.** On the i5 (and other cars that stream
  live GPS), BMW emits `trip.segment` batches repeatedly *during* a drive, not
  just at its end. Each one was treated as a completed-trip marker and closed the
  open trip, chopping one continuous drive into sub-minute pieces that then
  dropped out as noise — so a long drive recorded as a single 0.7 km trip. A
  segment batch now closes a trip only when the GPS track shows the car has
  actually stopped; otherwise the drive stays open and the GPS stationary-close
  debounce owns the end.

## [0.9.1-beta.1] - 2026-07-25

### Added
- **Diagnostics download.** Settings → Devices & Services → BavarianData → ⋮ →
  Download diagnostics now produces a redacted snapshot for triage: integration
  and Home Assistant versions, REST quota state, selected clusters, per-VIN
  descriptor arrival counts and last-message timestamps, and the MQTT
  connect/disconnect history with rc codes. VIN, GCID, client ID, tokens and GPS
  are redacted.
- **Two new repair issues** (Settings → Repairs), each linking to the matching
  Troubleshooting anchor: **no stream data received in 48 h**, and **stream
  unauthorized (MQTT rc=5) persisting** past reauth. The existing quota-exhausted
  repair now links to its Troubleshooting section too.
- **GitHub issue templates.** A bug report form that requires the diagnostics
  attachment and the "did you save Data Selection?" answer, and routes setup /
  BMW-registration questions to Discussions.

## [0.9.0] - 2026-07-24

The big one: a full **history layer**. BavarianData now keeps its own local
store — charging sessions with real cost, a Fahrtenbuch/trips log with a
month-in-review, a learned battery-health estimate, and long-term statistics
that feed the Energy dashboard — none of which touches your 50-request BMW API
quota. Past charges and drives can be imported from BMW's history, and the
Lovelace card gained views for all of it. Everything below accumulated across
the 0.9.0 pre-releases; upgrading from 0.8.1 gets it all at once.

### Added
- **History layer — the foundation (Phases 0–2).** A local history store that
  records **charging sessions with real cost** and surfaces them in a new
  **charging-history card view**, plus a **battery-health estimate** learned from
  wide-SoC charges with its own view. All local — no BMW API quota.
- **Trips / Fahrtenbuch with a month-in-review (Phase 3).** Trips are recorded
  locally with their endpoints stored as place names, never coordinates. The
  `get_trips` service lists them, and `get_driving_summary` aggregates a month —
  distance, the business/private/commute split, consumption, recuperation, a
  driving-style score, top destinations, and (with a tariff configured) an
  estimated driving cost.
- **Statistics backfill & month export (Phase 4).** The new `import_statistics`
  service rebuilds long-term statistics from recorded history, so charging and
  driving from before the install — or from while Home Assistant was down —
  appear on the Energy dashboard. The new `export_history` service returns a
  month of charging sessions and/or trips as CSV files or a self-contained,
  printable HTML report. Both read the local store and cost no BMW API quota.
- **`fetch_charging_history` now imports into the local history**, instead of
  only logging BMW's response. Past charges — including ones from before the
  integration was installed, or from while Home Assistant was down — appear on
  the card's charging view and in the monthly summaries. BMW's measured grid
  energy, SoC, odometer, location and power curve are mapped into session
  records; a charge that also streamed live is enriched in place (no duplicate),
  and cost is backfilled for a fixed tariff. Re-running the import is idempotent.
- **Descriptor coverage self-test.** A new `get_coverage_report` service (costs
  no BMW API quota) lists, per vehicle, which descriptors from the stream
  clusters you enabled have never actually arrived — so you can tell whether a
  missing entity is down to your BMW Data Selection, your car, or a bug. A
  dismissible repair issue is raised once a gap is genuinely overdue (7-day
  grace period), never a notification.
- **Selectors for every service action.** Action fields (VIN, config entry, date
  ranges, limits, month) now render in Home Assistant's visual action editor with
  proper pickers, instead of being reachable only in YAML mode.
- German translations for the derived entities and the setup/config flow.
- Tagged debug tracing across the history layer
  (`[trip]`/`[charge]`/`[health]`/`[stats]`/`[history]`) to make the opt-in debug
  log readable.

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

### Fixed
- **A charge that streamed live *and* was imported from BMW no longer becomes two
  records.** A brief `charging.status` blip (a momentary "not charging" that came
  straight back) split one plug-in into two live sessions; BMW's import then
  enriched only one of them and orphaned the other, so the monthly total counted
  the same charge's energy twice. Two fixes: the session close is now debounced,
  so a status flap no longer splits a live session in the first place; and on
  import, every live-only fragment that falls inside BMW's charge window is folded
  into the single measured record instead of being left behind. Re-running "Fetch
  charging history" once reconciles any already-split charge into one record.
- **The charging-state sensor ("Ladestatus") now shows readable states.** BMW's
  catalogue lists the wrong allowed values for this field (a charging-*mode*
  list), so the actual stream states — `nocharging`, `chargingactive`,
  `initialization`, `chargingpaused`, `chargingended`, `chargingerror` — were
  shown raw and untranslated. The real enum (which BMW documents in the field's
  own description) is now pinned, so the sensor reads "Not charging" / "Charging"
  / "Charging paused" … in English and "Lädt nicht" / "Lädt" / … in German.
- **Already-imported home charges now show "Home" instead of "Away".** Sessions
  imported by an earlier build kept their unresolved location, and a re-import
  wouldn't overwrite it — so a charge at home stayed labelled "Away" forever. On
  startup those legacy records are now re-resolved locally (no BMW request, no
  quota) and a re-import upgrades an unresolved location in place.
- **Public charges now show BMW's address.** When a charge's location matches no
  Home Assistant zone, BMW's own `formattedAddress` (which the charging-history
  API already returns) is kept as the label and shown on the card, in the CSV/
  HTML export, and as a sensor attribute — no reverse geocoding, and still no raw
  coordinates stored.
- **Trips are now detected from the live GPS position stream.** On vehicles that
  don't stream a live `isMoving` / ignition signal or a fresh completed-trip
  batch (e.g. the i5, even with those descriptors selected in Data Selection), no
  trip was ever recorded. Trip detection now opens on GPS movement, sums the
  drive's distance from the position track when no odometer or BMW
  `travelledDistance` is available, and closes after the car has been stationary
  — so drives finally appear in the trip list and the monthly distance sensor.
- **A stale "last trip end" field can no longer close a live trip.** BMW repeats
  the previous drive's `trip.segment.end.*` fields (with their old timestamp) in
  every telematic snapshot; these are now ignored unless their timestamp is
  recent, so a parked, charging car no longer looks like a just-finished trip.
- **Imported BMW charging now counts toward the monthly energy total.** An
  imported session carries only BMW's measured grid energy (`grid_kwh`), never
  the stream-integrated battery-side figure, so it was silently excluded from the
  "charging energy this month" sensor. The monthly summary now counts the
  measured grid figure — the same rule the Energy-dashboard statistics and the
  CSV/HTML export already use.
- **The Lovelace card showed "not charging" while the car was charging.** The
  overview picked the wrong status entity (`charging.hvStatus`) over the
  authoritative `charging.status`, and did not recognise BMW's uncatalogued
  `chargingactive` value. The card now prefers `charging.status`, treats
  `chargingactive`/`charging_in_progress` as active, and renders them as a clean
  localized "Charging" label.
- **Card entity auto-selection ignored multi-word keywords on non-English
  installs.** The picker matched keys like "charging status", "electric range" or
  "connection status" only against the localized friendly name, never the English
  descriptor path — so on a German (or other) install it fell back to arbitrary
  tie-breaks. The descriptor is now also matched word-normalized, fixing
  selection of the charging-status, range, plug and time-to-full tiles regardless
  of Home Assistant's language.
- **`fetch_charging_history` failed with HTTP 400 when run without a date
  range.** BMW requires `from`/`to`; the service now defaults to the last 30 days
  when they are omitted and sends the ISO 8601 UTC timestamps BMW expects. The
  `from`/`to` fields also get date/time pickers.
- **Duplicate unique-ID warning after a restart** for the monthly charging
  summary sensors (e.g. "energy this month"), which also spawned a phantom
  duplicate sensor. The restore path now recreates only the correct entity.
- **Vehicle location went unavailable after every restart or upgrade** until the
  car next streamed a GPS position (often hours away). The device tracker is now
  recreated immediately and its last known position restored, so the map shows
  where the car was parked straight away.
- **hassfest validation:** declared the `recorder` dependency (now required
  because the history layer writes long-term statistics) and sorted the manifest
  keys into the required order.

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
