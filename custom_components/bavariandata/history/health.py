"""Battery-health maths derived from recorded charging sessions.

Home Assistant-free (see ``models.py``) so the estimate can be unit-tested
without an HA install -- and battery health is precisely where a quietly-wrong
number does the most damage: EV owners watch state-of-health obsessively, and a
figure that jumps around erodes trust in the whole integration. Every path here
either returns a trustworthy number or admits it does not have one yet.

The estimate is deliberately simple and robust rather than clever. A charge that
adds energy ``E`` while raising the state of charge by ``ΔSoC`` implies a usable
capacity of ``E / (ΔSoC / 100)``. Averaging that across many charges cancels the
per-session noise; taking the *median* rather than the mean stops a single
weird session (a mid-charge unplug, a bad SoC reading) from dragging it.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

from .models import ChargingSession

# A charge has to span a wide SoC range before it says anything reliable about
# capacity: a 5%% top-up divides a small energy figure by a small SoC delta and
# amplifies every rounding error and every bit of measurement noise. Only
# whole-battery-ish charges are trusted as capacity samples.
MIN_SOC_DELTA = 40.0

# Below this many qualifying charges the sensor reads "Learning (n/10)" instead
# of a number, per the roadmap's rule 4 (never show a value we aren't sure of).
MIN_SAMPLES = 10

# How far our estimate may sit from BMW's own capacity figure before we distrust
# our own maths and fall back to "Learning". A wild divergence means an input is
# wrong (bad SoC scaling, a partial charge that slipped the ΔSoC filter), and a
# suspicious number is worse than an honest "not yet".
SANITY_TOLERANCE = 0.25


@dataclass
class BatteryHealth:
    """A usable-capacity estimate together with the honesty metadata to show it.

    ``confident`` is the single gate the entity reads: only when it is true may a
    number be presented. ``usable_kwh`` is still populated while learning (so the
    card can show progress) but must not be surfaced as a settled figure until
    ``confident`` flips.
    """

    usable_kwh: Optional[float]
    samples: int
    confident: bool
    nominal_kwh: Optional[float] = None
    vs_new_percent: Optional[float] = None
    suspicious: bool = False

    def as_dict(self) -> dict:
        return {
            "usable_kwh": self.usable_kwh,
            "samples": self.samples,
            "confident": self.confident,
            "nominal_kwh": self.nominal_kwh,
            "vs_new_percent": self.vs_new_percent,
            "suspicious": self.suspicious,
        }


def _capacity_sample(session: ChargingSession) -> Optional[float]:
    """The usable-capacity a single session implies, or ``None`` if it can't.

    Uses the battery-side energy on purpose: capacity is what the pack holds, so
    the grid-side figure (which includes charging losses) would overstate it.
    """

    delta = session.soc_delta
    if delta is None or delta < MIN_SOC_DELTA:
        return None
    energy = session.energy_kwh
    if energy is None or energy <= 0:
        return None
    return energy / (delta / 100.0)


def usable_capacity(
    sessions: Iterable[ChargingSession],
    *,
    nominal_kwh: Optional[float] = None,
    sanity_kwh: Optional[float] = None,
) -> BatteryHealth:
    """Estimate usable pack capacity from wide-SoC charges.

    ``nominal_kwh`` is the battery's as-new size (BMW's ``batterySizeMax``); it
    only sets the vs-new percentage and never gates the estimate. ``sanity_kwh``
    is BMW's own current capacity figure (``maxEnergy``) when available: if our
    estimate diverges from it by more than :data:`SANITY_TOLERANCE` we treat our
    own maths as suspect and stay in "Learning".
    """

    samples = sorted(
        value
        for value in (_capacity_sample(session) for session in sessions)
        if value is not None
    )
    count = len(samples)
    if count == 0:
        return BatteryHealth(
            usable_kwh=None,
            samples=0,
            confident=False,
            nominal_kwh=nominal_kwh,
        )

    estimate = round(median(samples), 1)

    reference = sanity_kwh or nominal_kwh
    suspicious = bool(reference) and abs(estimate - reference) / reference > SANITY_TOLERANCE
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
    sessions: Iterable[ChargingSession], *, limit: Optional[int] = None
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
