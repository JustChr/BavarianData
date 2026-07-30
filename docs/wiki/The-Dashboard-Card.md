# The dashboard card

A custom **BavarianData Card** is bundled and registered automatically as a
dashboard resource — there is nothing to add by hand, and the registration is
refreshed on every update so browsers pick up the new version. Pick the card
from the card gallery to open a visual editor, or write YAML directly.

> Automatic registration needs storage-mode resources, which is the default. If
> your dashboard resources are YAML-managed you must add the resource yourself —
> see [Troubleshooting](Troubleshooting-and-FAQ#config-error-after-reload).

With the integration installed, the minimal config auto-discovers the car:

```yaml
type: custom:bavariandata-card
```

The card has several **views**. The default is the Overview; set `view:` or
`cluster:` to switch. Use **one card per view** — add several cards to a
dashboard to show them side by side.

> If the card doesn't show up after an update, **hard-refresh the browser**.

- [Overview](#overview)
- [Charging history](#charging-history-view-charging)
- [Battery health](#battery-health-view-health)
- [Trips / driving journal](#trips--driving-journal-view-trips)
- [Trip map](#trip-map-view-map)
- [Tires](#tires-cluster-tire)
- [Security & closures](#security--closures-cluster-closures)
- [Single-cluster list](#single-cluster-list)
- [Full YAML reference](#full-yaml-reference)

---

## Overview

The vehicle render, a state-of-charge ring (blue while charging), remaining
range, charging status, and a grid of key metrics.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-car.png" alt="Overview card showing a BMW i5 with charge level, range, charging status and odometer" width="360" />
</p>

```yaml
type: custom:bavariandata-card
```

While a drive is under way, a **Trip in progress** badge appears at the bottom of
the vehicle image with the distance and minutes so far; tap it for the full
attributes. It follows the *Trip in Progress* entity, so it lingers for a few
minutes after you arrive — [why](Feature-Trips#seeing-the-drive-thats-happening-now).

Pin a specific vehicle with `device:` (device id) or `vin:`.

---

## Charging history (`view: charging`)

Lists recorded charging sessions, newest first, each showing the date, energy,
cost and a Home/Away badge. Tap a session to expand its **power curve**, peak and
average power, duration, and grid energy. The "this month" totals ride along the
top. **CSV** and **Report** buttons export the current month
([see Export](Feature-Export)).

```yaml
type: custom:bavariandata-card
view: charging
```

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-charging.png" alt="Charging history card: recorded sessions with date, SoC change, energy and a Home badge, and a this-month total across the top" width="360" />
</p>

It reads the integration's stored history via the `get_charging_sessions`
service, so it spends **no API quota**. Cost only appears once a price source is
set under **Configure → Charging costs & history**
([see Charging history & cost](Feature-Charging-History-and-Cost)); until then
sessions still list with their energy. A session charged without GPS is badged
*Home · assumed*, and one priced while the tariff was briefly unknown is tagged
*partial price*.

---

## Battery health (`view: health`)

Shows the learned usable battery capacity as a gauge (percentage of the as-new
pack) with a capacity-vs-mileage trend below.

```yaml
type: custom:bavariandata-card
view: health
```

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-health.png" alt="Battery health card showing a Learning (0/10) state while it gathers wide-range charges" width="420" />
</p>

It reads the **Battery Health** sensor, so it spends **no API quota**. Until
there are enough wide-range charges to be sure of the number, it shows
*Learning (n/10)* rather than a figure that would jump around
([how it's learned](Feature-Battery-Health)).

---

## Trips / driving journal (`view: trips`)

Lists your recorded drives, newest first, each showing *from → to*, distance,
duration and a business/private/commute badge; tap one for consumption,
recuperation and the SoC used, to reclassify it, and — for drives recorded with
**Record route** on — a small map of the route (drawn as a clean line with a
start and end marker; nothing leaves your browser to draw it). Above the list a **month in
review** sums the distance (with a vs-last-month delta), the
business/private/commute split, average consumption, energy recuperated, a
driving-style score and your top destinations — and, once a tariff is set, an
estimated driving cost. **CSV** and **Report** buttons export the current month.

A drive still under way **leads the list**, marked with a live badge and an accent
edge: where you set off from, the distance and time so far, and — with **Record
route** on — the route as it grows. It carries no classification buttons, because
there is no stored trip to classify until it ends
([details](Feature-Trips#seeing-the-drive-thats-happening-now)).

```yaml
type: custom:bavariandata-card
view: trips
```

<!-- screenshot: card-trips -->

It reads the `get_trips` and `get_driving_summary` services, so it spends **no
API quota**. Trips are reconstructed from the stream — no configuration needed.
Endpoints are stored as **place names**, never coordinates. Set a **work zone**
under **Configure → Trips** so home↔work drives are recognised as commutes
([see Trips](Feature-Trips)).

> This is a trip journal and expense helper — **not a tax-office-compliant
> logbook** (*kein Finanzamt-konformes Fahrtenbuch*): it has no legal
> tamper-resistance.

---

## Trip map (`view: map`)

A **destinations map**: it plots where your trips **end**, and those markers
**cluster into counted bubbles when you zoom out and split apart as you zoom in** —
a quick read on where you go most. Only the end of each trip is shown (a trip's
start is the previous trip's end, so plotting both would double-count), giving an
honest "times arrived here" count. Click a cluster to zoom into it. A chip row
switches the time window — **This month** (default), **3 months** or **All**.

To see the *route* of a particular drive, open the [Trips](#trips--driving-journal-view-trips)
view and expand that trip — its route is drawn on a small map there.

```yaml
type: custom:bavariandata-card
view: map
```

<!-- screenshot: card-map -->

The map only has something to show once you turn on **Record route** under
**Configure → Trips** — that opt-in setting is what stores each drive's GPS
coordinates (it is the only place the integration keeps raw coordinates on disk,
and it is **off by default**). With it off, or before your first drive with it on,
the view explains that no places have been recorded yet. Drives recorded *before*
you enabled it have no coordinates to place.

It reads endpoints through the `get_trips` service, so it spends **no API quota**,
and it reuses Home Assistant's own map component and marker clustering (map tiles
load from OpenStreetMap, as they do for the built-in Map card).

> **Privacy:** unlike the rest of the history layer — which stores place *names*,
> never coordinates — this view uses the recorded coordinates of your trip
> endpoints, home included. Only enable route recording if you're comfortable with
> that, and remember the map is visible to anyone who can see the dashboard.

---

## Tires (`cluster: tire`)

Draws a top-down car with each tire coloured by condition, a summary of the whole
set at the top, and each wheel's own readings and fitment beside it.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Tires card showing the pressure and wear summary above a top-down car with per-wheel size, tread and remaining mileage" width="300" />
</p>

```yaml
type: custom:bavariandata-card
cluster: tire
```

**The summary** at the top holds the two things that can be wrong with a tire,
side by side: **Pressure** (the measured spread across the set, with the shared
target underneath) and **Wear** (BMW's verdict, with the mileage until the
soonest wheel is due). The badge in the header is the combined verdict for the
whole car.

**Pressure** comes from the stream, judged against each wheel's own target. The
band is deliberately lopsided — low from 8% under target, high only past 15%
over — because the target is a *cold* pressure and a tire you have just driven on
reads 8–10% high without anything being wrong.

**Wear** comes from BMW's smart-maintenance tyre diagnosis, refreshed by the
[daily refresh](Feature-API-Quota#the-daily-refresh). When it is available, each
wheel also shows its own size, tread pattern, season, fitting date and the
mileage until a change is due. These are per wheel on purpose: staggered setups
(different sizes front and rear) are normal, and a single line under the diagram
would have to pick one to show.

Wear outranks pressure in the colour and the header: a tyre BMW flags as worn
reads *"Check tyres"* even at perfect pressure, because pressure is trivially
fixable and tread is not. If your car has no tyre service record on file BMW
returns nothing here, the wear parts are simply absent, and the card shows
pressure alone.

On a narrow dashboard column the car diagram drops out and the four wheels fall
back to a 2×2 grid, front row over rear.

---

## Security & closures (`cluster: closures`)

Shows doors, windows, hood, trunk, sunroof, the central lock and the anti-theft
alarm on the same car diagram. Open doors highlight red, open windows/sunroof
amber, and a central padlock reflects the lock state; a badge summarises the
worst-case status and every part taps through to the underlying entity. Parts
the vehicle doesn't report are simply omitted.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-security.png" alt="Security and closures card with a top-down car diagram, anti-theft alarm armed and all closures closed" width="300" />
</p>

```yaml
type: custom:bavariandata-card
cluster: closures
```

---

## Single-cluster list

Set `cluster:` to list every value in one catalogue cluster. Use one card per
cluster:

```yaml
type: custom:bavariandata-card
cluster: electric   # electric · status · tire · usage · events · basic · contract · metadata · other
```

The card groups entities via their `cluster`/`category` **attributes**, not their
names — so it works regardless of the user's Home Assistant language.

---

## Full YAML reference

| Key | Purpose |
| --- | --- |
| `type` | Always `custom:bavariandata-card`. |
| `view` | `charging`, `trips`, `map`, or `health`. Omit for the Overview. |
| `cluster` | `electric`, `status`, `tire`, `usage`, `events`, `basic`, `contract`, `metadata`, `other`, `closures`. Renders a single-cluster list (or the special tire/closures diagrams). |
| `device` | Device id, to pin a specific vehicle. |
| `vin` | VIN, as an alternative to `device`. |
| `title` | Override the card title entity. |
| `image` | Override the vehicle-image entity. |
| `soc` | Override the state-of-charge entity. |
| `range` | Override the range entity. |
| `charging` | Override the charging-status entity. |
| `target_soc` | Override the target-SoC entity. |
| `time_to_full` | Override the time-to-full entity. |
| `odometer` | Override the odometer entity. |
| `plug` | Override the plug-status entity. |

With the integration installed, entity overrides are rarely needed — the card
auto-discovers them from the vehicle's device. Use them only if you've renamed
entities or want to point the card at a helper.
