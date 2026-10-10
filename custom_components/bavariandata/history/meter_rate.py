"""How the wallbox meter's kilowatt-hours turn into this car's state of charge.

Home Assistant-free (see ``models.py``). The SoC estimate can follow the bound
wallbox meter instead of BMW's sparse charging power (see ``soc_tracking``), and
for that it needs a model of what reaches the pack. It is read off the ledger --
every home charge already carries the meter's ``grid_kwh``, the SoC it gained
and how long it ran -- rather than learned by a running average, so there is
nothing to persist, nothing to reset, and a new wallbox simply takes over as its
sessions come in.

The model is physics, two numbers::

    points gained = percent_per_kwh x (kWh from the meter - overhead_kw x hours)

``percent_per_kwh`` is the charger's efficiency over the pack's size -- points,
not an efficiency, because the capacity would come from the same sessions' SoC
span and cancel; asking for it would only let a wrong ``maxEnergy`` back in.
``overhead_kw`` is what the car draws while charging that never reaches the
pack: the onboard charger's standby, the 12 V system, battery management. It is
why a 2 kW solar-surplus charge is visibly less efficient than an 11 kW one.
Measured on the maintainer's i5 (2026-10-10): 0.30 kW, and with it a model
fitted on single-phase solar charges alone predicted a three-phase 11 kW charge
of 72 points to within 0.8 %. The phase count needed no term of its own there.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

from .models import ChargingSession
from .sessions import meter_counts_this_session

# A session must gain this much SoC to teach. BMW streams whole percent, so a
# small span is noisy -- but the fit weighs every session by its size, so the
# many modest solar charges a PV home produces still count without one
# five-point top-up being able to swing the result. At twenty (the first
# design) the maintainer's ledger held two usable charges in three months.
MIN_SOC_DELTA = 5.0
# Sessions needed before the meter drives the estimate at all (the user's call,
# 2026-10-10).
MIN_SESSIONS = 3
# The newest sessions only, so a new wallbox, a new car on the same account or
# an ageing pack shows up within a couple of weeks.
WINDOW = 10
# Overhead only accrues while energy flows: a solar charge that idles for an
# hour at zero would otherwise be charged an hour of overhead it never drew.
# Time is counted as at most ``kWh / MIN_FLOW_KW`` -- the lowest current a
# wallbox can deliver (6 A on one phase) is about 1.4 kW, so 1 kW is safe.
MIN_FLOW_KW = 1.0
# What a fitted overhead may plausibly be. Beyond it the sessions are not
# telling efficiency and overhead apart (they all ran at one power, so the two
# are interchangeable), and the plain ratio is the honest answer.
MAX_OVERHEAD_KW = 1.0
# A session this far off the first fit is not describing this charger -- BMW's
# SoC near the top of the pack, a stale start reading -- and is left out of the
# second. Both bounds must be exceeded, so a large charge's ordinary noise and a
# small charge's quantisation are never mistaken for it.
OUTLIER_POINTS = 2.5
OUTLIER_SHARE = 0.15
# Physics bounds on a single session's points per kWh. Anything outside is not
# a measurement of this car on this meter.
MIN_PERCENT_PER_KWH = 0.4  # 95 % into a 240 kWh pack -- nothing that big exists
MAX_PERCENT_PER_KWH = 8.0  # 100 % into a 12.5 kWh plug-in hybrid pack


@dataclass(frozen=True)
class MeterRate:
    """The learned model and how many sessions it rests on.

    ``fitted`` is False when the sessions could not separate overhead from
    efficiency; ``percent_per_kwh`` is then their median ratio and
    ``overhead_kw`` zero, which is exact for charges at the power they ran at.
    """

    percent_per_kwh: float
    overhead_kw: float
    samples: int
    fitted: bool

    def points(self, kwh: float, hours: float) -> float:
        """Points gained for ``kwh`` from the meter over ``hours``; never negative."""

        if kwh <= 0:
            return 0.0
        flowing = min(max(hours, 0.0), kwh / MIN_FLOW_KW)
        return max(0.0, self.percent_per_kwh * (kwh - self.overhead_kw * flowing))


@dataclass(frozen=True)
class _Sample:
    start: object
    points: float
    kwh: float
    hours: float


def meter_rate(
    sessions: Iterable[ChargingSession], home_zone: Optional[str]
) -> Optional[MeterRate]:
    """The model from the newest qualifying home charges, or ``None``.

    A session qualifies only when its two ends and its meter delta describe the
    same stretch of this car charging on this wallbox: closed, not
    ``late_start``, with a ``grid_kwh`` this meter measured (``grid_source``;
    BMW's charging history is another instrument, and an old enriched record
    may carry either), a gain of at least :data:`MIN_SOC_DELTA`, and a place
    the meter could have measured (:func:`meter_counts_this_session`).
    ``interrupted`` sessions do qualify: a restart leaves a hole in the
    battery-side integration, but the meter is a running total, so its delta
    from start to end is whole.
    """

    samples: list[_Sample] = []
    for session in sessions:
        if session.end is None or session.late_start or session.grid_source != "meter":
            continue
        kwh = session.grid_kwh
        delta = session.soc_delta
        if kwh is None or kwh <= 0 or delta is None or delta < MIN_SOC_DELTA:
            continue
        if not meter_counts_this_session(session.location, home_zone):
            continue
        if not MIN_PERCENT_PER_KWH <= delta / kwh <= MAX_PERCENT_PER_KWH:
            continue
        hours = (session.end - session.start).total_seconds() / 3600.0
        samples.append(_Sample(session.start, delta, kwh, min(hours, kwh / MIN_FLOW_KW)))
    if len(samples) < MIN_SESSIONS:
        return None
    samples.sort(key=lambda sample: sample.start)
    newest = samples[-WINDOW:]

    model = _fit(newest)
    if model is not None:
        kept = [s for s in newest if not _outlier(s, model)]
        if MIN_SESSIONS <= len(kept) < len(newest):
            newest = kept
            model = _fit(newest)
    if model is not None:
        return MeterRate(
            percent_per_kwh=round(model[0], 4),
            overhead_kw=round(model[1], 3),
            samples=len(newest),
            fitted=True,
        )
    ratio = median(s.points / s.kwh for s in newest)
    return MeterRate(
        percent_per_kwh=round(ratio, 4), overhead_kw=0.0, samples=len(newest), fitted=False
    )


def _fit(samples: list[_Sample]) -> Optional[tuple[float, float]]:
    """Least squares for ``points = a*kWh - b*hours``; ``(a, b/a)`` or ``None``.

    No intercept: no energy, no points. ``None`` when the sessions cannot tell
    the two terms apart -- every charge at one power makes kWh and hours
    proportional -- or when the answer is not physical.
    """

    sgg = sum(s.kwh * s.kwh for s in samples)
    shh = sum(s.hours * s.hours for s in samples)
    sgh = sum(s.kwh * s.hours for s in samples)
    sgy = sum(s.kwh * s.points for s in samples)
    shy = sum(s.hours * s.points for s in samples)
    det = sgg * shh - sgh * sgh
    # Relative to the scale: at det ~ 0 the sessions are collinear.
    if sgg <= 0 or shh <= 0 or det <= 0.01 * sgg * shh:
        return None
    a = (sgy * shh - shy * sgh) / det
    b = (sgh * sgy - sgg * shy) / det
    if a <= 0:
        return None
    overhead = b / a
    if not 0.0 <= overhead <= MAX_OVERHEAD_KW:
        return None
    return a, overhead


def _outlier(sample: _Sample, model: tuple[float, float]) -> bool:
    a, overhead = model
    error = abs(a * (sample.kwh - overhead * sample.hours) - sample.points)
    return error > OUTLIER_POINTS and error > OUTLIER_SHARE * sample.points
