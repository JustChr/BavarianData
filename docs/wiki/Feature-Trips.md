# Trips / driving journal

> 🇩🇪 [Deutsch](DE-Feature-Trips)

Every drive is reconstructed from the stream and kept: distance, duration,
start/end places, SoC used, and BMW's own per-trip consumption, recuperation and
driving-style figures. No REST quota.

> **Not a tax-office-compliant logbook** (*kein Finanzamt-konformes
> Fahrtenbuch*). This is a trip journal and expense helper; it has no legal
> tamper-resistance.

## How trips are detected

Trips are reconstructed from **live GPS** in the stream (the integration tracks
distance along the GPS track), so no configuration is needed. Make sure the
**Electric vehicle** and location descriptors are enabled in
[step 4](Getting-Started-4-Choose-Data).

### The times on a trip

A drive is *noticed* later than it starts and *closed* later than it ends — BMW's
position stream can go quiet for minutes at a time, and a trip only closes once
the car has sat still for five. The recorded start and end are corrected for
that, so they read as driving times rather than detection times:

- **Start** — when the car was last seen parked, if your car streams its driver
  door (the door closing means the driver just got in) and it closed within the
  last five minutes. Otherwise the first position fix that showed movement.
- **End** — the last position fix that showed movement, not the moment the
  five-minute stop timer expired. If the driver door opening ends the drive
  instead, that is the arrival.

Both are still estimates bounded by how often your car reports its position: a
long gap in the stream is a long gap in what can be known.

### When the position stream goes quiet

"No movement seen for five minutes" has two very different causes: the car
stopped, or the stream did. Only the first ends a drive — closing on the second
used to split one drive through a tunnel or a coverage hole into two trips.

So a stop is only acted on once something **confirms** it: a position report that
arrived showing the car standing still, or an explicit "not moving" from cars that
send one. With neither, the drive is held open until the reports come back and
settle it — if the car has moved on, it was one drive all along; if it reappears
where it vanished, it really was parked and the trip ends back at the last
movement. A stream that never comes back closes the trip after half an hour, and
because the end is backdated either way, a late close costs no accuracy.

## Seeing the drive that's happening now

A drive in progress is not in the journal yet — it has no end, no final distance
and nothing to classify — so it is surfaced separately, live:

- A **Trip in Progress** binary sensor per vehicle: `on` while a drive is under
  way, with the trip so far as attributes — `started`, `start_location`,
  `distance_km`, `duration_s`, `soc_start`, `soc_now`, `energy_kwh`,
  `last_movement` and `held`.
