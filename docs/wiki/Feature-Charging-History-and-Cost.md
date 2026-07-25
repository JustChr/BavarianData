# Charging history & cost

Because BavarianData streams data in real time, it records every completed
charging session and can put a price on it — all from the stream, spending **no
REST quota**.

## Charged-energy sensors

BMW never sends a kWh counter, so the integration integrates the live charging
power over time into two sensors per vehicle:

- **Charged Energy (Total)** — a monotonic kWh counter
  (`device_class: energy` / `state_class: total_increasing`). Add it to the
  **Energy dashboard** as an "individual device" to see and cost your car's
  charging alongside the rest of the house.
- **Charged Energy (Session)** — resets at the start of every session.

Both work on any vehicle that streams charging power.

## Recorded history

Every completed session is recorded and kept in the integration's **own store**
rather than the recorder (which purges after ten days). It captures start/end
SoC, energy, duration, peak power, the charging power curve, and cost.

Retention is set by **`history_retain_months`** under
**Configure → Charging costs & history** (0 = keep everything).

## Setting up cost

Set this up under **Configure → Charging costs & history**
([Settings reference](Settings-Reference#charging-costs--history)). Pick a
**price source**:

- **Fixed price per kWh** — a flat rate you know.
- **Live price entity** — point it at Tibber, Nordpool, aWATTar, … That entity is
  sampled *while the car charges*, so a session spanning a price change is billed
  correctly rather than at whatever the price happened to be at the end.

> Until you choose a price source, **no cost entities are created at all** — a
> wrong number would be worse than none.

You then get, per vehicle:

- **Charging Energy (This Month)** — always available.
- **Charging Cost (This Month)** and **Charging Cost (Last Session)** — once a
  price source is set.
- **Charging Cost per 100 km** — additionally needs the odometer (Vehicle status
  cluster) and two sessions to measure a distance between.

## Grid energy vs. battery energy

Energy is measured **at the battery**, so it's slightly below what the grid
delivered. Two ways to reconcile:

- **Wallbox sensor** — select your wallbox energy sensor
  (`grid_energy_entity`) and that exact grid figure is used instead.
- **Loss percentage** — otherwise gross the battery figure up by your charging
  losses (`charging_loss_percent`). It stays at **0 %** by default, because an
  invented correction would look like a measurement.

A session charged while the price was briefly unknown is flagged `partial`
rather than silently understated.

## Importing past charges from BMW

The **`bavariandata.fetch_charging_history`** service pulls BMW's own recorded
sessions and imports them into the local history, so charges from before you
installed the integration appear on the card and in the monthly summaries.
Overlapping live-recorded sessions are enriched in place with BMW's **measured
grid energy**. This one **does** spend API quota (one or more of your 50/24 h).

## Reading it back

- The [`view: charging` card](The-Dashboard-Card#charging-history-view-charging).
- **`bavariandata.get_charging_sessions`** returns the full history as service
  response data (optional `vin`, `from`, `to`, `limit`) — no quota.
- [Export](Feature-Export) to CSV or a printable report.
- [Long-term statistics](Feature-Energy-and-Statistics) on the Energy dashboard.
