# Services reference

Every service is available in **Developer Tools → Actions** and (for most) as a
button in the integration's **Configure** menu.

All services accept an optional **`entry_id`** (required only if you have
multiple config entries) and, where relevant, an optional **`vin`** (defaults to
the first known vehicle).

## Fetch services — spend API quota ⚡

Each of these spends **one or more** of your
[50 requests / 24 h](Feature-API-Quota).

| Service | What it fetches |
| --- | --- |
| `bavariandata.fetch_telematic_data` | Current contents of a VIN's telematics container. |
| `bavariandata.fetch_vehicle_mappings` | Vehicles linked to the account and their PRIMARY/SECONDARY status. |
| `bavariandata.fetch_basic_data` | Static vehicle metadata (model, series, …). |
| `bavariandata.fetch_charging_history` | BMW's charging sessions (paginated; optional `from`/`to`), imported into local history and enriched with measured grid energy. |
| `bavariandata.fetch_tyre_diagnosis` | Smart-maintenance tyre diagnosis. |
| `bavariandata.fetch_location_charging_settings` | Location-based charging settings (paginated). |
| `bavariandata.fetch_vehicle_image` | Vehicle render (updates the cached image entity). |

## Local services — no quota

These read (or write) the integration's own store and cost **no** quota.

| Service | What it does |
| --- | --- |
| `bavariandata.get_charging_sessions` | Recorded charging sessions as response data. |
| `bavariandata.get_trips` | Recorded trips as response data (endpoints as place names). |
| `bavariandata.get_driving_summary` | The month-in-review aggregation for trips. |
| `bavariandata.set_trip_class` | Corrects a trip's business/private/commute class. |
| `bavariandata.export_history` | Returns a month as CSV or a printable HTML report. |
| `bavariandata.get_coverage_report` | Descriptor-coverage self-test — see below. |
| `bavariandata.import_statistics` | Rebuilds the long-term statistics from the store. |

## Field details

### `get_charging_sessions` / `get_trips`
`vin`, `from`, `to`, `limit`. Returns records newest-first as **response data**
(tick *Return response* in Developer Tools).

### `get_driving_summary`
`vin`, `month` (`YYYY-MM`, defaults to current). Returns distance, the
business/private/commute split, consumption, recuperation, driving-style score,
top destinations, and (with a tariff) estimated driving cost.

### `set_trip_class`
`vin`, `trip_id` (as returned by `get_trips`), `classification`
(`business` · `private` · `commute`). Writes the local store only.

### `export_history`
`vin`, `month` (`YYYY-MM`), `type` (`charging` · `trips` · `both`), `format`
(`csv` · `html`), `language` (`en` · `de`, HTML report only). Returns the file
contents as response data; nothing is written to disk. See
[Export](Feature-Export).

### `get_coverage_report`
`vin`. For each vehicle, compares the descriptors your selected stream clusters
*should* deliver against those that have actually arrived, and lists any missing
ones. Answers *"I enabled a cluster but no entities appeared — is it my
selection, my car, or a bug?"* Reads the local store and live stream only.

### `import_statistics`
`vin`. Rebuilds this integration's long-term statistics from the recorded
history, so charging/driving from before the install (or from while HA was down)
appears on the Energy dashboard. Runs automatically as records are made; use it
after restoring a backup or to verify the row counts. See
[Energy & statistics](Feature-Energy-and-Statistics).

### `fetch_charging_history`
`vin`, `from` (defaults to 30 days ago), `to` (defaults to now). Imports BMW's
sessions into local history; overlapping live-recorded sessions are enriched in
place with BMW's measured grid energy.
