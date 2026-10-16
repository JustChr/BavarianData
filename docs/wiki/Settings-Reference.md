# Settings reference

> 🇩🇪 [Deutsch](DE-Settings-Reference)

All settings live under the integration's **Configure** menu: **Settings →
Devices & Services → BavarianData → Configure**. Every action step and every
option is listed here.

The menu has three kinds of entry: **stream setup**, **settings screens**, and
**one-shot fetch actions** (which spend API quota). Changes to settings screens
apply **immediately** — the integration doesn't reload the entry, because BMW
allows only one concurrent stream per account and a reload risks racing the
reconnect.

## The Configure menu

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-menu.png?v=2026-10-14" alt="BavarianData options menu listing every action, from Choose streamed data to Debug logging" width="460" />
</p>

The menu labels below are exactly as they appear in the UI.

| Menu entry | Kind | What it does |
| --- | --- | --- |
| **Choose streamed data** | setup | Re-open the [cluster picker](Getting-Started-4-Choose-Data), then activate the fields with the one-click **Activate BMW data** bookmarklet (same activator as guided setup). Additive — it adds the chosen clusters to the live stream but never removes fields you already stream. |
| **Refresh tokens now** | action | Force an OAuth token refresh. |
| **Re-authorize with BMW** | action | Re-run device auth (after BMW invalidates the token). |
| **Reset telemetry container** | action | Clear the stored HV container id/signature so it's rebuilt on the next fetch. Use if telematics fetches start failing after a descriptor change. |
| **Discover vehicles** | action ⚡ | Fetch vehicle mappings and add any car not yet known as a device. See [Services](Services-Reference). |
| **Fetch basic vehicle info** | action ⚡ | See [Services](Services-Reference). |
| **Fetch telematics data** | action ⚡ | " |
| **Fetch charging history** | action ⚡ | " |
| **Fetch tire diagnosis** | action ⚡ | " |
| **Fetch charging settings** | action ⚡ | Location-based charging settings. " |
| **Vehicle image** | settings ⚡ | Where the car's picture comes from: BMW CarData, or BMW's online configurator — below. |
| **Automatic data refresh** | settings ⚡ | Catch up with BMW after Home Assistant starts — below. |
| **Charging costs & history** | settings | Price source, retention, statistics — below. |
| **Solar & energy sources** | settings | Where each charge's energy came from: PV, house battery, grid — below. |
| **evcc / wallbox bridge** | settings | Publish the car's live state to MQTT for a charge controller — below. |
| **Trips** | settings | Work zone, default type, commute stop tolerance, address resolution and route recording — below. |
| **Mileage allowance** | settings | A lease or a yearly mileage limit for each car — below. |
| **Debug logging** | settings | Verbose logging toggle — below. |

⚡ = spends one (or more) of your [50 requests / 24 h](Feature-API-Quota).

## Vehicle image

Screen: **Configure → Vehicle image**.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-vehicle-image.png" alt="Vehicle image settings: one dropdown, Picture source, set to BMW configurator (360°, unofficial)" width="460" />
</p>

