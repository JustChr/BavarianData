# Events & automation blueprints

> 🇩🇪 [Deutsch](DE-Feature-Automations)

BavarianData streams data in real time, so it's built for automations that react
the instant something changes rather than on a polling cycle.

## Device triggers

The easiest way in: create an automation, choose **Device** as the trigger, pick
your car, and choose from the list. No YAML and no entity names needed.

| Trigger | Fires when | Options |
| --- | --- | --- |
| **Arrived at a zone** | a drive ends inside a zone, once the car has parked (usually 1–3 minutes after it stops) | a zone, or empty for any zone |
| **Left a zone** | a drive carries the car out of the zone it started in | a zone, or empty for any zone |
| **Parked and left unlocked** | the car stands unlocked for the chosen time | how long (default 10 minutes) |
| **Plugged in but not charging** | a cable is in, nothing is charging, and the car is below its charging target, for the chosen time | how long (default 15 minutes) |
| **Charging started** | a charging session begins | — |
| **Charging completed (target reached)** | a session ends at or above the target | — |
| **Charging stopped before the target, cable still in** | a session ends short of the target while the cable is still plugged in | — |

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
  target* needs the cable still in two minutes after the stop, so a short pause
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

### What the automation gets

Every trigger passes its details as `trigger.data`:

| Trigger | `trigger.data` |
| --- | --- |
| Arrived at a zone | `zone`, `zone_entity_id`, `from`, `distance_km`, `duration_s`, `energy_kwh`, `soc`, `trip_id`, `trip_start` |
| Left a zone | `zone`, `zone_entity_id`, `soc`, `trip_start` |
| Parked and left unlocked | `since`, `zone`, `zone_entity_id` |
| Plugged in but not charging | `since`, `soc`, `target_soc`, `zone`, `zone_entity_id` |
| Charging started / completed | `soc`, `target_soc`, `status` (completed: also `energy_kwh`, `cost`, `session_id`) |
| Charging stopped before the target | as completed, plus `reason` — BMW's own word for why, when the car sends one |

Every payload also carries `vin` and `entry_id`. An example that warns about a
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
Events**). Each carries `vin` and `entry_id`.

| Event | Fires when | Data |
| --- | --- | --- |
| `bavariandata_charging_started` | a session begins | `soc`, `target_soc`, `status` |
| `bavariandata_charging_stopped` | a session ends (any reason) | `soc`, `target_soc`, `status`, `energy_kwh`, `cost`, `session_id` |
| `bavariandata_charging_complete` | a session ends **at/above** the target SoC | as `charging_stopped` |
| `bavariandata_charging_interrupted` | a session ends short of the target with the cable still in | as `charging_stopped`, plus `reason` |
| `bavariandata_zone_arrived` | a drive ends inside a zone | as the *Arrived at a zone* trigger |
| `bavariandata_zone_left` | a drive leaves the zone it started in | as the *Left a zone* trigger |
| `bavariandata_situation` | a situation begins (`active: true`) or ends (`active: false`) | `situation` (`parked_unlocked` or `plugged_not_charging`), `active`, `since`, … |
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
- **The odometer counts whole kilometres**, so a drive shorter than one
  kilometre may not change it and then fires nothing.
- Fields the car does not send are `null`. The position comes with the event,
  as on the device tracker.

## Starter blueprints

Two starter automations ship with the integration. Import them via **Settings →
Automations → Blueprints → Import Blueprint** with the raw GitHub URL:

- **Stop charging at target %** —
  [`stop_charge_at_target.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/stop_charge_at_target.yaml).
  Switches off a wallbox / smart-plug the moment a SoC sensor reaches your
  target. CarData is **read-only**, so it drives an external switch you already
  have — it can't command the car directly.
- **Notify when charging completes** —
  [`notify_charging_complete.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/notify_charging_complete.yaml).
  Pings a notify service on the charging-complete / -stopped events above.

## Remember: read-only

CarData cannot send commands, so no automation built on this integration can
lock, precondition, or otherwise control the car. Automations act on **external**
devices (wallboxes, plugs, notifiers) in response to the car's data.
