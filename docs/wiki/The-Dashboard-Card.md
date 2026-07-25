# The dashboard card

A custom **BMW CarData Card** is bundled and registered automatically — there is
no dashboard resource to add by hand. Pick it from the card gallery to open a
visual editor, or write YAML directly.

With the integration installed, the minimal config auto-discovers the car:

```yaml
type: custom:bmw-cardata-card
```

The card has several **views**. The default is the Overview; set `view:` or
`cluster:` to switch. Use **one card per view** — add several cards to a
dashboard to show them side by side.

> If the card doesn't show up after an update, **hard-refresh the browser**.

- [Overview](#overview)
- [Charging history](#charging-history-view-charging)
- [Battery health](#battery-health-view-health)
- [Trips / driving journal](#trips--driving-journal-view-trips)
- [Tire pressures](#tire-pressures-cluster-tire)
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
type: custom:bmw-cardata-card
```

Pin a specific vehicle with `device:` (device id) or `vin:`.

---

## Charging history (`view: charging`)

Lists recorded charging sessions, newest first, each showing the date, energy,
cost and a Home/Away badge. Tap a session to expand its **power curve**, peak and
average power, duration, and grid energy. The "this month" totals ride along the
top. **CSV** and **Report** buttons export the current month
([see Export](Feature-Export)).

```yaml
type: custom:bmw-cardata-card
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
type: custom:bmw-cardata-card
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
recuperation and the SoC used, and to reclassify it. Above the list a **month in
review** sums the distance (with a vs-last-month delta), the
business/private/commute split, average consumption, energy recuperated, a
driving-style score and your top destinations — and, once a tariff is set, an
estimated driving cost. **CSV** and **Report** buttons export the current month.

```yaml
type: custom:bmw-cardata-card
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

## Tire pressures (`cluster: tire`)

Draws a top-down car with each tire coloured by pressure vs. its target (green
OK, amber high, red low) and the readings beside each wheel.

<p align="center">
  <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/wattfried-tires.png" alt="Tire pressure card flagging slightly high pressures on all four tires" width="300" />
</p>

```yaml
type: custom:bmw-cardata-card
cluster: tire
```

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
type: custom:bmw-cardata-card
cluster: closures
```

---

## Single-cluster list

Set `cluster:` to list every value in one catalogue cluster. Use one card per
cluster:

```yaml
type: custom:bmw-cardata-card
cluster: electric   # electric · status · tire · usage · events · basic · contract · metadata · other
```

The card groups entities via their `cluster`/`category` **attributes**, not their
names — so it works regardless of the user's Home Assistant language.

---

## Full YAML reference

| Key | Purpose |
| --- | --- |
| `type` | Always `custom:bmw-cardata-card`. |
| `view` | `charging`, `trips`, or `health`. Omit for the Overview. |
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
