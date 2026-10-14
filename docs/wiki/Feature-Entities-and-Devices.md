# Entities & devices

> 🇩🇪 [Deutsch](DE-Feature-Entities-and-Devices)

## One device per VIN

Each VIN on your account becomes its own Home Assistant **device**. A separate
integration-level **"CarData Debug Device"** carries the diagnostics entities
(stream connection, quota, last-message timestamps).

## Descriptor entities

Every descriptor BMW streams becomes a native entity:

- Streamed descriptors become **sensors** and **binary sensors**, named from a
  curated English title set (baked into the catalogue and the HA translations).
  Nine more languages ship too, using BMW's own names for each field — open an
  issue or PR if a name looks off.
- Numeric fields get sensible **device classes**; distances use
  `device_class: distance`, and odometer/mileage uses
  `state_class: total_increasing` so long-term statistics work.
- Percentages are measurements with long-term statistics too, including those
  without a device class: the tank level in %, seat and steering-wheel heating,
  sunroof and door positions, preconditioning progress.
- Every entity exposes its **source timestamp** (`timestamp`: when the car
  measured the value, not when Home Assistant received it) plus its catalogue
  `cluster` and `category` as attributes — the [dashboard card](The-Dashboard-Card) uses these
  to group values regardless of the user's HA language.
- A few descriptors are **lists** rather than single values — **Condition Based
  Service** (each service item with its due date) and **Check Control messages**
  (the warnings the car raised). Their state is the **number of entries** and the
  full list is in the **`items`** attribute, e.g.
  `{{ state_attr('sensor.<car>_check_control_messages', 'items') }}` in a
  template. A state of `0` means the car reported an empty list — or, for Check
  Control, that BMW reported no messages at all, which is how a cleared warning
  arrives. Check Control messages that have cleared stay in a **`resolved`**
  attribute (the last ten, newest first), each with `last_reported` and
  `resolved_at`.

The full field-per-cluster catalogue lives in
[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md).

## Derived & diagnostic entities

These aren't sent by BMW — the integration derives them. They are named from the
integration's own translations (so they are named in your language too).

