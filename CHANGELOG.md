# Changelog

All notable changes to BavarianData are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A curated changelog is kept from v0.9.0-beta.6 onward; earlier releases used
auto-generated notes.

## [Unreleased]

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
