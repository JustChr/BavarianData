# Changelog

All notable changes to BavarianData are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A curated changelog is kept from v0.9.0-beta.6 onward and backfilled to the last
stable release (v0.8.1); releases before that used auto-generated notes.

## [Unreleased]

### Added
- **A configurable default type for trips.** Anything that isn't a recognised
  home ↔ work commute is now filed as your **Default type** under
  **Configure → Trips** — **Private** out of the box, **Business** if that is the
  honest default for your driving, or **Leave unclassified** to keep sorting every
  trip by hand (the previous behaviour). Trips whose endpoints fall outside any
  zone are covered too; they used to be left blank however obvious they were. An
  automatic class is never more than a starting point: a trip you classified
  yourself is never overwritten.
- **A commute survives a stop on the way.** Buying groceries between home and work
  parks the car long enough that the detector records two drives, neither of which
  is home → work on its own. With the new **Commute stop tolerance** (default
  **30 minutes**) they are recognised as one commute and *both* legs are badged as
  such — retroactively, once the chain arrives. The stops stay visible as separate
  trips in the journal. A chain ends when it reaches home or work, so a lunch run
  out of the office and back is not dragged into the morning commute, and a round
  trip that starts and finishes at home stays private. Set the tolerance to 0 to
  switch chaining off.

### Fixed
- **A quiet position stream no longer splits one drive into two trips.** "No
  movement for five minutes" was treated as the car having stopped, when it can
  just as easily be the stream going silent — a tunnel, a coverage hole, or the
  i5's own sparse cadence. A stop is now only acted on once something confirms it
  (a position report showing the car standing still, or an explicit "not moving");
  otherwise the drive is held open until the reports come back and settle it. A
  car that reappears where it vanished still ends its trip back at the last
  movement, so a park in a signal-dead garage is not merged into the next drive.

## [0.9.2] - 2026-07-27

Trips grow up: every recorded drive can now be drawn on a map — a route line per
trip and a clustering map of where you actually go — and a trip's start and end
times finally describe the drive rather than the moment the detector noticed it.
Tires gain BMW's wear diagnosis next to pressure on a rebuilt tire card. And the
REST poller stops eating your quota: the daily container now carries the 41
fields the stream cannot, taking normal running from about 36 requests a day
down to 2 — while filling entities that until now could only ever sit empty.
Everything below accumulated across the 0.9.2 pre-releases; upgrading from 0.9.1
gets it all at once.

### Added
- **Each trip now shows its route on a map.** Expanding a trip in the Trips card
  view draws that drive on a small map — a single clean line with a start and an
  end marker — for trips recorded with **Record route** on. No map data leaves
  your browser to do it.
- **A destinations map (`view: map`) on the dashboard card.** A new card view
  plots where your trips **end** as markers that **cluster into counted bubbles
  when zoomed out and split apart as you zoom in** — a quick read on where you go
  most, with an honest "times arrived here" count (a trip's start is the previous
  trip's end, so plotting both would double-count). A time-window chip row (This
  month / 3 months / All) filters it. It reuses Home Assistant's own map and
  clustering, so there are no new dependencies, and it reads routes via
  `get_trips`, so it spends no API quota. Destinations appear once **Record
  route** (`trip_track`) is enabled; the map shows a hint until then.
- **Tire wear, on a rebuilt tire card** (card 1.8.1). BMW's smart-maintenance
  tyre diagnosis was being fetched and thrown away — `fetch_tyre_diagnosis`
  logged it and nothing else. It now becomes five entities per car (**Tyre
  Condition** plus one per wheel BMW reports), and the card is a tire card rather
  than a tire-*pressure* card: titled **Tires**, it opens with the two things
  that can actually be wrong with one, side by side — **Pressure** (the measured
  spread across the set, with the shared target under it) and **Wear** (BMW's
  verdict, with the mileage until the soonest wheel is due). Each wheel carries
  its own size, tread pattern, season and fitting date beside it, because
  staggered setups are normal: the i5 runs 245s at the front and 275s at the
  rear, and a single shared line had to show the wrong size for half the car.
  Wear outranks pressure in the wheel colour and the header badge — a tyre BMW
  flags as worn reads "Check tires" even at perfect pressure. Note that BMW
  reports **remaining mileage, not tread depth**; `tread` is the tread *pattern*
  ("EcoContact 6 Q"). Cars with no tyre service record on file get no tyre
  entities and an unchanged card.