| Entity | What it is |
| --- | --- |
| **Charged Energy (Total)** | Monotonic kWh counter (`device_class: energy`, `state_class: total_increasing`). Counts every charge; for the Energy dashboard use the [home series](Feature-Energy-and-Statistics#adding-to-the-energy-dashboard). |
| **Charged Energy (Session)** | Resets at the start of each charging session. |
| **Charging Energy (This Month)** | Monthly charged-energy total. |
| **Charging Cost (This Month)** | Monthly cost — only once a price source is set. |
| **Charging Cost (Last Session)** | Cost of the most recent session — only with a price source. |
| **Charging Cost per 100 km** | Needs the odometer and two sessions to measure a distance. Not created for a [plug-in hybrid](Feature-Efficiency-and-Range#plug-in-hybrids). |
| **Battery Health** | Learned usable capacity (kWh), with vs-new %, sample count and a capacity-vs-mileage trend. |
| **Real Range** | How far the car really goes from its current charge (km), from measured consumption and usable capacity — with the car's own prediction and the difference as attributes. Reads `unknown` until your charging history can support a figure; the `status` attribute says why ([details](Feature-Efficiency-and-Range#the-real-range-sensor)). Not created for a [plug-in hybrid](Feature-Efficiency-and-Range#plug-in-hybrids). |
| **Driving Distance (This Month)** | Monthly distance + business/private/commute split. |
| **Trip in Progress** | Binary sensor: `on` while a drive is under way, with the trip so far as attributes. Deliberately *not* a "moving" sensor — it lingers after an arrival; see [Trips](Feature-Trips#seeing-the-drive-thats-happening-now). |
| **Tire Condition** | BMW's overall verdict on the mounted set, plus any upstream errors. |
| **Tire Front Left / Front Right / Rear Left / Rear Right** | Per-wheel wear traffic light (`green`/`yellow`/`red`/`grey`), with the mileage until a change is due, defect status, season, dimension, tread pattern and fitting date as attributes. |
| **API Quota Remaining** | Diagnostic: requests left in the 50/24 h window. |
| **State-of-charge estimate / rate** | Extrapolated SoC helpers (need the Electric vehicle cluster) — see [which state-of-charge entity to use](#which-state-of-charge-entity-to-use). |
| **Predicted time to charge target** | Minutes until a **home** charge reaches the car's target, worked out from the [wallbox meter](Feature-evcc-and-Wallbox-Bridge#time-to-the-charge-target). `0` when not charging; unknown away from home, in a charge's first minutes or while a solar charge is paused. Only with a wallbox meter bound. |
| **Stream Connection Status** | Diagnostic: MQTT connection state. |
| **Last Message Received** | Diagnostic: timestamp of the last stream payload. |
| **Last Vehicle Report** | Diagnostic, **disabled by default**: when the car last reported its odometer, by the car's own clock. Differs from *Last Message Received* when a car parked without reception reports late. |
| **Mileage Allowance Balance** | **Disabled by default**: how far under (+) or over (−) its [mileage allowance](Feature-Mileage)'s plan the car is, in km, with used, remaining and the projected end as attributes. Unknown without an allowance. |
| **Last Telematics API Call** | Diagnostic: timestamp of the last REST call. |

The charging, state-of-charge, battery-health and real-range entities only exist
for a car with a **high-voltage battery**; they appear once the car first sends
battery data. A petrol or diesel car gets none of them. One that was given them by
an earlier release has them removed on the first start after updating, because
they could never hold a value — their old history may then be listed under
**Developer tools → Statistics**, where it can be deleted.

The **fuel in the tank** (*Range Tank level*) is a volume sensor with long-term
statistics. It takes the unit the car sends — liters, or gallons on a car that
reports them — and Home Assistant converts it to your display unit like any other.

The tire entities only exist for wheels BMW actually reports. Many cars have no
tire service record on file, in which case none are created — that is BMW having
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

The vehicle image and the device tracker also carry BMW's **basic data** for the
car: `vehicle_basic_data` (model, drivetrain, steering side, sunroof, the
factory option codes, …) and `vehicle_basic_data_raw` (BMW's answer as sent).
Use them in templates, e.g. `{{ state_attr('image.my_car', 'vehicle_basic_data').model_name }}`.
No other entity has them.

BMW CarData provides exactly one picture per car, a side view, and the API has
no option for other angles. **Configure → Vehicle image** can switch the picture
to BMW's online configurator instead: your exact car as a 360° turntable, at no
request cost, but through an unofficial BMW service
([more](Settings-Reference#vehicle-image)). The entity then shows the
configurator's front three-quarter view, and carries attributes for the card:
`configurator_spin`, `configurator_interior`, `configurator_top` (MINIs),
`paint_color`, and `equipment`, the car's factory options by name (kept out of
the recorder's history). The top-down car in the card's tire and closure views is not a BMW
image but a drawing built into the card: a BMW sedan, or a MINI Countryman for
that model ([more](The-Dashboard-Card#the-cars-shape)).

## Device tracker

Each VIN gets a **device_tracker** ("car") carrying the vehicle's location from
the GPS stream, usable on the HA map and in zone-based automations.

**Need the position as plain sensors?** Some add-ons only let you pick
`sensor.*` entities, not a tracker's attributes (DriveLoom's vehicle setup, for
example). Each car also has **GPS latitude** and **GPS longitude** sensors, but
they are **disabled by default**: every fix is written to the recorder as a
state, and the tracker already covers the map and zones. To use them, open
*Settings → Devices & Services → BavarianData → your car → disabled entities*,
enable the two and wait for Home Assistant to reload the integration. They
update with the stream, latitude and longitude as separate messages, like the
tracker does. If you only need them for another integration, exclude them from
the recorder.

## Service calendar

Each car gets a **Service Calendar**: the service items the car tracks (brake
fluid, vehicle check, statutory inspection, …) as all-day events, in Home
Assistant's calendar and on any calendar card. It's **disabled by default**;
enable it under *Settings → Devices & Services → BavarianData → your car →
disabled entities*. It costs no quota: the items come with the daily data fetch.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-service-calendar.png" alt="Home Assistant's calendar card in list view: brake fluid and the vehicle check as all-day events on 1 October 2027" width="400" />
</p>

- BMW gives a due **month**, not a day, so each item is an event on the **1st**
  of that month, described as "due by the end of October 2027". When BMW moves
  the date, the event moves with it.
- An item with only a **distance** left (an oil service on some cars) is placed
  where your own driving puts it: the distance left divided by your average km
  a day over the last 90 days. The event says it's an estimate. Until
  BavarianData has seen four weeks of odometer readings it shows no date for
  such an item, rather than a guess. The readings come from your charging
  history first, so most installs have a pace on the day they upgrade.
- An item BMW marks as due soon or overdue says so in its title.
- A car with a lease set up under [mileage allowance](Feature-Mileage) also
  gets its **hand-back day** as an event.
- The texts follow Home Assistant's language; distances follow its unit system.

The same items are on the **Service Condition based services** sensor's `items`
attribute, if you'd rather template them.

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

## Which range entity to use

An electric car can have up to five range sensors from BMW plus one of ours, and
BMW gave the least useful one the most obvious name:

| Entity | Descriptor | What it is |
| --- | --- | --- |
| **Range EV Remaining range** | `electricEngine.kombiRemainingElectricRange` | The electric range in the **instrument cluster**. The one to use. |
| **Range Total range (last sent)** | `lastRemainingRange` | Total range. The same figure on a pure EV; on a plug-in hybrid it **includes the fuel**. |
| **Range EV Estimate during charging** | `electricEngine.remainingElectricRange` | A prediction made **while charging**. It arrives a few times a day and then sits on that value, so on a parked car it can be far off — 128 km on a car showing 379. |
| **Range EV Remaining (set target based)** | `range.target` | The range once the car reaches its **charge target**, not the range now. |
| **GPS remaining range** | `navigation.remainingRange` | The navigation system's figure. |
| **Real Range** | — (calculated) | Ours: how far your **measured** consumption reaches on the current charge. See [Efficiency & real range](Feature-Efficiency-and-Range). |

Which of BMW's arrive depends on the car — not every model streams all of them.
The [dashboard card](The-Dashboard-Card) shows *Range EV Remaining range* and
only falls back to the others on a car that doesn't send it.

## Which state-of-charge entity to use

The same trap, with the battery level:

| Entity | Descriptor | What it is |
| --- | --- | --- |
| **Battery HV State Of Charge** | `batteryManagement.header` | The high-voltage battery's **measured** charge level, streamed. Despite the descriptor name, this is the one. |
| **State Of Charge (Predicted on Integration side)** | — (calculated) | Ours. While parked it equals the measured value; while charging it **keeps counting** when BMW goes quiet mid-charge, which it can for hours. The card shows this one. |
| **Battery EV Displayed state of charge** | `stateOfCharge.displayed` | BMW's display figure, on cars that stream it. |
| **Charging EV Predicted State Of Charge** | `electricEngine.charging.level` | BMW's server-side prediction (what the MyBMW app shows). **REST only**, so it never updates on the stream. |
| **Trip Battery charge level at end of trip** | `trip.segment.end…hvSoc` | Only changes when a drive ends. |
| **Battery charge level (12V)** | `electricalSystem.battery.stateOfCharge` | The small 12 V battery, not the drive battery. |

If *Battery HV State Of Charge* never fills in, the descriptor isn't switched on
in your Data Selection — see
[Charging history](Feature-Charging-History-and-Cost#no-startend-soc-on-a-session).

## Entity names and your language

<a id="entity-names-and-your-language"></a>

Entity names follow Home Assistant's own language setting. Eleven are shipped:

| HA language | What you get |
| --- | --- |
| **English** | US spellings — *Tire pressure (front left)*, *Tire Condition* |
| **English (UK)** | British spellings — *Tyre pressure (front left)*, *Tyre Condition* |
| **Deutsch** | *Gemessener Reifendruck vorne links*, *Reifenzustand* |
| **Français** | *Pression mesurée du pneu avant gauche*, *État des pneus* |
| **Italiano** | *Pressione misurata pneumatico anteriore sinistro*, *Stato degli pneumatici* |
| **Español** | *Presión medida del neumático delantero izquierdo*, *Estado de los neumáticos* |
| **Nederlands** | *Gemeten bandspanning linksvoor*, *Bandenconditie* |
| **Polski** | *Zmierzone ciśnienie w oponach przód lewo*, *Stan opon* |
| **Português** | *Pressão do pneu medida no pneu dianteiro esquerdo*, *Estado dos pneus* |
| **Čeština** | *Změřený tlak v pneumatice vpředu vlevo*, *Stav pneumatik* |
| **Svenska** | *Uppmätt däcktryck vänster fram*, *Däckstatus* |

Outside English, the names of BMW's fields are **BMW's own**, from the catalogue
BMW publishes in each language — so they match the wording in the BMW portal, not
a translation of our English titles. The one exception is the side doors and
windows. BMW reports them by seat, but several of its translations name them by
side (*Status der Tür vorn links*), which is the wrong door on a right-hand-drive
car. Those say driver or passenger side instead (*Status der Tür Fahrerseite
vorn*), as the English names do. The setup screens, options, services, repair
notices, the month report and the dashboard card are translated too. Portuguese is
European Portuguese; a Brazilian Portuguese install falls back to English in
Home Assistant, while the card still shows Portuguese.

Switch under *Profile → Language*; the names change on the next reload. The
bundled card follows the same setting.

**Entity IDs never change with it.** They are built once, from the descriptor BMW
sends — `sensor.<car>_tire_pressure_front_left` — so automations, dashboards and
templates keep working whichever language you pick, and a UK install still
refers to `tire` in YAML. Only the display name is translated.

US English is the default because BMW's own field names are US
(`vehicle.chassis.axle.row1.wheel.left.tire.pressure`), so the name you read
matches the ID you type. British English overrides only the words that actually
differ and inherits everything else, which is why a new label appears in both
without waiting for a translation.

## Unknown or unavailable?

<a id="why-some-entities-are-unavailable"></a>

The two mean different things here.

**`unknown`: there is no value yet.** Either BMW hasn't sent this field since
you installed, or a calculated entity is still waiting for enough data:

| Entity | What it waits for |
| --- | --- |
| A BMW field | The car's next report of it. Streamed fields fill in on their own; a lock/unlock in the MyBMW app usually prompts one. **REST-only** fields ([which](Getting-Started-4-Choose-Data#some-fields-never-arrive-on-the-stream)) need the daily refresh or a `fetch_*` service. A field your car doesn't have stays `unknown` for good. |
| **Real Range** | At least ~50 km driven between two recorded charges, and a battery capacity from the car. Its `status` attribute names the missing one: `not_enough_history` or `no_capacity`. |
| **Charging Cost per 100 km** | A [price source](Feature-Charging-History-and-Cost#setting-up-cost) and two charges with driving in between. |
| **Battery Health** | Not `unknown`: it reads `Learning (n/10)` until it has ten good charges ([why](Feature-Battery-Health)). |
| **Predicted time to charge target** | A home charge with a wallbox meter bound ([details](Feature-evcc-and-Wallbox-Bridge#time-to-the-charge-target)). |

**`unavailable`: the integration isn't running for that entity.** BavarianData
never marks its own entities unavailable, so this means the integration failed to
start or needs re-authorizing (check *Settings → Devices & services* and
*Repairs*), or the entity is left over from something the integration no longer
creates — then you can delete it.

Either way, entities keep their `cluster`/`category` attributes, because the
card's cluster views depend on them.