| Option | Values | Meaning |
| --- | --- | --- |
| **Picture source** | BMW CarData (official) · BMW configurator (360°, unofficial) | Default **BMW CarData**: BMW's one side view of the car, one of your [50 requests a day](Feature-API-Quota) per car. **BMW configurator** draws your exact car the way BMW's online configurator does, from the model, paint and factory options in its basic vehicle info: a 360° turntable in the [card](The-Dashboard-Card#overview) you can drag to turn, and the real paint on the card's [top-down car](The-Dashboard-Card#the-cars-shape). It spends no requests, but it is not part of CarData: it is the service behind BMW's configurator web page, which BMW can change or block at any time. |

**Submit** fetches the picture straight away and keeps it across restarts. With
the configurator it waits only for the 360° view (a few seconds per car); the
rest follows in the background within about a minute: the same view with the
lights on and at night, the interior panorama, the photo from above (MINIs only)
and the names and pictures of the car's factory options for the card's
[equipment view](The-Dashboard-Card#equipment-view-equipment). With several
cars, the setting is saved as long as at least one can be drawn: a car the
configurator can't draw keeps its official picture, and a closing message names
it. If that car has no official picture yet, one is fetched (one request). Home
Assistant's log says why the car couldn't be drawn. If no car can be drawn,
nothing is saved and the screen names the car and why (not a BMW or MINI car,
its basic vehicle info isn't in yet, the configurator didn't answer, or it has
no picture of that model). A car added later is drawn when it first appears,
or gets its official picture if it can't be.

These pictures are fetched only then: when you press **Submit** here, and once
for a car when it is first added. Nothing refreshes them on a schedule or after a
restart or update, so press **Submit** again to fetch them anew (for example after
updating BavarianData, to get pictures a newer version adds).

The configurator draws BMW and MINI cars, including models no longer sold. It
has no overhead view of BMW models, and its interior shows BMW's default trim,
because BMW's vehicle data doesn't say which upholstery a car has. Whenever there is
no configurator picture for a car, the card falls back to the official one.

## Automatic data refresh

Screen: **Configure → Automatic data refresh**.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-data-refresh.png" alt="Automatic data refresh settings: one toggle, Catch up after a restart or an outage, switched on" width="460" />
</p>

The live stream only carries what changes while Home Assistant is listening.
Whatever a car reported while Home Assistant was down, or while a network outage
cut the connection to BMW — a charge that ended, the doors being locked, a drive —
is not replayed on reconnect, and a car charging at steady power can stay silent
for hours afterwards. So the integration asks BMW once per car for its current
state: two minutes after a start, and when the connection comes back after an
outage of five minutes or more. BMW returns the last
value each car sent, so this catches up without waking the car; it cannot produce
a newer reading than the car last sent.

| Option | Values | Meaning |
| --- | --- | --- |
| **Catch up after a restart or an outage** | on/off | Default **on**. One request per car of your [50 a day](Feature-API-Quota). Skipped when BMW was asked within the last hour (the daily refresh or a manual fetch count too), so restarting repeatedly, or a connection that keeps dropping, costs nothing more; a charge that was running at a restart is still checked if the last request is over half an hour old. Always leaves 10 requests for your own service calls. Takes effect immediately. |

A charge running at the restart is settled by this answer: still charging and it
carries on as the same session, stopped and it is recorded as ended — see
[Restarting while the car is charging](Feature-Charging-History-and-Cost#restarting-while-the-car-is-charging).

## Charging costs & history

Screen: **Configure → Charging costs & history**. See
[Charging history & cost](Feature-Charging-History-and-Cost) for the concepts.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs.png?v=2026-10-11b" alt="Charging costs & history settings: price source, fixed price, price entity, currency, wallbox energy sensor, charging losses and retention" width="460" />
</p>

**Several cars on the account?** The screen first asks which vehicle. Each car
keeps its own **Wallbox energy sensor** and **Charging losses**: two cars can
charge at two wallboxes, through two different chargers. The price, currency,
history and statistics settings apply to every car; they sit in the **All
vehicles** section below the car's own. With one car there is no vehicle
question.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs-second-car.png?v=2026-10-11b" alt="Charging costs for a second car: its own wallbox sensor and charging losses on top, the shared price settings in the All vehicles section below" width="460" />
</p>

| Option | Values | Meaning |
| --- | --- | --- |
| **Price source** | `none` · `fixed` · `entity` | How energy becomes money. `none` creates **no cost entities at all**. |
| **Fixed price per kWh** | number | Price per kWh, when mode is `fixed`. Required in that mode. |
| **Price entity** | sensor / input_number | Live price source (Tibber/Nordpool/aWATTar), when mode is `entity`. Sampled while charging. Required in that mode. |
| **Currency** | text | Currency code for the cost entities. |
| **Wallbox energy sensor** | sensor | Optional. The wallbox's **cumulative** energy total (`total_increasing`, not a per-session counter). Its measured grid figure replaces the battery-side estimate in the session record, the monthly totals and the cost. After three home charges it also [drives the state-of-charge estimate](Feature-evcc-and-Wallbox-Bridge#it-also-drives-the-state-of-charge-estimate) while the car charges at home. Only read for charges in your Home zone, and refused when it can't be right — see [the bridge page](Feature-evcc-and-Wallbox-Bridge#when-the-reading-is-refused). |
| **Charging losses (%)** | 0–30 | Grosses the battery figure up by your losses. Default **0** (no invented correction). |
| **Keep history for (months)** | 0–120 | How long to keep recorded sessions/trips. **0 = keep everything.** |
| **Publish to long-term statistics** | on/off | Mirror history into the Energy dashboard. Turning it **off deletes** the published series. See [Energy & statistics](Feature-Energy-and-Statistics). |

## Solar & energy sources

Screen: **Configure → Solar & energy sources**. See
[Charging history & cost → Where the energy came from](Feature-Charging-History-and-Cost)
for what it does with these.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-solar-sources.png" alt="Solar and energy sources settings: PV power, grid power, optional house battery power, a switch for batteries that report positive while charging, and the value of your own solar per kWh" width="460" />
</p>

| Option | Values | Meaning |
| --- | --- | --- |
| **PV power** | sensor (power) | Total generation from your inverter(s). Required for any attribution. |
| **Grid power (+ import / − export)** | sensor (power) | Signed power at the meter: **positive while importing**. Required. |
| **House battery power** | sensor (power) | Optional. Without it you get a two-way PV/grid split. Expected **positive while discharging**. |
| **My battery sensor is positive when charging** | on/off | Flips the sign convention. There is no way to tell from the value itself, so this has to be stated. |
| **Value of own solar per kWh** | number | What a kWh off your own roof is worth — usually your feed-in tariff. **Empty = solar is costed at your import price**, exactly as before, and only the mix is new. |

Both power sensors are needed: with only one, the split would have to assume the
other. Sensors reporting **kW** are converted automatically; one with no unit at
all is read as **watts**.

The pickers list sensors whose **device class is `power`**. If your own template
sensor isn't offered, give it `device_class: power` (and a `unit_of_measurement`
of `W` or `kW`) and it will appear.

## evcc / wallbox bridge

Screen: **Configure → evcc / wallbox bridge**. See
[evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge) for the concepts, the
topic table and the troubleshooting.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-evcc-bridge.png?v=2026-10-11b" alt="evcc / wallbox bridge with one car: publish this car to MQTT, topic prefix and publish retained" width="460" />
</p>

**Several cars on the account?** The screen first asks which vehicle, and
**Publish this car to MQTT** is per car, so a car charged elsewhere can stay
off the broker, VIN included. Switching one car off removes that car's topics.
**Topic prefix** and **Publish retained** apply to every car, in the collapsed
**All vehicles** section.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-evcc-bridge-second-car.png?v=2026-10-11b" alt="evcc / wallbox bridge for a second car: its own publish switch, the shared topic prefix and retained setting in the All vehicles section" width="460" />
</p>

Publishes the car's live state onto your MQTT broker so evcc, openWB or a
Node-RED flow can read a state of charge that costs no
[API quota](Feature-API-Quota). **Requires the MQTT integration** to be set up in
Home Assistant — the bridge publishes through it, so there is no broker host or
password to enter here. Switching it on shows a second screen with the
ready-to-paste evcc configuration.

| Option | Values | Meaning |
| --- | --- | --- |
| **Publish this car to MQTT** | on/off | Default **off**. It puts the VIN and the car's state on a broker other things can read. Switching it back off **removes** the published topics. |
| **Topic prefix** | text | Topic root; everything lands under `<prefix>/<VIN>/`. Default `bavariandata`. Slashes are trimmed and an MQTT wildcard falls back to the default. |
| **Publish retained** | on/off | Default **on**, and best left on: it is what lets evcc find the state of charge the moment it starts rather than waiting for the car to speak again. Turn off only for a broker that refuses retained messages. |

## Trips

Screen: **Configure → Trips**. See [Trips](Feature-Trips) for the concepts.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-trips.png?v=2026-10-11b" alt="Configure, Trips with one car: work zone, default type, commute stop tolerance, trip detection, record route, resolve addresses and trip-capture diagnostics" width="460" />
</p>

**Several cars on the account?** Trips first asks which vehicle, and each car
keeps its own **Work zone**, **Default type**, **Commute stop tolerance**,
**Trip detection** and **Record route**: a company car and a private one, or an
older car next to one that streams while driving. **Resolve addresses** and
**Trip-capture diagnostics** apply to every car; they sit in the collapsed
**All vehicles** section at the bottom of each car's form. A car added later
starts with the settings that were in use before it arrived. With one car
there is no vehicle question.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-vehicle-picker.png?v=2026-10-11b" alt="The vehicle question with two cars: a Vehicle dropdown and a Next button" width="460" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-trips-second-car.png?v=2026-10-11b" alt="Trips settings for a second car on End-of-trip reports, with the shared All vehicles section opened below" width="460" />
</p>

| Option | Values | Meaning |
| --- | --- | --- |
| **Work zone** | zone entity | Drives commute classification (home↔work). |
| **Default type** | Private / Business / Leave unclassified | Default **Private**. What every trip that isn't a recognized home↔work commute is filed as. Always just a starting point: a correction on the card is never overwritten. Pick **Leave unclassified** to triage each trip by hand. |
| **Commute stop tolerance** | 0–180 min | Default **30**. How long the car may stand between two drives for both to still count as one commute — the supermarket on the way to work. **0** switches chaining off. Stops under ~5 min never split a drive at all. |
| **Resolve addresses** | on/off | Off by default. When on, trip endpoints **outside** any zone are reverse-geocoded via OpenStreetMap; the address string is stored, never the coordinates. |
| **Trip detection** | Live / End-of-trip reports | Default **Live**: trips follow the car's position while it drives. **End-of-trip reports** is for cars that send nothing while driving (iDrive 6 and older): one trip per vehicle report, from where the car stood at the previous one, with the live detection off. Start times come from `bavariandata.mark_trip_start`. See [Trips](Feature-Trips#older-cars-that-report-only-at-the-end-of-a-trip). |
| **Record route** | on/off | Off by default. When on, each new trip stores its GPS track — coordinates along the drive, each stamped with its time (`[lat, lon, t]`, `t` = seconds since start) — so a map can draw and replay the route. The only setting that persists raw coordinates, your exact start/end included. Served via `get_trips`; never in the export. |
| **Phone** / **Phone is in the car** | `device_tracker` / binary sensor | Only on the follow-up screen **Route from a phone**, shown for a car on **End-of-trip reports** with **Record route** on. The phone's positions draw the trip's route; distance and times stay the car's. The in-car sensor turning on marks the trip's start. See [Trips](Feature-Trips#the-route-from-a-phone). |
| **Trip-capture diagnostics** | on/off | Off by default. A troubleshooting aid for improving trip detection: logs the raw substrate (every GPS fix with cadence/latency, the close-timer lifecycle, full segment batches, a per-message descriptor firehose and a per-trip post-mortem) under `[trip.*]` tags, and writes `bavariandata_trip_capture.ndjson` to your config folder. Independent of **Debug logging**. Verbose and contains GPS/VIN — turn it on for a test drive and back off. See [Troubleshooting](Troubleshooting-and-FAQ#capturing-a-drive-for-trip-detection). |

## Mileage allowance

Screen: **Configure → Mileage allowance**. See [Mileage & allowance](Feature-Mileage)
for what the figures mean.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-allowance.png" alt="Mileage allowance: None, Lease or financing, or Yearly limit (e.g. insurance), with a Next button" width="460" />
</p>

The first screen asks which kind of limit the car has: **None**, **Lease or
financing** or **Yearly limit (e.g. insurance)**. **Next** opens that kind's
form; **None** removes the car's allowance. The allowance is always the car's
own: with several cars the vehicle question comes first, and a car added later
starts without one. It applies at once, to the card and the sensor.

| Option | Values | Meaning |
| --- | --- | --- |
<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-lease.png" alt="Lease or financing: start date, end date, allowance per year and odometer at the start, filled in for a three-year lease at 15,000 km a year" width="460" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-mileage-yearly.png" alt="Yearly limit: the day the year starts, the allowance per year and the odometer that day" width="460" />
</p>

| **Start date** / **Year starts on** | date | The day the lease began, or the first day of your insurance year (a yearly limit renews on it every year). |
| **End date** | date | Lease only: the day you hand the car back. Must be after the start. |
| **Allowance per year** | km or mi (see below) | From the contract or policy. A lease's total is this times its length. |
| **Odometer at the start** / **Odometer that day** | km or mi | The reading on the start date, from the handover papers. Filled in with today's reading while none is saved; for a yearly limit, the reading at the most recent start of the year. |

The boxes say which unit they take. It is the car's own: the unit its odometer
sensor is shown in (so a UK install, metric with the odometer switched to
miles, types miles), else the car's display setting when BMW streams it, else
Home Assistant's unit system. The card and the service calendar use the same
rule. Earlier versions followed the unit system alone, so on a metric
install an allowance typed in miles was saved as km: if yours looks too small,
enter it once more.

## Debug logging

Screen: **Configure → Debug logging**.

| Option | Values | Meaning |
| --- | --- | --- |
| **Enable debug logging** | on/off | Off by default. Gates the integration's verbose logging (separate from HA's per-integration log level). **Verbose and can include VIN/GPS** — leave off unless chasing a problem. Applies immediately. With more than one BMW account set up, the log level is shared: logging stays verbose while **any** entry has this on, and covers every account ([Multiple cars & accounts](Feature-Multiple-Cars-and-Accounts#telling-the-cars-apart)). |

## Hidden overrides

A few advanced options aren't in the menu and are only set via the hidden
overrides mechanism / imported options. Most users never touch them.

| Option key | Meaning |
| --- | --- |
| `mqtt_keepalive` | MQTT keepalive interval for the stream client. |
| `diagnostic_log_interval` | How often the diagnostic heartbeat is logged. |
