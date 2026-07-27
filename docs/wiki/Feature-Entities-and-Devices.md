# Entities & devices

## One device per VIN

Each VIN on your account becomes its own Home Assistant **device**. A separate
integration-level **"CarData Debug Device"** carries the diagnostics entities
(stream connection, quota, last-message timestamps).

## Descriptor entities

Every descriptor BMW streams becomes a native entity:

- Streamed descriptors become **sensors** and **binary sensors**, named from a
  curated English title set (baked into the catalogue and the HA translations).
  German names ship too — open an issue or PR if a name looks off.
- Numeric fields get sensible **device classes**; distances use
  `device_class: distance`, and odometer/mileage uses
  `state_class: total_increasing` so long-term statistics work.
- Every entity exposes its **source timestamp** plus its catalogue `cluster` and
  `category` as attributes — the [dashboard card](The-Dashboard-Card) uses these
  to group values regardless of the user's HA language.

The full field-per-cluster catalogue lives in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md).

## Derived & diagnostic entities

These aren't sent by BMW — the integration derives them. They are named from the
integration's own translations (so German installs get German names too).

| Entity | What it is |
| --- | --- |
| **Charged Energy (Total)** | Monotonic kWh counter (`device_class: energy`, `state_class: total_increasing`). Add to the Energy dashboard. |
| **Charged Energy (Session)** | Resets at the start of each charging session. |
| **Charging Energy (This Month)** | Monthly charged-energy total. |
| **Charging Cost (This Month)** | Monthly cost — only once a price source is set. |
| **Charging Cost (Last Session)** | Cost of the most recent session — only with a price source. |
| **Charging Cost per 100 km** | Needs the odometer and two sessions to measure a distance. |
| **Battery Health** | Learned usable capacity (kWh), with vs-new %, sample count and a capacity-vs-mileage trend. |
| **Driving Distance (This Month)** | Monthly distance + business/private/commute split. |
| **Tyre Condition** | BMW's overall verdict on the mounted set, plus any upstream errors. |
| **Tyre Front Left / Front Right / Rear Left / Rear Right** | Per-wheel wear traffic light (`green`/`yellow`/`red`/`grey`), with the mileage until a change is due, defect status, season, dimension, tread pattern and fitting date as attributes. |
| **API Quota Remaining** | Diagnostic: requests left in the 50/24 h window. |
| **State-of-charge estimate / rate** | Extrapolated SoC helpers (need the Electric vehicle cluster). |
| **Stream Connection Status** | Diagnostic: MQTT connection state. |
| **Last Message Received** | Diagnostic: timestamp of the last stream payload. |
| **Last Telematics API Call** | Diagnostic: timestamp of the last REST call. |

The tyre entities only exist for wheels BMW actually reports. Many cars have no
tyre service record on file, in which case none are created — that is BMW having
no data, not a fault. They are populated by the
[daily refresh](Feature-API-Quota#the-daily-refresh) and by
`bavariandata.fetch_tyre_diagnosis`.

## Vehicle image

Each VIN also gets an **image** entity holding BMW's rendered picture of the car.
It is **cached and survives restarts**, so it doesn't burn quota on every boot;
refresh it manually with `bavariandata.fetch_vehicle_image`.

## Device tracker

Each VIN gets a **device_tracker** ("car") carrying the vehicle's location from
the GPS stream, usable on the HA map and in zone-based automations.

## Why some entities are "unavailable"

Entities keep exposing their `cluster`/`category` attributes even when restored
or unavailable, because the card's cluster views depend on them. An entity may
read unavailable until the car next streams that descriptor — trigger a
lock/unlock in the MyBMW app to prompt an update.
