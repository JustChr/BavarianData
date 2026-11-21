"""Battery-health maths derived from recorded charging sessions.

Home Assistant-free (see ``models.py``) so the estimate can be unit-tested
without an HA install -- and battery health is precisely where a quietly-wrong
number does the most damage: EV owners watch state-of-health obsessively, and a
figure that jumps around erodes trust in the whole integration. Every path here
either returns a trustworthy number or admits it does not have one yet.

The estimate is deliberately simple and robust rather than clever. A charge that
adds energy ``E`` while raising the state of charge by ``ΔSoC`` implies a usable
capacity of ``E / (ΔSoC / 100)``. Two things keep that honest:

- The car's own draw while it charges (:data:`OVERHEAD_KW` for every hour power
  flows) is taken off ``E`` first. It never reaches the pack, and it is a fixed
  power, not a share of the charge: a slow solar charge carries far more of it
  per kWh than an 11 kW one. Left in, one i5 charge at 2.6 kW over 13 hours
  implied an 87.7 kWh pack where its 11 kW charges said 76-81.
- The capacity is every qualifying charge's energy over every one's SoC gain,
  pooled -- after dropping any charge whose own figure strays more than
  :data:`OUTLIER_BAND` from the median (a mid-charge unplug, a bad SoC reading).
  Pooling weights a wide charge above a narrow one, as its whole-number SoC
  deserves: one point either way is 4 % of a 25-point charge, 2 % of a 50.

On the i5's twelve charges (2026-11-21) this reads 76.3 kWh against BMW's own
``maxEnergy`` of 76; the old median of per-charge figures read 80.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from statistics import median
from typing import Any
from collections.abc import Iterable

from .models import ChargingSession

# A charge has to span a wide SoC range before it says anything reliable about
# capacity: a 5%% top-up divides a small energy figure by a small SoC delta and
# amplifies every rounding error and every bit of measurement noise. Only
# whole-battery-ish charges are trusted as capacity samples.
#
# 40 %% was set before there was data to calibrate it against, and it turned out
# to be unreachable for anyone who tops up little and often rather than running
# the pack down -- one real car went 39 sessions without a single qualifying
# charge, leaving the sensor at "Learning (0/10)" permanently rather than
# slowly. Measured error against BMW's own ``maxEnergy`` on that car: a 21 %%
# charge implied 77 kWh against BMW's 78 (0.8 %% out), a 12 %% charge 82 kWh
# (5 %%), a 9 %% charge 123 kWh (58 %%). The error is small well below 40 %% and
# explodes below ~15 %%, so the gate sits between them.
MIN_SOC_DELTA = 25.0

# Below this many qualifying charges the sensor reads "Learning (n/10)" instead
# of a number, per the roadmap's rule 4 (never show a value we aren't sure of).
MIN_SAMPLES = 10

# How far our estimate may sit from BMW's own capacity figure before we distrust
# our own maths and fall back to "Learning". A wild divergence means an input is
# wrong (bad SoC scaling, a partial charge that slipped the ΔSoC filter), and a
# suspicious number is worse than an honest "not yet".
SANITY_TOLERANCE = 0.25

# The car's own draw while it charges, in kW: what the wallbox meter counts that
# never reaches the pack (the cooling, the electronics, the charger idling).
# Fitted on the i5's metered home charges by the SoC estimate's model
# (0.30-0.38 kW, 2026-10-10) and again by battery health's own charges
# (0.24 kW, 2026-11-21). An error of 0.1 kW moves an 11 kW charge's figure by
# under 1 %, a 2.6 kW one's by about 4 %.
OVERHEAD_KW = 0.3

# A charge whose own capacity figure strays further than this from the median
# is left out of the pooled estimate (and still drawn on the trend).
OUTLIER_BAND = 0.15


@dataclass
class BatteryHealth:
    """A usable-capacity estimate together with the honesty metadata to show it.

    ``confident`` is the single gate the entity reads: only when it is true may a
    number be presented. ``usable_kwh`` is still populated while learning (so the
    card can show progress) but must not be surfaced as a settled figure until
    ``confident`` flips.
    """

    usable_kwh: float | None
    samples: int
    confident: bool
    nominal_kwh: float | None = None
    vs_new_percent: float | None = None
    suspicious: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "usable_kwh": self.usable_kwh,
            "samples": self.samples,
            "confident": self.confident,
            "nominal_kwh": self.nominal_kwh,
            "vs_new_percent": self.vs_new_percent,
            "suspicious": self.suspicious,
        }


def charging_hours(session: ChargingSession) -> float | None:
    """How long power flowed into the car, from its power curve.

    Not the session's length: a car plugged in at six and charged from one sat
    idle for seven hours, drawing nothing through the wallbox. A curve point
    holds until the next one, the last until the session ended. Without a curve
    (an imported charge, an older record) ``None``: no overhead is taken off.
    """

    curve = session.power_curve
    if not curve:
        return None
    seconds = 0.0
    for (start, power), (end, _next) in pairwise(curve):
        if power > 0:
            seconds += max(0.0, end - start)
    last_at, last_power = curve[-1]
    if last_power > 0 and session.end is not None:
        seconds += max(0.0, (session.end - session.start).total_seconds() - last_at)
    return seconds / 3600


def _stored_kwh(session: ChargingSession) -> float | None:
    """The energy that went into the pack: the charge's, less the car's own draw."""

    energy = session.energy_kwh
    if energy is None or energy <= 0:
        return None
    stored = energy - OVERHEAD_KW * (charging_hours(session) or 0.0)
    return stored if stored > 0 else None


def _capacity_sample(session: ChargingSession) -> float | None:
    """The usable-capacity a single session implies, or ``None`` if it can't.

    Uses ``energy_kwh``, the integration's sum of the car's own streamed
    charging power, never ``grid_kwh``, so an imported session (BMW's grid
    figure only) can never be a capacity sample however wide its SoC span.
    BMW calls that power the battery's, but on the i5 it tracks the wallbox
    meter (56.98 vs 56.85 kWh over a clean 8 -> 80 % charge, 2026-10-18): it is
    measured at the plug, so the estimate carries the charger's losses and
    reads a few percent above the pack.

    A session that opened mid-charge is rejected outright: its energy and its
    SoC span both start late and by *different* amounts, so dividing one by the
    other is meaningless rather than merely noisy -- the case that prompted this
    implied a 123 kWh pack on a 78 kWh car.

    One that was *interrupted* -- a restart mid-charge -- is rejected for a
    sharper reason: its SoC span is complete while its energy has a hole in it,
    and the hole is filled from an estimate that already assumes a capacity
    (``coordinator._apply_gap_credit``). Dividing that by the SoC span would
    measure the assumption, not the pack.
    """

    if session.late_start or session.interrupted:
        return None
    delta = session.soc_delta
    if delta is None or delta < MIN_SOC_DELTA:
        return None
    stored = _stored_kwh(session)
    if stored is None:
        return None
    return stored / (delta / 100.0)


def usable_capacity(
    sessions: Iterable[ChargingSession],
    *,
    nominal_kwh: float | None = None,
    sanity_kwh: float | None = None,
) -> BatteryHealth:
    """Estimate usable pack capacity from wide-SoC charges.

    ``nominal_kwh`` is the battery's as-new size (BMW's ``batterySizeMax``); it
    only sets the vs-new percentage and never gates the estimate. ``sanity_kwh``
    is BMW's own current capacity figure (``maxEnergy``) when available: if our
    estimate diverges from it by more than :data:`SANITY_TOLERANCE` we treat our
    own maths as suspect and stay in "Learning".
    """

    qualifying = [
        (session, value)
        for session, value in ((session, _capacity_sample(session)) for session in sessions)
        if value is not None
    ]
    if not qualifying:
        return BatteryHealth(
            usable_kwh=None,
            samples=0,
            confident=False,
            nominal_kwh=nominal_kwh,
        )

    middle = median(value for _session, value in qualifying)
    kept = [
        session for session, value in qualifying if abs(value - middle) <= OUTLIER_BAND * middle
    ]
    count = len(kept)
    stored = sum(_stored_kwh(session) or 0.0 for session in kept)
    gained = sum(session.soc_delta or 0.0 for session in kept)
    estimate = round(stored / (gained / 100.0), 1)

    reference = sanity_kwh or nominal_kwh
    suspicious = (
        reference is not None
        and reference != 0
        and abs(estimate - reference) / reference > SANITY_TOLERANCE
    )
    confident = count >= MIN_SAMPLES and not suspicious

    vs_new = None
    if nominal_kwh:
        vs_new = round(estimate / nominal_kwh * 100, 1)

    return BatteryHealth(
        usable_kwh=estimate,
        samples=count,
        confident=confident,
        nominal_kwh=nominal_kwh,
        vs_new_percent=vs_new,
        suspicious=suspicious,
    )


def degradation_series(
    sessions: Iterable[ChargingSession], *, limit: int | None = None
) -> list[list[float]]:
    """``[odometer_km, usable_kwh]`` points for a capacity-vs-mileage trend.

    One point per qualifying charge that also carried an odometer reading,
    oldest mileage first. Each point is that charge's own noisy capacity sample,
    not the smoothed estimate -- the card draws the trend through the scatter.
    ``limit`` keeps the most recent points (by mileage) so the series can live in
    a sensor attribute without growing without bound.
    """

    points: list[list[float]] = []
    for session in sessions:
        sample = _capacity_sample(session)
        if sample is None or session.mileage_km is None:
            continue
        points.append([round(session.mileage_km, 1), round(sample, 1)])

    points.sort(key=lambda point: point[0])
    if limit is not None and len(points) > limit:
        points = points[-limit:]
    return points