- On the card: a **badge on the overview** (distance and minutes so far, tap it
  for the attributes) and a **live row at the top of the trips view** — where you
  came from, the distance and time so far, and the route so far on a map when
  [route recording](#recording-the-route-opt-in) is on.
- In `bavariandata.get_trips`: an `open_trips` list alongside the recorded ones.

Two things to know before you automate on it:

- **It is not a "car is moving" sensor**, which is why it isn't named one. It
  tells you a *trip is open*. A trip opens on the first position report that shows
  movement and closes five minutes after the last one — longer while the stream is
  quiet (see [above](#when-the-position-stream-goes-quiet)), which is what parking
  underground looks like. So it stays `on` for some minutes after you arrive, and
  up to half an hour if the car parked somewhere with no reception. The recorded
  trip's end is backdated correctly regardless; it is only the live flag that
  lingers. Use the `last_movement` attribute if you need the finer question.
- **The figures are provisional.** Distance comes from the odometer, which only
  ticks in whole kilometres, so it reads `unknown` for the first minute or so of a
  drive and then falls behind the GPS track a little. The final record is
  computed at close from the better of the available sources.
- Nothing survives a restart: an in-flight trip lives only in memory, so
  restarting Home Assistant mid-drive ends the trip and the flag goes `off`.

## Privacy by default

Endpoints are stored as **place names, never coordinates**:

- A point inside a Home Assistant **zone** shows that zone's name.
- A point outside any zone is stored as an address **only if** you enable
  reverse geocoding under **Configure → Trips** (`trip_geocode`). That sends the
  endpoint coordinates to OpenStreetMap's Nominatim; the resulting address
  string is stored, and the coordinates themselves are never persisted.

## Recording the route (opt-in)

By default a trip keeps only its named endpoints — enough for the journal, but
not enough to draw a map. Turn on **Record route** under **Configure → Trips**
(`trip_track`) and each new trip additionally stores its **GPS track**: the
polyline of coordinates along the drive, each stamped with its time, so a map can
show where the car went **and** replay the drive.

- This is the **only** setting that writes raw coordinates to disk — including
  your exact start and end points — which is why it is off by default and
  independent of address resolution.
- It takes effect on the **next** trip that starts; trips already recorded keep
  whatever they were captured with, and an in-progress drive keeps the setting
  it began with.
- The track rides the trip record and is returned by
  **`bavariandata.get_trips`** (as a `track` list of points). Each point is
  `[lat, lon, t]`, where `t` is whole **seconds since the trip started** — so a
  map can animate the route in real time and colour it by pace. Routes recorded
  before this was added store two-element `[lat, lon]` points and read back
  without timing; there is no way to backfill their times. The track is never
  included in the CSV / printable [export](Feature-Export), which stays
  place-names-only.
- The track is bounded and lightly downsampled, so even a long multi-hour drive
  stays a compact route rather than an unbounded stream of fixes. A stop shows up
  as a single point whose gap to the next stamp records how long the car sat.

Once routes are recorded, the dashboard card's **Trip map** view (`view: map`)
draws them on a map, coloured by classification and filterable by time window
([see the card](The-Dashboard-Card#trip-map-view-map)).

## Classification

Drives auto-classify as **business**, **private** or **commute**:

- Set a **work zone** under **Configure → Trips** (`trip_work_zone`) so
  home↔work drives are recognised as commutes.
- Everything else is filed as your **Default type** (`trip_default_class`) —
  **Private** out of the box. Choose **Business** if that is the honest default
  for your driving, or **Leave unclassified** to sort every trip by hand.
- Correct any guess with **`bavariandata.set_trip_class`** or the tap-to-edit
  control on the [trips card](The-Dashboard-Card#trips--driving-journal-view-trips).

Automatic classification is only ever a **starting point**: a trip you classified
yourself is never overwritten, and changing these settings does not touch trips
that are already recorded.

### A commute with a stop on the way

Buying groceries between home and work parks the car long enough that the
detector records **two** drives, neither of which is home→work on its own. The
**Commute stop tolerance** (`trip_commute_gap`, default **30 minutes**) covers
that: if the car stands no longer than this between one drive ending and the next
beginning, the drives form a *chain*, and a chain that runs from one commute zone
to the other counts as commuting — all of its legs, retroactively. The stops stay
visible as separate trips in the journal; they are just all badged commute.

```
Home ──12 min──▶ Supermarket ──[22 min stop]──▶ Work     both legs = commute
Home ──8 min───▶ Bakery ──────[15 min stop]───▶ Home     both legs = private
Work ──5 min───▶ Lunch ───────[40 min stop]───▶ Work     both legs = private
```

Details worth knowing:

- A chain **ends when it reaches home or work**, so a lunch run out of the office
  and back doesn't get pulled into the morning commute.
- Both **ends** of the chain are checked, not any endpoint — a round trip that
  starts and finishes at home stays private, however many stops it had.
- Up to **five** drives may form one chain. A longer string of short hops is a day
  of running around, so it keeps the default type.
- Stops shorter than about **five minutes** never split a drive in the first
  place, so this setting governs the band above that. Set it to **0** to switch
  chaining off entirely.

## How consumption is measured

BMW streams the state of charge as a **whole percent**. On a 78 kWh pack that
makes one step worth about 0.8 kWh — a rounding error over a 30 km commute, and
the entire measurement over a 1 km hop. There is nothing finer to reach for: the
only other energy value in the stream (`smeEnergyDeltaFullyCharged`) is the same
quantity rounded to whole kWh, and BMW's own per-trip consumption field
(`energyConsumptionComfort`) is not sent by every car — the i5, for one, never
sends it. So BavarianData shows two figures, and refuses a third.

**The headline: an energy balance.** Measured from the charging ledger alone —
two charging sessions bracket a window, each carrying an odometer reading and an
SoC taken when it ended, so the distance covered and the energy delivered between
them are both known without any drive having to have been detected.

```
used     = energy charged between the two readings
           − (SoC at the close − SoC at the open) × capacity
distance = odometer at the close − odometer at the open
```

Charged energy is integrated from streamed charging power, not read off a
quantised SoC, so this figure isn't limited by the one-percent resolution. And
because it never reads the trip record, it stays right in a month where a drive
was missed (a dead stream, an upgrade, an underground garage). It covers first
charge to last charge, which is usually a little shorter than the calendar month.

**Which side of the charger it describes depends on your setup**, and the card
says which:

- **At the battery** — the default. BMW streams *battery* charging power, so the
  energy that goes into this sum is what reached the pack, not what left the
  wall. This is directly comparable with the car's own consumption display.
- **At the plug** — only once every charge in the window carries a *measured*
  grid figure, from a wallbox energy entity you've bound or from BMW's own
  charging-history import. This one **includes charging losses**, so it reads
  above the car's display and is what the electricity actually cost. Only then
  does the card also show the battery-side figure beneath it, with the gap
  between them labelled as the charging loss — because only then are the two
  measuring genuinely different things.

**The battery-side trip figure.** Total trip energy over total trip distance,
also comparable with the car's display. It is a distance-weighted total, *not* an
average of the per-trip figures: averaging ratios lets a 2 km hop outvote a
200 km run, which inflates the result badly. It is the same quantity the
battery-side balance measures, only measured through trip detection and SoC
deltas — which is why the balance leads and this appears alongside it only when
the balance is grid-side.

**Per-trip consumption is withheld below a 3 % SoC drop.** Under that, the
quantisation is the measurement — a 1 km drive that happens to tick one percent
would read as ~78 kWh/100 km. Such trips show their distance, duration and
energy as usual but no consumption figure, and they can be neither the "best" nor
the "worst" trip of the month. On a typical month of short errands and long
commutes this means only the longer drives carry a rate; that is the honest
outcome, not a gap.

**Plug-in hybrids get no per-trip consumption rate.** A trip's energy is the drop
in battery charge, but a hybrid may have driven part of the distance on fuel, and
the stream carries no electric-only distance to divide by. A 40 km run that used
4 kWh and a litre of petrol would read 10 kWh/100 km — a figure no part of the car
achieved. So hybrid trips keep their energy and leave the rate blank, and they
don't count toward the battery-side average.

> **Recuperation** is likewise shown in **kWh/100 km**, not kWh: BMW's
> `recuperationTotal` is documented as an average per 100 km, so a month is a
> distance-weighted mean of it, never a sum.

## The monthly sensor and summaries

- One **Driving Distance (This Month)** sensor per vehicle carries the monthly
  total and the business/private/commute split.
- One **Trip in Progress** binary sensor per vehicle for the drive happening now
  — see [above](#seeing-the-drive-thats-happening-now).
- The detail lives in the services (and the card), not a flood of entities:
  - **`bavariandata.get_trips`** — recorded trips as response data, plus
    `open_trips` for a drive still under way.
  - **`bavariandata.get_driving_summary`** — the "month in review": distance
    (vs last month), the split, consumption, recuperation, a driving-style
    score, top destinations, and (with a tariff) an estimated driving cost.
    Consumption arrives as two keys — `energy_balance` (plug-side, with the
    window and the inputs it was derived from) and
    `avg_consumption_kwh_per_100km` (battery-side); see
    [how consumption is measured](#how-consumption-is-measured). Either can be
    absent when its inputs don't support a figure.

## Viewing & exporting

- The [`view: trips` card](The-Dashboard-Card#trips--driving-journal-view-trips).
- [Export](Feature-Export) to CSV or a printable report.