- **Sharper trip start and end on cars that stream the driver door** (e.g. the
  i5). The driver door brackets the drive: closing it (you got in) anchors where
  a trip starts, and opening it again after the car has stopped (you got out)
  ends the trip promptly instead of waiting out the 5-minute stationary timer.
  Used only as an accelerator — cars that don't stream the door fall back to GPS
  exactly as before.
- **Recorded routes now carry timing.** With **Record route** (`trip_track`) on,
  each GPS fix in a trip's track is stored with the number of seconds since the
  trip started (`track` points become `[lat, lon, t]`), so a map can replay a
  drive in real time, colour it by pace and show where the car stopped. Only
  affects opted-in recording; routes captured before this update keep their
  two-element `[lat, lon]` points and read back without timing (their times
  can't be backfilled).
- **Trip-capture diagnostics (`Configure → Trips`).** An opt-in troubleshooting
  toggle for improving trip detection. When on, the integration logs the raw
  detector substrate under greppable tags — every GPS fix with its cadence,
  latency, odometer and the close-timer countdown (`[trip.gps]`), the timer
  lifecycle (`[trip.timer]`), full BMW segment batches (`[trip.seg]`), a
  per-message descriptor firehose (`[trip.raw]`) and a per-trip post-mortem
  (`[trip.post]`) — and writes a replayable `bavariandata_trip_capture.ndjson`
  capture to the config folder. Each `[trip.gps]` line also carries the GPS fix
  state, satellite count and heading (to tell a stopped car from a lost fix), and
  a `[trip.watch]` line surfaces catalogue signals that might drive a better
  detector (speed, HV-system and connector state, the ignition trio, driver
  door/lock, active navigation) whenever the car streams them. Independent of
  Debug logging, off by default, and contains GPS/VIN, so it's meant to be
  switched on for a test drive and back off.
- **Documented how to remove BavarianData completely.** Troubleshooting & FAQ has
  a new "Removing BavarianData completely" section: deleting the integration
  already wipes your tokens, your charging/trip history and the statistics it
  published, but the quota log, the cached vehicle image and any trip-capture
  file are left behind — and a trip capture contains GPS coordinates, so it's
  worth deleting. Also covers the one trap when testing a fresh install: long-term
  statistics live in the recorder database, so a partial removal can leave
  `bavariandata:…` series showing up in your Energy dashboard with nothing
  installed.

### Changed
- **The REST container is polled once a day instead of every 40 minutes, and now
  carries everything the stream cannot.** The container held 30 descriptors of
  which 26 duplicated the stream, and was refetched every 40 minutes — about 36
  of the 50 daily requests. It now holds the 41 non-streamable descriptors the
  container endpoint can serve (service demands, Condition Based Servicing,
  check-control messages, door-lock status, lifetime-consumption counters, state
  of health) plus the battery keys for a fast first paint, and refreshes daily.

  **Net effect: from ~36 requests a day to 2** (the container, plus the tyre
  diagnosis on its own endpoint), leaving 48 free for manual fetches — while
  filling entities that until now could only ever sit empty. Existing installs
  migrate on the next token refresh: the old container is deleted and replaced,
  rather than left to idle against BMW's 10-container limit.
- **A trip's start and end times now describe the drive, not the detection.** A
  trip was stamped from the first position fix that registered movement to the
  moment the five-minute stationary timer expired — so a drive from 10:51 to
  10:57 was recorded as 10:54 → 11:00, and its duration overstated by minutes at
  both ends. The start now falls back to when the car was last seen parked (on
  cars that stream the driver door, bounded to five minutes before detection, so
  sitting in the car before pulling away doesn't count as driving), and a
  stationary or segment close ends the trip at the last fix that showed movement
  instead of when the timer fired. A driver-door arrival still ends the trip
  where it always did — the door opening *is* the arrival. Trips already
  recorded keep their old timestamps.
- **The tire-pressure band is no longer symmetric: low from 8% under target, high
  only past 15% over.** The old ±4% flagged every wheel of a perfectly healthy
  car — BMW's target is the *cold* pressure and a tire you have just driven on
  reads 8–10% high. Under-inflation is the condition worth an early hint;
  over-inflation is only worth one past what warm-up explains.
- **The dashboard card is now the "BavarianData Card".** It was previously shown
  as the "BMW CarData Card" (element `custom:bmw-cardata-card`); the card, its
  element (`custom:bavariandata-card`) and its bundled file have been renamed to
  match the integration's name. Existing dashboards keep working: the old
  `custom:bmw-cardata-card` element is still registered as a hidden alias, so no
  card needs to be re-added. The old name simply no longer appears in the card
  picker.
- **Refreshed BMW's API reference material from source.** `docs/reference/` now
  carries Integration Guide **v1.5 (09/01/2026)** and the current Swagger files,
  re-fetched from BMW. Notable spec changes: the charging-history session gained
  `energyDecreaseHvbKwh` and `energyDischargedKwh` and lost
  `chargingCostInformation`; `basicData` gained `reessNominalCapacityGross`; the
  device-code response no longer documents `verification_uri_complete`; and the
  streaming chapter now states MQTT **QoS 0 only** plus a per-IP connection-rate
  policy over a one-minute window. No integration behaviour changes. The README
  in that folder records the exact URLs to re-fetch from, and `tools/README.md`
  now records the direct Telematics Data Catalogue download URL and spells out
  that the catalogue — not the Swagger — is the stream's contract.

### Fixed
- **Cards no longer break on a browser reload.** Every BavarianData card turned
  into a "Configuration error" box after pressing F5 — a hard refresh didn't
  help, only closing and reopening the browser did. The card was published as a
  frontend module, which Home Assistant renders into the page as a
  fire-and-forget `import()`; the frontend's service worker can serve that page
  from cache without it, so the `bavariandata-card` element was never defined
  and every placed card fell back to the error box
  ([home-assistant/frontend#18728](https://github.com/home-assistant/frontend/issues/18728)).
  The card is now registered as a proper Lovelace dashboard resource, which is
  loaded before any dashboard renders and is version-stamped on every update.
  Installations whose dashboard resources are YAML-managed keep the old
  behaviour and should add the resource by hand — see
  [Troubleshooting](https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ#config-error-after-reload).
- **Trip routes were drawn from mismatched latitude/longitude pairs**, putting
  every recorded point somewhere the car never was: a right-angle staircase where
  each vertex took its latitude from the *previous* fix and its longitude from the
  current one. BMW sends the two coordinates as separate messages, and the pairing
  guard compared each coordinate only against its own previous value — so once a
  single unpaired message slipped through (the first one after connect always
  does, with nothing to compare against), the stale half counted as "advanced"
  too and every later fix paired one message behind, permanently. Pairing now
  waits for both halves of a fix to actually *arrive*, and drops BMW's duplicate
  redelivery by the fix's own timestamp. The phantom right-angle points also
  inflated the measured trip distance, which is now correct too. Routes recorded
  before this fix keep their bad points; new drives are correct.
- **Routes now start where the car was parked.** A trip's track (and its start
  place) began at the first point that registered as movement — often a block or
  more past the actual start. It is now seeded from the last known parked
  position, so the line connects from where the drive really began.
- **The tire diagnosis now survives a restart.** Wear, tread, size, season and
  fitting date were held in memory only, so every Home Assistant restart blanked
  the tire sensors and the wear half of the tire card until the next daily
  refresh came due — up to 24 hours later — or `fetch_tyre_diagnosis` was called
  by hand. The last fetched diagnosis is now stored and restored at startup, at
  no cost to the API quota. The sensors also carry a `fetched_at` attribute now,
  so a day-old reading is recognisable as one.
- **The coverage report no longer reports gaps that can never close.** BMW's
  telematic catalogue marks each field with whether the MQTT stream can carry it
  (246 of 295 can), but that column is drawn as a tick glyph rather than text, so
  the catalogue generator read it as empty and the flag never existed. As a
  result **35 fields BMW cannot stream** — tyre diagnosis, vehicle image, state of
  health, Condition Based Servicing, door-lock status, the lifetime-consumption
  counters — were being requested on the stream and counted as expected by
  `get_coverage_report`, which then listed them as missing on every car, forever.
  That is exactly the false alarm the self-test exists to prevent.

  The flag is now parsed and carried through the pipeline, and non-streamable
  fields are excluded from the cluster picker, the portal snippet,
  `activate_stream_fields` and the coverage comparison. **Entities are unaffected**
  — several of these fields arrive over REST via a container, so they keep their
  entity and simply are not expected on the stream. `telematics-fields.md` gained
  a **Stream** column so you can see which is which per field.

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
