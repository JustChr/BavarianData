<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/logo.png" alt="BavarianData logo" width="240" />
</p>

<h1 align="center">BavarianData: Connect Home Assistant to BMW CarData</h1>

<p align="center">
  Bring your BMW's live data into Home Assistant — straight from BMW CarData,
  no third-party cloud in between.
</p>

---

BMW CarData is BMW's own telematics service: an MQTT stream that pushes vehicle
data in real time and a REST API for on-demand snapshots. This integration talks
to both directly, using your personal BMW client ID. There is no intermediate
server and no MyBMW screen-scraping — Home Assistant is the only client.

Every descriptor BMW sends becomes a native entity. A parked charging session,
a door left open, tyre pressures, the 12 V battery — each surfaces as a sensor or
binary sensor with a proper device class, unit, and (where BMW provides one) a
set of translated states. The integration also ships a Lovelace card and a cached
vehicle image so a usable dashboard exists out of the box.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-car.png" alt="Bundled Lovelace card showing a BMW i5 eDrive40 with charge level, range, charging status and odometer" width="360" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-security.png" alt="Security &amp; closures card with a top-down car diagram, anti-theft alarm armed and all closures closed" width="300" />
  &nbsp;&nbsp;
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Tyre pressure card flagging slightly high pressures on all four tyres on a top-down car diagram" width="300" />
</p>

> **Status — experimental.** This is a spare-time project, verified against a
> limited number of vehicles and Home Assistant versions. Expect rough edges and
> avoid wiring it into safety-critical automations. Track `main`; other branches
> may be broken at any time.

## How it works

- **Streaming** — the integration holds one MQTT connection to BMW's CarData
  broker and refreshes your OAuth tokens on its own. Whatever descriptors you
  enable in the BMW portal arrive as they change; each maps to an entity.
- **REST snapshots** — some data (basic vehicle info, charging history, tyre
  diagnosis, the vehicle image, …) isn't streamed, so the integration fetches it
  on demand or on a schedule.
- **Quota-aware** — BMW caps the REST API at **50 requests per 24 h**. The
  integration tracks and enforces this itself, and every manual service call
  counts against it.

## Requirements

- A BMW account with a vehicle that supports CarData.
- **CarData API** and **CarData Streaming** subscribed in the BMW portal, and a
  **client ID** generated for this integration.
- Home Assistant **2026.3** or newer.

