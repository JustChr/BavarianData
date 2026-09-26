# Charging history & cost

> 🇩🇪 [Deutsch](DE-Feature-Charging-History-and-Cost)

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

### How accurate is it?

The figure is **battery-side** — it's what reached the pack, not what left the
wall, so it sits below your meter by the charging losses. (A true grid figure
only appears as `grid_kwh`, from BMW's charging-history import or a wallbox
entity you bind.)

BMW does **not** sample charging power evenly. It arrives in bursts, sometimes
with more than an hour between them, and the integration has to assume the last
reported power held until the next one. When the readings are dense that is
accurate to within a percent; when the stream goes quiet mid-charge, one
unrepresentative sample can dominate a long stretch.

So the running total is **bounded by what the battery can have absorbed** — the
SoC rise times the pack capacity, plus a small margin for the fact that SoC
arrives a whole percent at a time. It is a ceiling, never a correction: a
session that under-read is left alone, because nothing can tell an under-read
from a genuinely slow charge. The bound applies to the total rather than to each
step, so a charge held back while a SoC reading is pending recovers in full the
moment it lands. Where SoC or capacity is unknown, no bound applies.

The bound needs a SoC reading **taken during the session**. Not every car streams
one: where the only state of charge available comes from the REST snapshot it
never changes between charges, and a rise of zero would bound every session at
the margin alone rather than at what the car took. Such a session is left
unbounded and recorded with no SoC — see [no start/end SoC on a
session](#no-startend-soc-on-a-session) below.

## Recorded history

Every completed session is recorded and kept in the integration's **own store**
rather than the recorder (which purges after ten days). It captures start/end
SoC, energy, duration, peak power, the charging power curve, and cost.

Retention is set by **`history_retain_months`** under
**Configure → Charging costs & history** (0 = keep everything).

### Restarting while the car is charging

A charge that is still running is written to the store as it goes, so restarting
Home Assistant, updating it, or reloading the integration in the middle of one
no longer loses it. When Home Assistant comes back:

- **still charging** — the same session simply carries on, and the energy the
  pack gained while nothing was watching is credited back from the state of
  charge that measured it;
- **charging already over** — the session is recorded ending at the last reading
  actually seen, not at the moment the integration noticed. Whatever flowed
  after that reading is not in the figure, so the record is a *floor*.

Either way the session is flagged **`interrupted`**, which is visible in
`get_charging_sessions` and in an export. Battery health skips such a session
(see [Battery health](Feature-Battery-Health)); energy and cost still count
everywhere else.

Which of the two it was is only known once the car says so, and a car charging
at steady power can stay silent for hours. So if the car has not reported within
two minutes of the restart, the integration **asks BMW** — BMW's servers keep
the last status the car sent, including a stop that happened while Home
Assistant was down. That is the [automatic data refresh](Settings-Reference#automatic-data-refresh)
after every start, and a running charge is checked even when the rest is skipped
as recent. With it switched off the charge waits for the car, and is filed as
ended by the restart if the car stays quiet for 15 minutes. Meanwhile the estimated state of
charge keeps climbing at the rate the charge was running at.

Before v0.9.9 an in-progress charge was dropped outright on a restart, which is
why the odd charge could be missing from the ledger and monthly totals could
read low.

### No start/end SoC on a session

A session shows no SoC when no state-of-charge reading arrived while it ran. That
is the honest answer rather than a missing feature: the last value held may be
days old and belong to a different charge, and reporting it as this one's start
and end would show a flat "38 → 38%" for every charge the car ever makes.

It means your car isn't streaming
`vehicle.drivetrain.batteryManagement.header`, which is BMW's (confusingly named)
high-voltage state of charge.

**If you set up before v0.9.6, re-run Configure → Choose streamed data.** That
descriptor was missing from the old setup snippet, so it was never switched on in
the portal and never reached the stream. The
coverage repair names it once it has been missing long enough.

If it is ticked and the value still never updates, your car doesn't send it and
there is nothing the integration can do about that. Energy, duration, peak power,
the power curve and cost are all unaffected either way, and importing BMW's own
charging history ([Importing past charges from BMW](#importing-past-charges-from-bmw))
fills the SoC back in where BMW recorded it.

Charges recorded **before v0.9.6** show the opposite symptom — a flat "38 → 38%"
and about 1.4 kWh — and can be repaired from BMW's history too:
[Repairing charges from before v0.9.6](#repairing-charges-from-before-v096).

## Where the energy came from

With **Configure → Solar & energy sources** set up, every charge also records
how much of its energy came off your roof, out of your house battery, and off
the grid — in grid-side kWh, the side your house meters measure.

The card shows a **☀ 62 % solar** tag on the session row and the full breakdown
when you expand it; the month's share rides as attributes (`solar_percent`,
`energy_mix`) on the **Charging energy this month** sensor, and the export and
the printed month report both carry the numbers.

### How the split is decided

Energy is attributed **as it is delivered**, from the site's supply mix at that
instant — the same way the price is sampled. A charge that starts in sunshine
and finishes after dark is split between the two, rather than filed under
whichever came last.

The car is treated as **just another load**: it gets the same mix the rest of
the house got at that moment. No convention that gives the car the sunshine
first (which flatters the roof), and none that makes it the marginal load
carrying the grid import (which flatters the grid). Concretely, at an instant
with 4 kW of PV reaching loads, 2 kW out of the battery and 2 kW imported, the
car's energy is booked 50 % PV, 25 % battery, 25 % grid.

Two consequences worth knowing:

- **Exported PV doesn't count**, and PV going *into* the house battery doesn't
  count yet — it is attributed later, as battery, when it comes back out. So the
  same kilowatt-hour is never counted twice.
- **A missing or unavailable sensor means "unattributed"**, not "grid". Such
  energy lands in its own bucket, and a session where nothing could be
  attributed records no mix at all — because "we couldn't tell" and "none of it
  was solar" are different statements. The solar percentage is always a share of
  what *could* be attributed.

### What it costs

Set **Value of own solar per kWh** — usually your feed-in tariff, the money you
give up by not exporting — and a charge's cost becomes what it really cost you.
Leave it empty and solar is billed at your import price, so existing totals keep
their old meaning and only the mix is new information.

House-battery energy is **always** billed at the import price in force at the
time. Its contents could have come from the roof at noon or from a cheap-hour
grid charge at three in the morning, and this integration cannot tell which —
so it takes the conservative figure rather than claim a saving that may never
have existed. The raw kWh per source are stored, so you can always do that
arithmetic differently yourself.

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

- **Wallbox sensor** — select your wallbox's **cumulative** energy sensor
  (`grid_energy_entity`) and that exact grid figure is used instead: it lands on
  the session as a measured `grid_kwh`, feeds the monthly totals, the statistics
  and the export, and is billed **as the charge proceeds**, so a dynamic tariff
  still prices each kilowatt-hour at the rate in force when it arrived. A
  reading that can't be right — a meter that reset, or one reporting far less
  than the pack absorbed or nearly twice it — is refused rather than believed, and
  the session keeps its battery-side figure. Details and the failure cases:
  [evcc & wallbox bridge](Feature-evcc-and-Wallbox-Bridge#inbound-your-wallboxs-meter-for-the-charging-history).
- **Loss percentage** — otherwise gross the battery figure up by your charging
  losses (`charging_loss_percent`). It stays at **0 %** by default, because an
  invented correction would look like a measurement. With a wallbox meter bound
  you don't need it: the loss becomes measured.

A session charged while the price was briefly unknown is flagged `partial`
rather than silently understated.

## Importing past charges from BMW

The **`bavariandata.fetch_charging_history`** service pulls BMW's own recorded
sessions and imports them into the local history, so charges from before you
installed the integration appear on the card and in the monthly summaries.
Overlapping live-recorded sessions are enriched in place with BMW's **measured
grid energy**. This one **does** spend API quota (one or more of your 50/24 h).
Without **From** and **To** it fetches the last 30 days.

### Repairing charges from before v0.9.6

Until v0.9.6 the state of charge never reached the stream, so every charge from
then was stored with the same stale level at both ends ("38 → 38%"). The energy
was held to what a flat level allows: about 2 % of the pack, roughly 1.4 kWh.

Run **Fetch charging history** once with **From** set before your first affected
charge. For each charge that BMW recorded with a rise of **at least 2 percentage
points** but that is stored as flat, the import:

- takes BMW's start and end level,
- adds BMW's measured grid energy, which the card, monthly totals, statistics and
  export then count,
- removes the battery-side kWh and the solar share, since both came from the
  capped figure,
- works the cost out again on a fixed price, or keeps it marked **partial** with
  a live price entity, whose past prices can't be rebuilt.

A charge BMW also recorded as flat — a car pre-heating while full, say — is left
exactly as it is, as is a charge BMW doesn't list. Running the import again
changes nothing further.

Two things stay different from a charge recorded on a fixed build. Those charges
never count toward [battery health](Feature-Battery-Health), which needs
battery-side energy. And [real range](Feature-Efficiency-and-Range) needs the
battery-side kWh of every charge it covers, so it shows nothing rather than a
wrong figure until the repaired charges are older than 30 days.

## Reading it back

- The [`view: charging` card](The-Dashboard-Card#charging-history-view-charging).
- **`bavariandata.get_charging_sessions`** returns the full history as service
  response data (optional `vin`, `from`, `to`, `limit`) — no quota.
- [Export](Feature-Export) to CSV or a printable report.
- [Long-term statistics](Feature-Energy-and-Statistics) on the Energy dashboard.
