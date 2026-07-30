# Trips / driving journal

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

## The monthly sensor and summaries

- One **Driving Distance (This Month)** sensor per vehicle carries the monthly
  total and the business/private/commute split.
- The detail lives in the services (and the card), not a flood of entities:
  - **`bavariandata.get_trips`** — recorded trips as response data.
  - **`bavariandata.get_driving_summary`** — the "month in review": distance
    (vs last month), the split, consumption, recuperation, a driving-style
    score, top destinations, and (with a tariff) an estimated driving cost.

## Viewing & exporting

- The [`view: trips` card](The-Dashboard-Card#trips--driving-journal-view-trips).
- [Export](Feature-Export) to CSV or a printable report.