It helps to skim
[BMW's CarData documentation](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction)
once before starting — the portal steps below mirror it.

## Step 1 — Set up BMW CarData in the portal

Do this before adding the integration. The CarData portal isn't offered in every
market, but the client ID it produces is account-wide, so you can complete the
setup from any supported region and use it everywhere.

Open the vehicle overview and pick **CarData**:

|        | English | German | Austrian |
| ------ | ------- | ------ | -------- |
| BMW    | [vehicle overview](https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.de/de-de/mybmw/vehicle-overview) | [Fahrzeugübersicht](https://www.bmw.at/de-at/mybmw/vehicle-overview) |
| Mini   | [vehicle overview](https://www.mini.co.uk/en-gb/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.de/de-de/mymini/vehicle-overview) | [Fahrzeugübersicht](https://www.mini.at/de-at/mymini/vehicle-overview) |

1. Select your vehicle and open **BMW CarData** / **Mini CarData**.
2. [Generate a client ID](https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Technical-registration_Step-1).
3. Give the client both scopes — `cardata:api:read` and `cardata:streaming:read`
   — and authorize it.
   *If the portal throws a scope error, reload, add one scope, wait ~30 s, then
   add the second.*

That's all you need here. **Don't tick anything under Data Selection yet** —
which descriptors to stream is chosen from inside Home Assistant after install
([Step 4](#step-4--choose-which-data-to-stream)), which generates a snippet
tailored to the clusters you pick. The client ID is account-wide, so it covers
every vehicle on the account.

## Step 2 — Install

Via [HACS](https://hacs.xyz/) as a custom repository:

1. HACS → **Custom repositories** → add this repo, category **Integration**.
2. Install **BavarianData: Connect Home Assistant to BMW CarData**.
3. Restart Home Assistant.

## Step 3 — Add the integration

1. **Settings → Devices & Services → Add Integration → BavarianData: Connect Home Assistant to BMW CarData**.
2. The first screen recaps the portal setup and asks for your **client ID**.
3. Home Assistant shows a **link and a code**. Open the link, sign in, and
   approve the device on BMW's site. When BMW accepts the approval, the dialog
   **continues on its own** — nothing to click in HA. If the code times out,
   press **Submit** for a fresh one and try again. BMW's authorization backend is
   sometimes flaky and can report "access denied" even though your login clearly
   worked; if that happens, follow
   [Onboarding fails with "access denied"](#onboarding-access-denied) in
   Troubleshooting — it's a known BMW-side quirk with a reliable workaround.
4. **Choose which data to stream.** The moment authorization succeeds, setup
   moves straight to the cluster picker (see [Step 4](#step-4--choose-which-data-to-stream)) —
   no separate trip to Configure. Until you finish it, no descriptors are
   selected in the portal, so no MQTT data will arrive.

If BMW later invalidates the token, run **Configure → Re-authorize with BMW**.
Removing and re-adding the integration with the same client ID also works — the
previous entry is cleaned up automatically.

## Step 4 — Choose which data to stream

BMW only streams the descriptors you tick under **Data Selection** in the portal,
and it offers **no API** to set that selection — it is portal-only. Rather than
hand-picking hundreds of technical fields, the integration builds the selection
for you. These screens appear automatically at the end of Step 3:

1. **Pick the clusters** you want (Electric vehicle, Vehicle status, Tire data,
   …). The defaults are a sensible starting set; the choice is remembered and the
   picker re-opens pre-filled next time.
2. The next screen shows a **browser-console snippet** generated for exactly
   those clusters. Copy it.
3. In the portal, open **Data Selection** (`Datenauswahl ändern`) and click
   **Load more** until every field is listed. Open the browser console
   (F12 → Console), paste the snippet, and press Enter. It ticks only the
   checkboxes belonging to your chosen clusters — leaving any other selections
   untouched — and logs how many it matched.
4. **Save** the selection in the portal, then press **Submit** in Home Assistant
   to finish. Repeat the portal step for each vehicle.
5. Trigger something in the MyBMW app (lock/unlock) to nudge the car into sending
   its first update.

Re-run this any time from **Configure → Choose streamed data** to widen or narrow
the stream. Requesting per-descriptor streaming *scopes* instead of a portal
selection is rejected by BMW — see
[docs/reference/stream-scope-investigation.md](docs/reference/stream-scope-investigation.md).
The full field-per-cluster breakdown lives in
[docs/reference/telematics-fields.md](docs/reference/telematics-fields.md).

> **Extrapolated state-of-charge helpers** need the **Electric vehicle** cluster
> in the stream — specifically the descriptors
> `vehicle.drivetrain.batteryManagement.header`,
> `vehicle.drivetrain.batteryManagement.maxEnergy`,
> `vehicle.powertrain.electric.battery.charging.power`, and
> `vehicle.drivetrain.electricEngine.charging.status`.

## Entities

- Each VIN becomes its own device.
- Streamed descriptors become sensors and binary sensors, named from a curated
  English title set ([`tools/curated_titles.json`](tools/curated_titles.json),
  baked into the catalogue and the HA translations). German names ship too;
  open an issue or PR if a name looks off.
- Numeric fields get sensible device classes; distances use
  `device_class: distance`, and odometer/mileage uses
  `state_class: total_increasing` so long-term statistics work.
- Every entity exposes its source timestamp plus its catalogue `cluster` and
  `category` as attributes — the Lovelace card uses these to group values
  regardless of the user's HA language.
- Each VIN also gets an **image** entity holding BMW's rendered picture of the
  car. It's cached and survives restarts, so it doesn't burn quota on every
  boot; refresh it manually with `bavariandata.fetch_vehicle_image`.

## Lovelace card

A custom **BMW CarData Card** is bundled and registered automatically — no
dashboard resource to add by hand. Pick it from the card gallery to open a visual
editor, or write YAML directly.

**Overview** — the vehicle render, a state-of-charge ring (blue while charging),
remaining range, charging status, and a grid of key metrics. With the integration
installed, the minimal config auto-discovers the car:

```yaml
type: custom:bmw-cardata-card
```

Pin a specific vehicle with `device:` (device id) or `vin:`. Optional entity
overrides: `title`, `image`, `soc`, `range`, `charging`, `target_soc`,
`time_to_full`, `odometer`, `plug`. Set `view: charging` for the charging-history
layout, `view: trips` for the trip journal, or `view: health` for battery health
(all below), or `cluster:` for a single-cluster list.

**Single cluster** — set `cluster:` to list every value in one catalogue cluster.
Use one card per cluster:

```yaml
type: custom:bmw-cardata-card
cluster: electric   # electric · status · tire · usage · events · basic · contract · metadata · other
```

**Charging history** — `view: charging` lists recorded charging sessions, newest
first, each showing the date, energy, cost and a Home/Away badge. Tap a session
to expand its **power curve**, peak and average power, duration, and grid energy.
The "this month" totals ride along the top. It reads the integration's stored
history via the `get_charging_sessions` service, so it spends no API quota:

```yaml
type: custom:bmw-cardata-card
view: charging
```

Cost only appears once a price source is set under **Configure → Charging costs
& history**; until then sessions still list with their energy. A session charged
without GPS is badged *Home · assumed*, and one priced while the tariff was
briefly unknown is tagged *partial price*.

**Battery health** — `view: health` shows the learned usable battery capacity as
a gauge (percentage of the as-new pack) with a capacity-vs-mileage trend below.
It reads the **Battery Health** sensor, so it spends no API quota. Until there
are enough wide-range charges to be sure of the number, it shows *Learning
(n/10)* rather than a figure that would jump around:

```yaml
type: custom:bmw-cardata-card
view: health
```

**Trips (driving journal)** — `view: trips` lists your recorded drives, newest
first, each showing *from → to*, distance, duration and a business/private/commute
badge; tap one for consumption, recuperation and the SoC used, and to reclassify
it. Above the list a **month in review** sums the distance (with a vs-last-month
delta), the business/private/commute split, average consumption, energy
recuperated, a driving-style score and your top destinations — and, once a tariff
is set, an estimated driving cost. It reads the `get_trips` and
`get_driving_summary` services, so it spends no API quota:

```yaml
type: custom:bmw-cardata-card
view: trips
```

Trips are reconstructed from the stream — no configuration needed. Endpoints are
stored as **place names**, never coordinates: a point inside a Home Assistant zone
shows that zone's name, and (only if you enable it under **Configure → Trips**) a
point outside any zone is looked up via OpenStreetMap and stored as an address.
Set a **work zone** there so home↔work drives are recognised as commutes. This is
a trip journal and expense helper — **not a tax-office-compliant logbook**
(*kein Finanzamt-konformes Fahrtenbuch*): it has no legal tamper-resistance.

**Tire pressures** — `cluster: tire` draws a top-down car with each tire coloured
by pressure vs. its target (green OK, amber high, red low) and the readings beside
each wheel:

```yaml
type: custom:bmw-cardata-card
cluster: tire
```

**Security & closures** — `cluster: closures` shows doors, windows, hood, trunk,
sunroof, the central lock and the anti-theft alarm on the same car. Open doors
highlight red, open windows/sunroof amber, and a central padlock reflects the lock
state; a badge summarises the worst-case status and every part taps through to the
underlying entity. Parts the vehicle doesn't report are simply omitted:

```yaml
type: custom:bmw-cardata-card
cluster: closures
```

If the card doesn't show up after an update, hard-refresh the browser.

## Smart charging & automations

Because BavarianData streams data in real time, it's built for automations that
react the instant something changes rather than on a polling cycle.

**Charged-energy sensors** — the integration integrates the live charging power
over time into two sensors per vehicle:

- **Charged Energy (Total)** — a monotonic `kWh` counter with
  `device_class: energy` / `state_class: total_increasing`. Add it to Home
  Assistant's **Energy dashboard** as an "individual device" to see (and cost)
  your car's charging alongside the rest of the house.
- **Charged Energy (Session)** — resets at the start of every charging session,
  so you can see how much went in this plug-in.

Both are derived on the integration side (BMW never sends a kWh counter), so they
work on any vehicle that streams charging power.

**Charging history & cost** — every completed session is recorded and kept, in
the integration's own store rather than the recorder (which purges after ten
days). No REST quota is spent: it all comes from the stream.

Set this up under **Configure → Charging costs & history**. Pick a **fixed price
per kWh**, or point it at a **live price entity** (Tibber, Nordpool, aWATTar, …)
— that one is sampled *while the car charges*, so a session spanning a price
change is billed correctly rather than at whatever the price happened to be at
the end. Until you choose a price source, **no cost entities are created at
all**; a wrong number would be worse than none.

You then get per vehicle:

- **Charging Energy (This Month)** — always available.
- **Charging Cost (This Month)** and **Charging Cost (Last Session)** — once a
  price source is set.
- **Charging Cost per 100 km** — additionally needs the odometer (Vehicle status
  cluster) and two sessions to measure a distance between.

`bavariandata.get_charging_sessions` returns the full history as service
**response data** (optional `vin`, `from`, `to`, `limit`) — start/end SoC, energy,
duration, peak power, the charging power curve, and cost. It reads the local
store, so unlike the `fetch_*` services it spends **no** API quota.

Energy is measured at the battery, so it's slightly below what the grid
delivered. If you have a wallbox energy sensor, select it and that exact figure
is used instead; otherwise you can gross the value up by your charging losses,
which stays at 0 % by default because an invented correction would look like a
measurement. A session charged while the price was briefly unknown is flagged
`partial` rather than silently understated.

**Battery health** — from those same recorded sessions the integration learns the
battery's usable capacity: a wide-range charge (say 20 → 80 %) that adds a known
number of kWh implies the whole-pack capacity, and averaging across many such
charges cancels the noise. This surfaces as a **Battery Health** sensor per EV
(state in kWh, with vs-new percentage, sample count and a capacity-vs-mileage
trend as attributes). It reads `Learning (n/10)` until it has enough good samples
**and** the estimate agrees with BMW's own capacity figure — a suspicious number
is withheld rather than shown. No REST quota: it's all derived from the stream.

**Trips (driving journal)** — every drive is reconstructed from the stream and
kept: distance, duration, start/end places, SoC used, and BMW's own per-trip
consumption, recuperation and driving-style figures. One **Driving Distance (This
Month)** sensor per vehicle carries the monthly total and the
business/private/commute split; the detail lives in the `get_trips` and
`get_driving_summary` services (and the `view: trips` card) rather than a flood of
entities. Drives between your home and work zones auto-classify as commutes, and
`bavariandata.set_trip_class` (or the card) corrects any guess. Privacy is the
default: places are stored as zone names, and reverse geocoding is opt-in and
never persists coordinates. No REST quota. **Not a tax-office-compliant logbook.**

**Energy dashboard & long-term statistics** — the recorded history is published
into Home Assistant's own long-term statistics, under this integration's
`bavariandata:` namespace: charging energy (kWh), charging cost and driving
distance, one series per vehicle. Because they're written as *external*
statistics, they can cover hours no entity was alive for — so charging from
**before you installed this**, or from **while Home Assistant was down**, still
lands on the Energy dashboard. Add one under **Settings → Dashboards → Energy →
Individual devices**.

The statistics mirror the store rather than being a second archive: each rebuild
regenerates the whole series from the records still held, so your retention
setting genuinely governs everything kept. It happens automatically as sessions
and trips are recorded; `bavariandata.import_statistics` forces a rebuild (useful
after restoring a backup) and reports the row counts. Turn the whole thing off
under **Configure → Charging costs & history**, which also deletes the published
series.

**Export** — the Charging and Trips card views each carry **CSV** and **Report**
buttons that download the current month straight from the browser:

- **CSV** — one row per session or trip, with both the battery-side and measured
  grid energy kept in separate columns, the cost and where it came from, and (for
  trips) the endpoints as place names. Opens cleanly in Excel and Numbers.
- **Report** — a self-contained HTML page: month totals, the charging table and
  the trip journal. Print it (Ctrl/⌘+P → *Save as PDF*) for a tidy A4 document.
  It's a trip journal and expense helper, **not** a tax-office-compliant logbook,
  and says so on the page.

Both come from `bavariandata.export_history` (`month`, `type`, `format`,
`language`), which returns the file contents as response data — nothing is
written to disk, and it spends no API quota. Automations can use it too.

**Events** — meaningful charging transitions fire on the Home Assistant event
bus, ready to use as automation triggers (Developer Tools → Events to watch them):

| Event | Fires when | Data |
| --- | --- | --- |
| `bavariandata_charging_started` | a session begins | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | a session ends (any reason) | `vin`, `soc`, `target_soc`, `status` |
| `bavariandata_charging_complete` | a session ends at/above the target SoC | `vin`, `soc`, `target_soc`, `status` |

**Blueprints** — two starter automations ship in
[`blueprints/`](blueprints/automation/bavariandata). Import them via **Settings →
Automations → Blueprints → Import Blueprint** with the raw GitHub URL:

- **Stop charging at target %** — switches off a wallbox / smart-plug the moment a
  SoC sensor reaches your target (CarData is read-only, so it drives an external
  switch you already have).
- **Notify when charging completes** — pings a notify service on the
  charging-complete / -stopped events above.

**API quota sensor** — an **API Quota Remaining** diagnostic sensor exposes how
many of the 50 requests/24 h are left (with `used`, `limit` and `next_reset`
attributes). If the quota is ever exhausted, a repair issue appears under
**Settings → Repairs** telling you when it resets; streaming data keeps flowing
throughout.

## Services

Each service is available in Developer Tools and as a button in the integration's
**Configure** menu. **Every call spends one of your 50 requests / 24 h.**

| Service | What it fetches |
| --- | --- |
| `bavariandata.fetch_telematic_data` | Current contents of a VIN's telematics container. |
| `bavariandata.fetch_vehicle_mappings` | Vehicles linked to the account and their PRIMARY/SECONDARY status. |
| `bavariandata.fetch_basic_data` | Static vehicle metadata (model, series, …). |
| `bavariandata.fetch_charging_history` | Charging sessions (paginated; optional `from` / `to`). |
| `bavariandata.fetch_tyre_diagnosis` | Smart-maintenance tyre diagnosis. |
| `bavariandata.fetch_location_charging_settings` | Location-based charging settings (paginated). |
| `bavariandata.fetch_vehicle_image` | Vehicle render (updates the image entity). |

These read (or write) the integration's own recorded history instead, and cost
**no** quota:

| Service | What it does |
| --- | --- |
| `bavariandata.get_charging_sessions` | Recorded charging sessions as response data. |
| `bavariandata.get_trips` | Recorded trips as response data. |
| `bavariandata.get_driving_summary` | The month-in-review aggregation for trips. |
| `bavariandata.set_trip_class` | Corrects a trip's business/private/commute class. |
| `bavariandata.export_history` | Returns a month as CSV or a printable HTML report. |
| `bavariandata.import_statistics` | Rebuilds the long-term statistics from the store. |

## Troubleshooting

<a id="onboarding-access-denied"></a>

- **Onboarding fails with "access denied" / "declined" even though BMW confirmed
  your login?** This is flakiness in BMW's device-authorization backend, not the
  integration — the flow can return `access_denied` ("The user has declined
  authorization") even though you never saw a consent page and the login clearly
  worked. BMW support has confirmed (in response to a support ticket) that the
  device-code flow is handled by an internal partner system that is currently
  having "sync problems" on their end — there's nothing this integration can do
  to fix it. It can take a few attempts; this sequence has worked reliably
  (multiple times) for others:
  1. Open a **fresh incognito/private browser window**.
  2. Go to the My BMW / CarData portal **manually** — do **not** use the
     pre-filled complete link, and strip any `?user_code=…` from the URL.
  3. Sign in, open **Authenticate device** ("Gerät authentifizieren"), then
     **type the user code by hand** (make sure no "incorrect code" banner
     appears) and approve. You'll typically land on a "Login successful" /
     "continue in the car" screen — **you don't need to do anything in the
     car**; that screen is just confirmation. Go straight back to Home
     Assistant and press **Submit**.
  4. Back in Home Assistant, if the code timed out meanwhile, press **Submit**
     for a fresh code and repeat.
  5. Still failing after several tries? Delete the client in the BMW portal,
     create a new one (tick **both** subscriptions), and redo auth with the new
     client ID.

  If none of that helps, **stop retrying and wait** — this is genuinely a
  lottery on BMW's side. Many users only got through after leaving it alone
  for several hours to a couple of days (sometimes it starts working
  overnight with no further action), and repeated rapid retries don't seem to
  speed it up. There's no single trick that works for everyone — the
  incognito/manual-code ritual above unblocks most people but not all; a
  smaller group stays stuck regardless of client recreation, waiting, or
  switching browsers/networks. For those cases the only lever left is BMW
  support: bmwcardata-b2c-support@bmwgroup.com (include your reference ID
  from the error, if BMW showed one).
- **Debug logging** is off by default. Turn it on in **Configure → options**
  (`debug_log`) and reload. It's verbose and can include vehicle data such as GPS
  and VIN, so leave it off unless you're chasing a problem.
- **No data arriving?** Make sure you completed
  [Step 4](#step-4--choose-which-data-to-stream) — descriptors must be ticked and
  **saved** in the portal's Data Selection — then trigger a lock/unlock in the
  MyBMW app to prompt an update.
- **Only one stream per account (GCID)** — BMW allows a single concurrent
  streaming client, so no other tool can be connected at the same time.
- **Read-only** — CarData cannot send commands, so this integration can't lock,
  precondition, or otherwise control the car.

## Contributing & support

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).

The descriptor catalogue, metadata, translations, and reference docs are all
generated from BMW's exports by the pipeline in [`tools/`](tools/) — see
[tools/README.md](tools/README.md) before hand-editing any generated file.

## Credits & license

Released under the [MIT License](LICENSE). This integration began as a
continuation of the public-domain
[`bmw-cardata-ha`](https://github.com/JjyKsi/bmw-cardata-ha) by **JjyKsi**;
that project carried no licensing restrictions, and the original author is
credited in [`NOTICE`](NOTICE) out of respect for their work.

"BMW", "Mini", "Rolls-Royce", and "CarData" are trademarks of their respective
owners. This is an independent, community-built integration and is **not**
affiliated with, endorsed by, or sponsored by BMW Group. Use at your own risk;
see the warranty disclaimer in the [LICENSE](LICENSE).
