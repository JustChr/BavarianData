# Events & automation blueprints

> 🇩🇪 [Deutsch](DE-Feature-Automations)

BavarianData streams data in real time, so it's built for automations that react
the instant something changes rather than on a polling cycle.

## Device triggers

The easiest way in: create an automation, choose **Device** as the trigger, pick
your car, and choose from the list. No YAML and no entity names needed.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/automation-device-triggers.png" alt="The automation editor's device trigger with the car picked and its trigger list open: Alarm went off, Arrived at a zone, Charging completed (target reached), Charging started, Charging stopped before the target" width="360" />
</p>

| Trigger | Fires when | Options |
| --- | --- | --- |
| **Arrived at a zone** | a drive ends inside a zone, once the car has parked (usually 1–3 minutes after it stops) | a zone, or empty for any zone |
| **Left a zone** | a drive carries the car out of the zone it started in | a zone, or empty for any zone |
| **Trip ended (anywhere)** | a drive is recorded in the trip journal, wherever it ended | — |
| **Parked and left unlocked** | the car stands unlocked for the chosen time | how long (default 10 minutes) |
| **Parked with a window open** | a window or the sunroof stands open, or partly open, for the chosen time | how long (default 10 minutes) |
| **Plugged in but not charging** | a cable is in, nothing is charging, and the car is below its charging target, for the chosen time | how long (default 15 minutes) |
| **Charging started** | a charging session begins | — |
| **Charging completed (target reached)** | a session ends at or above the target | — |
| **Charging stopped before the target, cable still in** | a session ends short of the target while the cable is still plugged in | — |
| **Preconditioning started** | the car starts heating or cooling itself | — |
| **Preconditioning finished** | the car stops heating or cooling itself | — |
| **Alarm went off** | the anti-theft alarm starts sounding | — |
| **New Check Control message** | a warning the car did not show before appears | — |

**"Charging completed" is the "target reached" trigger.** The car stops at its
charging target, and the session ends there. To act at a different level, say
70 % on a wallbox with no limit of its own, use a numeric state trigger on the
battery level entity.

**Only the triggers your car can fire are listed.** A car that never sent a lock
state gets no *Parked and left unlocked*; the zone triggers need the car's
position and the [trip journal](Feature-Trips) (on by default).

### What they deliberately don't do

These triggers are built to fire exactly once, and never falsely:

- **Nothing fires from a restart.** After Home Assistant restarts, a situation
  starts again only once the car reports it anew. A car that was locked while
  Home Assistant was down never raises "left unlocked" on startup. The price is
  that a car that was already unlocked before the restart is only noticed at its
  next report.
- **Unlocking to get in is not "left unlocked".** The car unlocks at every
  arrival and relocks itself about two minutes later if no door is opened. The
  waiting time exists for that; below about three minutes the trigger fires
  every day.
- **A car that is done charging is not "not charging".** At (or within 1 % of)
  its target, a plugged-in car that has stopped is finished, not stuck.
- **Unplugging early is not an interruption.** *Charging stopped before the
  target* needs the cable still in five minutes after the stop, so a short pause
  that resumes on its own, or a driver who unplugs, doesn't fire it.
