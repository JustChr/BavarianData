# Events & automation blueprints

> 🇩🇪 [Deutsch](DE-Feature-Automations)

BavarianData streams data in real time, so it's built for automations that react
the instant something changes rather than on a polling cycle.

## Device triggers

The easiest way in: create an automation, choose **Device** as the trigger, pick
your car, and choose from the list. No YAML and no entity names needed.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/automation-device-triggers.png" alt="The automation editor's device trigger with the car picked and its trigger list open: Arrived at a zone, Charging completed (target reached), Charging started, Charging stopped before the target" width="360" />
</p>

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

Seven starter automations ship with the integration. Import them via **Settings →
Automations → Blueprints → Import Blueprint** with the raw GitHub URL:

- **Stop charging at target %** —
  [`stop_charge_at_target.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/stop_charge_at_target.yaml).
  Switches off a wallbox / smart-plug the moment a SoC sensor reaches your
  target. CarData is **read-only**, so it drives an external switch you already
  have — it can't command the car directly.
- **Notify when charging completes** —
  [`notify_charging_complete.yaml`](https://github.com/JustChr/BavarianData/blob/main/blueprints/automation/bavariandata/notify_charging_complete.yaml).
  Pings a notify service on the charging-complete / -stopped events above.
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
    home. Set the threshold in its unit (km or mi).

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

## Ask Assist about your car

With an AI conversation agent behind Assist (OpenAI, Google, Anthropic or
Ollama), Assist can answer questions such as *"How far did I drive last
month?"*, *"What did charging cost this week?"* or *"Am I on track with my
lease?"* from BavarianData's own history. It needs three scripts that hand the
answers over. They read the local store, so they cost **no API quota**.

1. Add the scripts below to `scripts.yaml` (or create each under *Settings →
   Automations & scenes → Scripts*, *Edit in YAML*), then reload scripts.
2. Expose them to Assist: *Settings → Voice assistants → Expose*, add the
   three scripts.
3. Turn on *Control Home Assistant* for your conversation agent, if it isn't.

```yaml
car_driving_month:
  alias: Car driving month
  description: >-
    How far the car was driven in a month: distance, number of trips, the
    private/business/commute split, average consumption and the estimated
    driving cost. Use it for questions about distance, trips, consumption or
    what driving cost.
  fields:
    month:
      description: The month as YYYY-MM. Leave empty for the current month.
      example: "2026-09"
      selector:
        text:
  sequence:
    - action: bavariandata.get_driving_summary
      data:
        month: "{{ month if month | default('') | trim else now().strftime('%Y-%m') }}"
      response_variable: result
    - variables:
        # Named fields only: the trip ids in the raw answer hold the VIN.
        answer: >
          {% set s = result.summary %}
          {% set best = s.best_trip or {} %}
          {% set worst = s.worst_trip or {} %}
          {{ {'month': result.month,
              'total_km': s.total_km, 'trip_count': s.trip_count,
              'avg_trip_km': s.avg_trip_km, 'longest_trip_km': s.longest_trip_km,
              'split': s.split,
              'avg_consumption_kwh_per_100km': s.avg_consumption_kwh_per_100km,
              'energy_balance': s.energy_balance,
              'top_destinations': s.top_destinations,
              'previous_month_km': s.prev_total_km,
              'change_vs_previous_month_percent': s.mom_delta_percent,
              'estimated_cost': s.estimated_cost,
              'most_efficient_trip': {'to': best.get('label'),
                                      'kwh_per_100km': best.get('consumption'),
                                      'km': best.get('distance_km')},
              'least_efficient_trip': {'to': worst.get('label'),
                                       'kwh_per_100km': worst.get('consumption'),
                                       'km': worst.get('distance_km')}} }}
    - stop: Done
      response_variable: answer

car_charging_history:
  alias: Car charging history
  description: >-
    The car's charging sessions over the last days: when and where, state of
    charge before and after, energy and cost. Use it for questions about
    charging, charging cost or energy.
  fields:
    days:
      description: How many days back to look. 30 if not given.
      example: 30
      selector:
        number:
          min: 1
          max: 365
  sequence:
    - action: bavariandata.get_charging_sessions
      data:
        from: "{{ (now() - timedelta(days=days | default(30) | int)).date() | string }}"
      response_variable: result
    - variables:
        answer: >
          {% set ns = namespace(sessions=[]) %}
          {% for s in result.sessions %}
            {% set ns.sessions = ns.sessions + [{
              'start': s.start, 'end': s.end,
              'place': (s.location or {}).get('zone'),
              'soc_before': s.soc_start, 'soc_after': s.soc_end,
              'kwh_from_grid': s.grid_kwh, 'kwh_into_battery': s.energy_kwh,
              'cost': (s.cost or {}).get('amount'),
              'currency': (s.cost or {}).get('currency')}] %}
          {% endfor %}
          {{ {'sessions': ns.sessions} }}
    - stop: Done
      response_variable: answer

car_mileage:
  alias: Car mileage
  description: >-
    The car's odometer, the distance driven this month and this year, the
    last twelve months, its pace in km per day and, if one is set, where it
    stands against its lease or yearly mileage allowance.
  sequence:
    - action: bavariandata.get_mileage_report
      data: {}
      response_variable: result
    - variables:
        answer: >
          {{ {'odometer_km': result.odometer_km,
              'this_month_km': result.this_month.km,
              'this_year_km': result.this_year.km,
              'km_per_day': result.km_per_day,
              'projected_year_km': result.projected_year_km,
              'months': result.months,
              'allowance': result.allowance} }}
    - stop: Done
      response_variable: answer
```

**What the AI sees.** Exactly what these scripts return: distances,
consumption, costs, charging times and the names of places (your zones, or
addresses for trips that ended elsewhere). Nothing else, and no VIN: the
scripts list each field by name, because the raw service answers carry the VIN
inside their ids. With a cloud agent, these figures leave your home; a local
agent such as Ollama keeps them there.

With more than one car (or account), add `vin:` (and `entry_id:`) under each
script's `data:` and give each car its own three scripts, named after the car.

## Remember: read-only

CarData cannot send commands, so no automation built on this integration can
lock, precondition, or otherwise control the car. Automations act on **external**
devices (wallboxes, plugs, notifiers) in response to the car's data.
