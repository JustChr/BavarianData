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
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-configure-menu.png" alt="BavarianData options menu listing every action, from Choose streamed data to Debug logging" width="460" />
</p>

The menu labels below are exactly as they appear in the UI.

| Menu entry | Kind | What it does |
| --- | --- | --- |
| **Choose streamed data** | setup | Re-open the [cluster picker](Getting-Started-4-Choose-Data), then activate the fields with the one-click **Activate BMW data** bookmarklet (same activator as guided setup). Additive — it adds the chosen clusters to the live stream but never removes fields you already stream. |
| **Refresh tokens now** | action | Force an OAuth token refresh. |
| **Re-authorize with BMW** | action | Re-run device auth (after BMW invalidates the token). |
| **Reset telemetry container** | action | Clear the stored HV container id/signature so it's rebuilt on the next fetch. Use if telematics fetches start failing after a descriptor change. |
| **Discover vehicles** | action ⚡ | Fetch vehicle mappings. See [Services](Services-Reference). |
| **Fetch basic vehicle info** | action ⚡ | See [Services](Services-Reference). |
| **Fetch telematics data** | action ⚡ | " |
| **Fetch charging history** | action ⚡ | " |
| **Fetch tyre diagnosis** | action ⚡ | " |
| **Fetch charging settings** | action ⚡ | Location-based charging settings. " |
| **Fetch vehicle image** | action ⚡ | " |
| **Charging costs & history** | settings | Price source, retention, statistics — below. |
| **Solar & energy sources** | settings | Where each charge's energy came from: PV, house battery, grid — below. |
| **evcc / wallbox bridge** | settings | Publish the car's live state to MQTT for a charge controller — below. |
| **Trips** | settings | Work zone, default type, commute stop tolerance, address resolution and route recording — below. |
| **Debug logging** | settings | Verbose logging toggle — below. |

⚡ = spends one (or more) of your [50 requests / 24 h](Feature-API-Quota).

## Charging costs & history

Screen: **Configure → Charging costs & history**. See
[Charging history & cost](Feature-Charging-History-and-Cost) for the concepts.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-charging-costs.png" alt="Charging costs & history settings: price source, fixed price, price entity, currency, wallbox energy sensor, charging losses and retention" width="460" />
</p>

| Option | Values | Meaning |
| --- | --- | --- |
| **Price source** | `none` · `fixed` · `entity` | How energy becomes money. `none` creates **no cost entities at all**. |
| **Fixed price per kWh** | number | Price per kWh, when mode is `fixed`. Required in that mode. |
| **Price entity** | sensor / input_number | Live price source (Tibber/Nordpool/aWATTar), when mode is `entity`. Sampled while charging. Required in that mode. |
| **Currency** | text | Currency code for the cost entities. |
| **Wallbox energy sensor** | sensor | Optional. The wallbox's **cumulative** energy total (`total_increasing`, not a per-session counter). Its measured grid figure replaces the battery-side estimate in the session record, the monthly totals and the cost. Only read for charges in your Home zone, and refused when it can't be right — see [the bridge page](Feature-evcc-and-Wallbox-Bridge#when-the-reading-is-refused). |
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

| Option | Values | Meaning |
| --- | --- | --- |
| **Work zone** | zone entity | Drives commute classification (home↔work). |
| **Default type** | Private / Business / Leave unclassified | Default **Private**. What every trip that isn't a recognised home↔work commute is filed as. Always just a starting point: a correction on the card is never overwritten. Pick **Leave unclassified** to triage each trip by hand. |
| **Commute stop tolerance** | 0–180 min | Default **30**. How long the car may stand between two drives for both to still count as one commute — the supermarket on the way to work. **0** switches chaining off. Stops under ~5 min never split a drive at all. |
| **Resolve addresses** | on/off | Off by default. When on, trip endpoints **outside** any zone are reverse-geocoded via OpenStreetMap; the address string is stored, never the coordinates. |
| **Record route** | on/off | Off by default. When on, each new trip stores its GPS track — coordinates along the drive, each stamped with its time (`[lat, lon, t]`, `t` = seconds since start) — so a map can draw and replay the route. The only setting that persists raw coordinates, your exact start/end included. Served via `get_trips`; never in the export. |
| **Trip-capture diagnostics** | on/off | Off by default. A troubleshooting aid for improving trip detection: logs the raw substrate (every GPS fix with cadence/latency, the close-timer lifecycle, full segment batches, a per-message descriptor firehose and a per-trip post-mortem) under `[trip.*]` tags, and writes `bavariandata_trip_capture.ndjson` to your config folder. Independent of **Debug logging**. Verbose and contains GPS/VIN — turn it on for a test drive and back off. See [Troubleshooting](Troubleshooting-and-FAQ#capturing-a-drive-for-trip-detection). |

## Debug logging

Screen: **Configure → Debug logging**.

| Option | Values | Meaning |
| --- | --- | --- |
| **Enable debug logging** | on/off | Off by default. Gates the integration's verbose logging (separate from HA's per-integration log level). **Verbose and can include VIN/GPS** — leave off unless chasing a problem. Applies immediately. |

## Hidden overrides

A few advanced options aren't in the menu and are only set via the hidden
overrides mechanism / imported options. Most users never touch them.

| Option key | Meaning |
| --- | --- |
| `mqtt_keepalive` | MQTT keepalive interval for the stream client. |
| `diagnostic_log_interval` | How often the diagnostic heartbeat is logged. |
