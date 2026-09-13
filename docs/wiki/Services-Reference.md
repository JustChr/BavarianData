# Services reference

Every service is available in **Developer Tools → Actions** and (for most) as a
button in the integration's **Configure** menu.

All services accept an optional **`entry_id`** (required only if you have
multiple config entries) and, where relevant, an optional **`vin`** (defaults to
the first known vehicle).

## Fetch services — spend API quota ⚡

Each of these spends **one or more** of your
[50 requests / 24 h](Feature-API-Quota). The first two run automatically once a
day as the [daily refresh](Feature-API-Quota#the-daily-refresh); calling them by
hand just fetches early and spends an extra request.

| Service | What it fetches |
| --- | --- |
| `bavariandata.fetch_telematic_data` | Current contents of a VIN's telematics container — every field BMW cannot stream, in one request. |
| `bavariandata.fetch_vehicle_mappings` | Vehicles linked to the account and their PRIMARY/SECONDARY status. |
| `bavariandata.fetch_basic_data` | Static vehicle metadata (model, series, …). |
| `bavariandata.fetch_charging_history` | BMW's charging sessions (paginated; optional `from`/`to`), imported into local history and enriched with measured grid energy. |
| `bavariandata.fetch_tyre_diagnosis` | Smart-maintenance tyre diagnosis — tread wear, remaining mileage, defect status per wheel. Populates the tyre sensors and the card's wheel diagram. |
| `bavariandata.fetch_location_charging_settings` | Location-based charging settings (paginated). |
| `bavariandata.fetch_vehicle_image` | Vehicle render (updates the cached image entity). |

## Local services — no quota

These read (or write) the integration's own store and cost **no** quota.

| Service | What it does |
| --- | --- |
| `bavariandata.get_charging_sessions` | Recorded charging sessions as response data. |
| `bavariandata.get_trips` | Recorded trips as response data (endpoints as place names), plus any drive still under way. |
| `bavariandata.get_driving_summary` | The month-in-review aggregation for trips. |
| `bavariandata.get_efficiency` | Measured consumption, the real range it implies, the charging loss and the monthly trend — see below. |
| `bavariandata.get_evcc_config` | The evcc `custom` vehicle configuration for a car, plus the bridge's MQTT topics — see below. |
| `bavariandata.set_trip_class` | Corrects a trip's business/private/commute class. |
| `bavariandata.export_history` | Returns a month as CSV or a printable HTML report. |
| `bavariandata.get_coverage_report` | Descriptor-coverage self-test — see below. |
| `bavariandata.import_statistics` | Rebuilds the long-term statistics from the store. |
| `bavariandata.activate_stream_fields` | Replaces which attributes BMW streams, by replaying the portal's stream-setup request — see below. |

## Field details

### `get_charging_sessions` / `get_trips`
`vin`, `from`, `to`, `limit`. Returns records newest-first as **response data**
(tick *Return response* in Developer Tools).

A bare date names the **whole day**, at whichever end it is used: `from:
2026-08-01` starts at midnight and `to: 2026-08-31` runs to the end of the 31st,
so asking for a month doesn't silently drop its last day. Pass an explicit
date/time instead and it is used exactly as given.

`get_trips` also returns **`open_trips`** — a drive still under way, which is not
in the journal yet: no end, no classification, a provisional distance and the
route so far when route recording is on. It rides along whenever the window you
asked for **contains the present moment** (including no window at all): a request
for last March plainly doesn't mean the drive happening now, but one for this
month just as plainly does. See
[Trips](Feature-Trips#seeing-the-drive-thats-happening-now).

### `get_driving_summary`
`vin`, `month` (`YYYY-MM`, defaults to current). Returns distance, the
business/private/commute split, consumption, recuperation, driving-style score,
top destinations, and (with a tariff) estimated driving cost.

Consumption comes back as two independent figures, either of which may be absent
when its inputs don't support one:

- **`energy_balance`** — an object with `kwh_per_100km` plus the window and the
  inputs it came from. Its **`source`** says which side of the charger it
  describes: `"grid"` only when every contributing session carried a measured
  `grid_kwh`, otherwise `"battery"` (BMW streams *battery* charging power, so an
  estimated session never measured the wall). Read `source` before labelling the
  number — the two differ by the charging losses.
- **`avg_consumption_kwh_per_100km`** — battery-side, from the trips.

Recuperation is `recuperation_kwh_per_100km`, a distance-weighted mean rather
than a total. See
[how consumption is measured](Feature-Trips#how-consumption-is-measured).

### `get_efficiency`
`vin`. Returns the measured-efficiency profile: consumption, the real range it
implies, the charging loss, and a month-by-month trend. Reads the local store,
so it costs no BMW API quota.

- **`consumption`** — battery-side `kwh_per_100km` plus `window_days` (30, 90,
  365, or `null` when it took the whole ledger) and the odometer window it was
  measured over. **`grid_consumption`** is the same figure at the plug, present
  only when every charge in that same window carried a measured `grid_kwh`.
- **`measured_loss_percent`** — the gap between the two, when both exist. This is
  *measured*, and is a different thing from the
  [charging loss % setting](Settings-Reference#charging-costs--history), which is
  an assumption used for costing.
- **`range`** — `full_km`, `now_km` (scaled by the current charge), `bmw_km`
  (the car's own prediction) and `vs_bmw_percent`. Absent when either the
  capacity or the consumption is unknown.
- **`capacity_kwh` / `capacity_source`** — `measured` once battery health is
  confident, otherwise `bmw`.
- **`trend`** — one entry per calendar month that could be measured, oldest
  first; months whose charging couldn't bracket enough distance are omitted
  rather than shown as zero.
- **`cost_per_100km` / `currency` / `energy_mix`** — this month's running cost
  and where its energy came from.

`status` says why a figure is missing: `ok`, `not_enough_history`, or
`no_capacity`. See
[Efficiency & real range](Feature-Efficiency-and-Range).

### `get_evcc_config`
`vin`. Returns the evcc configuration for one car, ready to paste into
`evcc.yaml`, plus everything needed to debug the bridge. Reads local state only,
so it costs no BMW API quota.

- **`yaml`** — the `vehicles:` block, with your VIN, your topic prefix and the
  car's pack size already filled in. Only the fields the car actually reports
  are referenced, and **no `timeout`** is set (the bridge republishes on a
  heartbeat instead).
- **`enabled`** — whether the bridge is switched on. The YAML is generated
  either way, but nothing is published while this is `false`.
- **`mqtt_available`** — whether Home Assistant has a loaded MQTT integration to
  publish through. `false` here is the first thing to check when nothing appears
  on the broker.
- **`topic_prefix`**, **`topics`** — the prefix in force and every topic the
  bridge owns.
- **`published_topics`** — the subset currently being published, i.e. what this
  car actually reports. A `status` missing from this list means the car reports
  no plug state — see the bridge page's troubleshooting.

See [evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge).

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

Fields BMW marks as not streaming-capable are excluded from the comparison — they
can only arrive over REST, so counting them would report a permanent gap that no
setting can close. See
[Choose your data](Getting-Started-4-Choose-Data#some-fields-never-arrive-on-the-stream).

### `import_statistics`
`vin`. Rebuilds this integration's long-term statistics from the recorded
history, so charging/driving from before the install (or from while HA was down)
appears on the Energy dashboard. Runs automatically as records are made; use it
after restoring a backup or to verify the row counts. See
[Energy & statistics](Feature-Energy-and-Statistics).

### `activate_stream_fields`
Replaces a vehicle's streamed-attribute selection by sending the **same request
the BMW portal sends** when you save *Datenauswahl ändern* — so you can activate
all your fields in one call instead of ticking checkboxes. It is a **replace**:
the attribute list you pass becomes the whole selection.

Stream selection has **no CarData API**; it lives behind the market portal, which
authenticates with your **browser session**. This service therefore needs a
**captured portal session**, and — because that session (including BMW's
bot-defense cookies) is short-lived and cannot be refreshed automatically — it is
a **manual, occasional** tool, not something that runs unattended. It spends no
API quota.

**Getting the four required values** — open your vehicle's **stream-setup** page
in a browser, open DevTools → **Network**, save any change, and inspect the
`POST …/utilities/bmw/api/cd/streams/…` request:

| Field | Where it comes from | Example |
| --- | --- | --- |
| `base_url` | the request origin | `https://www.bmw.at` |
| `locale` | first path segment | `de-at` |
| `mapped_vehicle_id` | the id in the URL (a hash, **not** the VIN) | `90d3dd3e0ba0ea99…` |
| `cookie` | the request's **Cookie** header (a secret — never logged) | `gcdmToken=…; ak_bmsc=…` |

**Choosing the attributes** — pass an explicit `attributes` list, or `sections`
(cluster slugs like `electric`, `status`, `tire`). If you pass neither, it uses
your saved **Choose streamed data** clusters, or the default cluster set. The
list is de-duplicated and sorted, and an unchanged selection is detected and
skipped. Returns `{requested, accepted, unchanged}` as response data.

If the call reports the session was **rejected** (auth), the captured cookie has
expired — grab a fresh one and retry. If it **times out**, BMW's bot-defense is
throttling automated calls; wait a bit and retry with a **freshly captured**
session (this is why it's a one-shot manual tool, not something to loop). See
[Choose your data](Getting-Started-4-Choose-Data) for the checkbox/snippet
alternative.

### `fetch_charging_history`
`vin`, `from` (defaults to 30 days ago), `to` (defaults to now). Imports BMW's
sessions into local history; overlapping live-recorded sessions are enriched in
place with BMW's measured grid energy.