- **Solar and price-controlled charging pauses on purpose.** If a charge
  controller (evcc, a wallbox's solar mode) stops and starts the charge, *Plugged
  in but not charging* and *Charging stopped before the target* fire at those
  pauses too — that is what happened. Add a condition (for example "only after
  22:00") or a longer waiting time.
- **Arriving is not crossing the boundary.** *Arrived at a zone* fires when the
  drive ends, so driving past home doesn't trigger it, and GPS jitter at the zone
  edge can't make it flicker. Home Assistant's own zone triggers on the car's
  location entity still exist if you want the moment of crossing.
- **A window open while driving is not "parked with a window open".** The
  situation starts once the drive is over. A partly open window counts: a
  window left open a crack lets the rain in too.
- **An old value is not news.** The alarm and preconditioning triggers fire only
  on a change the car streams, and only while the car's own time stamp on it is
  less than 10 minutes old. Neither the catch-up after a restart (BMW may answer
  it from its cache) nor the car repeating an old value starts them.
- **The first Check Control list only sets the starting point.** Warnings the car
  already showed when you installed the integration are not announced. The car
  sends these warnings only with the data fetched from BMW, never on the live
  stream, so a new one shows up at the next fetch (at startup, or when a service
  fetches), not the moment it appears in the car.
- **The alarm is not verified on a real alarm.** It is built from BMW's alarm
  field and tested against recorded data, but no car with this integration has
  set its alarm off yet. If yours does and nothing fires, please open an issue.

### What the automation gets

Every trigger passes its details as `trigger.data`:

| Trigger | `trigger.data` |
| --- | --- |
| Arrived at a zone | `zone`, `zone_entity_id`, `from`, `distance_km`, `duration_s`, `energy_kwh`, `soc`, `trip_id`, `trip_start` |
| Left a zone | `zone`, `zone_entity_id`, `soc`, `trip_start` |
| Trip ended | as *Arrived at a zone* (`zone` and `zone_entity_id` are `null` outside a zone), plus `trip_end`, `to`, `soc_start`, `classification` |
| Parked and left unlocked | `since`, `zone`, `zone_entity_id` |
| Parked with a window open | `since`, `windows` (which ones: `front_driver`, `front_passenger`, `rear_driver`, `rear_passenger`, `sunroof`, `sunroof_tilt`, `rear_window`), `zone`, `zone_entity_id` |
| Plugged in but not charging | `since`, `soc`, `target_soc`, `zone`, `zone_entity_id` |
| Charging started / completed | `soc`, `target_soc`, `status` (completed: also `energy_kwh`, `cost`, `session_id`) |
| Charging stopped before the target | as completed, plus `reason` — BMW's own word for why, when the car sends one |
| Preconditioning started / finished | `activity` (`heating`, `cooling`, … or `inactive`), `error` (BMW's reason when it could not run, such as `lowbattery`; `ok` otherwise), `soc`, `zone`, `zone_entity_id` |
| Alarm went off | `zone`, `zone_entity_id` |
| New Check Control message | `id`, `title`, `text`, `message_type`, `message` (the whole entry as BMW sends it), `zone`, `zone_entity_id` |

Every payload also carries `vin`, `entry_id` and `vehicle`, the car's name as BMW
reports it (`null` until it does). An example that warns about a
car left unlocked at home:

```yaml
triggers:
  - trigger: device
    domain: bavariandata
    device_id: <your car>
    type: parked_unlocked
    for: { minutes: 10 }
conditions:
  - condition: template
    value_template: "{{ trigger.data.zone_entity_id == 'zone.home' }}"
actions:
  - action: notify.mobile_app_phone
    data:
      message: "The car has been unlocked in the driveway for 10 minutes."
```

## Events

Everything behind the triggers is also fired on the Home Assistant **event
bus**, for YAML automations and Node-RED (watch them under **Developer Tools →
Events**). Each carries `vin`, `entry_id` and `vehicle` (the car's name).

| Event | Fires when | Data |
| --- | --- | --- |
| `bavariandata_charging_started` | a session begins | `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | a session ends (any reason) | `soc`, `target_soc`, `status`, `energy_kwh`, `cost`, `session_id`, `complete` (`true` when it reached its target) |
| `bavariandata_charging_complete` | a session ends **at/above** the target SoC, also one that finished while Home Assistant was restarting | as `charging_stopped` |
| `bavariandata_charging_interrupted` | a session ends short of the target with the cable still in | as `charging_stopped`, plus `reason` |
| `bavariandata_zone_arrived` | a drive ends inside a zone | as the *Arrived at a zone* trigger |
| `bavariandata_zone_left` | a drive leaves the zone it started in | as the *Left a zone* trigger |
| `bavariandata_trip_ended` | a drive is recorded, in a zone or not | as the *Trip ended* trigger |
| `bavariandata_situation` | a situation begins (`active: true`) or ends (`active: false`) | `situation` (`parked_unlocked`, `parked_window_open` or `plugged_not_charging`), `active`, `since`, … |
| `bavariandata_preconditioning` | preconditioning starts (`active: true`) or finishes (`active: false`) | `active`, then as the *Preconditioning* triggers |
| `bavariandata_alarm` | the alarm starts (`active: true`) or stops (`active: false`) | `active`, `zone`, `zone_entity_id` |
| `bavariandata_check_control` | a Check Control message appears (`active: true`) or is gone (`active: false`) | `active`, then as the *New Check Control message* trigger |
| `bavariandata_vehicle_report` | the odometer has a new reading and it has not changed for 5 minutes | `timestamp` (the car's own time), `odometer_km`, `previous_odometer_km`, `previous_timestamp`, `distance_km`, `fuel_l`, `fuel_percent`, `range_km`, `soc_percent`, `latitude`, `longitude`, `altitude_m`, `heading` |

`bavariandata_situation` fires at the moment a situation starts, with no waiting
time — the device triggers add the "for N minutes". In YAML, the same effect
needs a `wait_for_trigger` on the matching `active: false` event.

`bavariandata_vehicle_report` is made for **logbooks**, above all on older cars
that send their data in one burst when parked and nothing while driving. It
fires once per new odometer reading, after the reading has held for 5 minutes,
so the burst is complete by then. Things to know:

- **It does not fire for a repeated reading.** Some cars repeat their unchanged
  odometer every few minutes; that is not a report. Neither is the catch-up
  after a restart, which hands back the car's last values.
- **The first reading after installing or updating only sets the starting
  point.** The first event comes with the second new reading.
- **On a car that streams while driving** it fires at the end of the drive —
  and a second time if the car stood still for 5 minutes or more in between,
  in a long traffic jam for example. Each event carries its own `distance_km`,
  so the distances still add up.
- **A reading the car could not have driven is not announced.** One that runs
  backwards, or lies further ahead than 250 km/h would cover since the last
  report, is set aside. If the next reading follows on from it, it was the
  earlier one that was wrong, and the reports carry on from there. Either way
  one drive may go unannounced — a made-up one never is.
- **The odometer counts whole kilometres**, so a drive shorter than one
  kilometre may not change it and then fires nothing.
- Fields the car does not send are `null`. The position comes with the event,
  as on the device tracker.

## Starter blueprints

Eight starter automations ship with the integration. Import them via **Settings →
Automations → Blueprints → Import Blueprint** with the raw GitHub URL:

- **Stop charging at target %** —
  [`stop_charge_at_target.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/stop_charge_at_target.yaml).
  Switches off a wallbox / smart-plug the moment a SoC sensor reaches your
  target. CarData is **read-only**, so it drives an external switch you already
  have — it can't command the car directly.
- **Notify when charging completes** —
  [`notify_charging_complete.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/notify_charging_complete.yaml).
  Sends one message when a session ends, either only when the car reached its
  target or on any end. The title names the car, and **Cars** limits it to the
  ones you pick (empty: every car). Re-import it to get the car filter. Until
  v0.9.20 a charge that reached its target sent two messages when *Only when
  target reached* was off.
- **Mirror the charging cable into a switch** —
  [`plug_to_switch.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/plug_to_switch.yaml).
  Turns a switch on when the cable goes in at home and off when it comes out.
  Made for charge planners that need to know the car is connected, such as EV
  Smart Charging's *EV connected* switch; see
  [Smart charging](Feature-Smart-Charging). Plugging in away from home does
  nothing; a car that sends no position counts as home.
- **Car arriving home soon** —
  [`arriving_home.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/arriving_home.yaml).
  Runs your actions once per drive, when the car gets closer to home than the
  threshold you set: open the garage, warm the house. It needs a figure that
  falls as the car comes home, with the car's location as its origin. Either:
  - **Waze Travel Time** or **Google Maps Travel Time**: minutes to home, with
    traffic. The blueprint refreshes it at every new position.
  - **Proximity** (built into Home Assistant, no account): the distance to
    home. Set the threshold in km (mi if it shows feet), even though
    Proximity shows metres: the blueprint converts.

  Things to know:
  - **It doesn't know where you're going.** It only sees the car getting
    closer to home. A drive that passes near home and carries on fires it too.
    Pick the zones your drives home start from (*Only trips that start in*,
    e.g. Work) to count only those.
  - **A short drive may not fire it.** One that never got further away than
    your threshold never crosses it; leaving home never fires it either.
  - **Expect it a little after the threshold.** BMW sends the position about
    every one to three minutes while driving.
  - The car's own navigation has a *GPS arrival time* field. A destination
    set on the phone through CarPlay or Android Auto is unknown to the car, so
    the blueprint doesn't rely on it.
- **Rain coming, a window is open** —
  [`rain_window_open.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/rain_window_open.yaml).
  When a window, the sunroof or the tailgate window has been open for 10
  minutes, and every hour after that, it checks your weather forecast. Rain
  within the hours you set (by probability where your forecast gives one, else
  any rain amount or a rainy condition) runs your actions, with the open ones
  named in `open_names`. Then it stays quiet until everything is closed.
- **Frost tonight, the car isn't plugged in** —
  [`frost_unplugged.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/frost_unplugged.yaml).
  At the time you set (20:00 by default), checks the forecast low until 09:00
  the next morning. At or below your frost limit, with the car at home and the
  cable out, it runs your actions, with the low in `low`. Plugged in, an
  electric car can warm its battery and cabin from the grid before you leave.
- **One tire losing pressure** —
  [`tire_pressure_imbalance.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/tire_pressure_imbalance.yaml).
  Compares the two tires on each axle. Weather and the warmth of driving move
  both alike, so a gap that holds for an hour (30 kPa, 0.3 bar, by default)
  means one is losing air, usually well before the car's own warning. It
  names the tire in `low_tire` and fires again only after the gap has closed.
  Healthy tires stay within about 20 kPa of each other.
- **Car left open at night** —
  [`car_left_open.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/car_left_open.yaml).
  At the time you set (22:00 by default), checks that the car is locked and
  its doors, windows, sunroof, tailgate and hood are shut. If anything isn't,
  it runs your actions once, with what is open or unlocked in `open_names`.
  You only pick the car (or several): it finds the car's lock, door, window
  and lid sensors itself and checks those your car sends. The charge flap and
  the cable lock are left out, since they are open every night the car
  charges. To check from your own goodnight routine instead, run the
  automation from it.
  The car can't be locked from Home Assistant (CarData is read-only); lock it
  in the My BMW app.

## Ask Assist about your car

With an AI conversation agent behind Assist (OpenAI, Google, Anthropic or
Ollama), Assist can answer questions such as *"Do I need to charge before
driving to Munich and back?"*, *"Is the car locked?"*, *"What did charging cost
this month?"* or *"Am I on track with my lease?"*. BavarianData offers its own
set of tools for that. They read what the card reads, so they cost **no API
quota**.

Turn them on once: open your AI integration under **Settings → Devices &
services**, then **Configure** its conversation agent, and tick
**BavarianData** under *Control Home Assistant*, next to *Assist*. Nothing has
to be exposed, and no scripts are needed. Home Assistant's *Model Context
Protocol Server* integration offers the same choice, so desktop AI apps can
ask too.

The tools:

| Tool | Answers |
|---|---|
| Car status | Battery, charging (power, minutes to the target), range (on a plug-in hybrid: electric, fuel and total), fuel level, the zone it is in, whether it is being driven, anything open or unlocked |
| Range check | Whether a drive fits the range left, one way or there and back, with a reserve (a tenth of a full battery's range, at least 20 km), and how much more charge it needs if not. A plug-in hybrid or fuel car counts its tank too (reserve 20 km), and a hybrid also says whether the battery alone does it. The agent estimates the distance from the places you name. |
| Charging history | The sessions of the last days (30 unless you say otherwise): when, where, battery before and after, energy and cost, with totals |
| Driving month | Distance, trips, the business/private/commute split, consumption, cost and the most visited places, against the month before |
| Mileage | Odometer, this month and year, the yearly projection and your lease or yearly allowance, if one is set |
| Efficiency | Measured consumption, the real range it gives, how it compares with the car's own figure, and the monthly trend |

The range is the [measured one](Feature-Efficiency-and-Range) where there is
one, else the car's own. A plug-in hybrid's fuel range is the car's total range
less its electric range, since BMW sends no fuel range of its own. Answers come in your car's distance unit. With several
cars, the agent can ask about one by name or about all of them.

**What the AI sees.** The figures above and the names of places (your zones,
or addresses for trips that ended elsewhere). Never the VIN, the car's
position, trip ids or charging curves: they are removed from every answer.
The tools can't do anything to the car; CarData is read-only. With a cloud
agent, these figures leave your home; a local agent such as Ollama keeps them
there.

*Set up the three `car_…` scripts from an earlier version of this page?* You
can delete them; the tools answer everything they did.

## Remember: read-only

CarData cannot send commands, so no automation built on this integration can
lock, precondition, or otherwise control the car. Automations act on **external**
devices (wallboxes, plugs, notifiers) in response to the car's data.
