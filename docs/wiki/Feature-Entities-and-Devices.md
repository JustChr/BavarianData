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
- A few descriptors are **lists** rather than single values — **Condition Based
  Service** (each service item with its due date) and **Check Control messages**
  (the warnings the car raised). Their state is the **number of entries** and the
  full list is in the **`items`** attribute, e.g.
  `{{ state_attr('sensor.<car>_check_control_messages', 'items') }}` in a
  template. A state of `0` means the car reported an empty list.

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
| **Real Range** | How far the car really goes from its current charge (km), from measured consumption and usable capacity — with the car's own prediction and the difference as attributes. |
| **Driving Distance (This Month)** | Monthly distance + business/private/commute split. |
| **Trip in Progress** | Binary sensor: `on` while a drive is under way, with the trip so far as attributes. Deliberately *not* a "moving" sensor — it lingers after an arrival; see [Trips](Feature-Trips#seeing-the-drive-thats-happening-now). |
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

Because that data costs a request and is refreshed at most once a day, it is
**stored and restored across restarts** — the sensors come back showing the last
reading rather than `unknown`. Each carries a `fetched_at` attribute with the
time of that fetch, so a day-old reading is recognisable as one.

## Vehicle image

Each VIN also gets an **image** entity holding BMW's rendered picture of the car.
It is **cached and survives restarts**, so it doesn't burn quota on every boot;
refresh it manually with `bavariandata.fetch_vehicle_image`.

## Device tracker

Each VIN gets a **device_tracker** ("car") carrying the vehicle's location from
the GPS stream, usable on the HA map and in zone-based automations.

## Which lock entity to use

The car reports its central lock through **two** descriptors, and they behave
very differently:

| Entity | Descriptor | Updates |
| --- | --- | --- |
| **Doors overall state** | `vehicle.cabin.door.status` | **On the stream** — follows every lock/unlock within seconds |
| **Doors lock** | `vehicle.cabin.door.lock.status` | **REST only** — BMW does not stream it, so it refreshes at most once per [daily refresh](Feature-API-Quota#the-daily-refresh) and can sit on a stale value for days |

Despite its name, **Doors overall state is the one to automate on.** Both carry
the same values — `Secured`, `Locked`, `Partially locked`, `Unlocked` — and both
appear in the automation editor's state picker, so a condition can be selected
from the dropdown rather than typed by hand. The
[dashboard card](The-Dashboard-Card) prefers the streamed one for its central-lock
tile too, and falls back to `Doors lock` on cars that never stream it.

"Partially locked" (BMW's `SELECTIVE-LOCKED`) means every door is locked except
the driver's — the state a car lands in after a remote unlock.

Per-door **open/closed** state is separate again, and streamed: the four
`Door state (…)` binary sensors.

## Why some entities are "unavailable"

Entities keep exposing their `cluster`/`category` attributes even when restored
or unavailable, because the card's cluster views depend on them. An entity may
read unavailable until the car next streams that descriptor — trigger a
lock/unlock in the MyBMW app to prompt an update.
