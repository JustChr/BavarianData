# Battery health

> 🇩🇪 [Deutsch](DE-Feature-Battery-Health)

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

## What counts as a sample

Not every charge can say something about capacity. A session qualifies only if
**all** of these hold:

- **It spans at least 25 % of SoC.** BMW streams SoC as a whole percent, so a
  narrow charge divides a small energy figure by a small, coarsely-rounded SoC
  delta. Measured on a real car against BMW's own `maxEnergy` of 78 kWh: a 21 %
  charge implied 77 kWh (0.8 % out), a 12 % charge 82 kWh (5 %), and a 9 % charge
  **123 kWh** (58 %). The error is modest above ~20 % and explodes below ~15 %.
- **It carries battery-side energy.** Sessions imported from BMW's charging
  history hold only a *grid* figure, which includes charging losses and would
  overstate the pack. They are excluded however wide their SoC span — so the
  first charges after an install, which are usually imported, don't count.
- **It caught the whole charge.** If the car was already charging when the
  integration noticed — a restart mid-charge, or a status transition the stream
  never sent — both the energy and the SoC span start late by *different*
  amounts, and dividing one by the other is meaningless. Such a session is
  flagged `late_start` and skipped. Its energy and cost still count everywhere
  else, as a floor.
- **It ran end to end without an interruption.** A charge that Home Assistant
  restarted in the middle of keeps its full SoC span but has a hole in its
  energy, filled from an estimate that already assumes a pack size — so
  measuring capacity with it would measure the assumption. Such a session is
  flagged `interrupted` and skipped too.

## Learning mode

It reads **`Learning (n/10)`** until it has enough good samples **and** the
estimate agrees with BMW's own capacity figure. A suspicious number is
**withheld rather than shown** — a health figure that jumped around would be
worse than an honest "not sure yet."

To reach a confident number faster, do a few **wide-range charges** (a large SoC
swing in one session) rather than many small top-ups.

> **If it sits at `Learning (0/10)`**, your charging pattern is probably the
> reason: topping up little and often from a half-full pack never produces a
> qualifying charge. Running the battery lower and charging it back in one go —
> even occasionally — is what moves the counter. Meanwhile BMW's own
> **State of health (SOCE)** sensor gives you a figure directly, if your car
> streams it. Note also that the vs-new percentage needs BMW's `batterySizeMax`,
> which some cars report as `0` (invalid); where that happens the percentage
> stays blank however many samples accumulate.

## Viewing it

Use the [`view: health` card](The-Dashboard-Card#battery-health-view-health): a
gauge (percentage of the as-new pack) with the capacity-vs-mileage trend below.
