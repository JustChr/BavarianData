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
- Correct any guess with **`bavariandata.set_trip_class`** or the tap-to-edit
  control on the [trips card](The-Dashboard-Card#trips--driving-journal-view-trips).

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
