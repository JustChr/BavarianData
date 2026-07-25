# Battery health

The integration learns your EV battery's **usable capacity** from the same
recorded charging sessions used for [charging history](Feature-Charging-History-and-Cost)
— no REST quota, all derived from the stream.

## How it's learned

A **wide-range charge** (say 20 → 80 %) that adds a known number of kWh implies
the capacity of the whole pack: if 30 % of the battery took *X* kWh, the pack is
roughly *X / 0.30*. Averaging across many such charges cancels the noise from
temperature, charging speed and measurement error.

## The Battery Health sensor

Surfaces as one **Battery Health** sensor per EV:

- **State** — usable capacity in kWh.
- **Attributes** — vs-new percentage, sample count, and a capacity-vs-mileage
  trend.

## Learning mode

It reads **`Learning (n/10)`** until it has enough good samples **and** the
estimate agrees with BMW's own capacity figure. A suspicious number is
**withheld rather than shown** — a health figure that jumped around would be
worse than an honest "not sure yet."

To reach a confident number faster, do a few **wide-range charges** (a large SoC
swing in one session) rather than many small top-ups.

## Viewing it

Use the [`view: health` card](The-Dashboard-Card#battery-health-view-health): a
gauge (percentage of the as-new pack) with the capacity-vs-mileage trend below.
