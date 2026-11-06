"""A daily odometer log: one reading per local day, per car.

The car's own pace (km a day) answers two questions nothing else in the history
layer can: when a distance-only service item falls due, and how a mileage
allowance is going. Trips cannot answer them -- a trip the builder missed is
distance that never happened -- but the odometer cannot miss anything: whatever
the gaps between readings, the difference between two of them is exactly what
was driven.

The log keeps the **highest** reading of each local day, keyed ``YYYY-MM-DD``.
An odometer never runs backwards, so a reading lower than an earlier day's is a
stale replay (a REST catch-up, a restored value) and is ignored rather than
allowed to fold the curve. The one exception is a reading no car could have
driven to (one off by a unit lands thousands of kilometres ahead): left in, it
would hold every true reading below it out of the log for months, so the next
reading that follows on from the day before it takes its place
(:func:`_spike_days`).

It is tiny -- a number a day -- so it is kept for years regardless of the
charging-history retention: a lease that started three years ago still needs its
start. :data:`MAX_DAYS` is only a backstop.

Home Assistant-free on purpose, so the pace arithmetic is unit-tested;
``store.py`` persists it and the coordinator feeds it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping, MutableMapping, Optional

# Ten years of daily readings.
MAX_DAYS = 3660

# The window the pace is measured over, and how much of it the readings must
# span before a pace is offered at all: a projected date from a week of driving
# (a holiday, a sick week) is worse than none.
PACE_WINDOW_DAYS = 90
PACE_MIN_SPAN_DAYS = 28

# The furthest a car is taken to drive in a day, and the allowance on top for
# the odometer's whole-kilometre steps. Two readings on consecutive days can be
# up to 48 hours apart, so a gap of ``n`` days allows ``n + 1`` days of driving.
# Generous for any real car; the check is there for a reading off by a unit.
MAX_KM_PER_DAY = 2000.0
SLACK_KM = 5.0

OdometerLog = MutableMapping[str, float]


def _drivable(start: date, start_km: float, end: date, end_km: float) -> bool:
    days = (end - start).days + 1
    return end_km - start_km <= SLACK_KM + MAX_KM_PER_DAY * days


def _spike_days(log: Mapping[str, float], day: date, km: float) -> list[str]:
    """The days ``km`` shows to have been a wrong reading; empty when none are.

    Only the newest days can be: those after the last reading at or below
    ``km``, each further from it than a car could drive, while ``km`` itself
    follows on from it. A stale replay never qualifies -- the days it undercuts
    were real steps forward -- and without a reading to measure from (the
    spike was the first) nothing is judged.
    """

    upto = [(stamp, value) for stamp, value in readings(log) if stamp <= day]
    base = next(
        ((stamp, value) for stamp, value in reversed(upto) if value <= km and stamp < day), None
    )
    if base is None or not _drivable(*base, day, km):
        return []
    after = [(stamp, value) for stamp, value in upto if stamp > base[0]]
    if not after or any(_drivable(*base, stamp, value) for stamp, value in after):
        return []
    return [stamp.isoformat() for stamp, _value in after]


def note_reading(log: OdometerLog, day: date, km: float) -> bool:
    """Record ``km`` as ``day``'s reading; ``True`` when the log changed.

    A reading below the newest earlier day's is ignored (an odometer never runs
    back), as is one at or below what the day already holds -- unless it shows
    the readings above it to have been no car's (:func:`_spike_days`).
    """

    if km is None or km < 0:
        return False
    key = day.isoformat()
    # Only a reading below what the log holds can show a spike; this runs on
    # every kilometre of a drive, so the rising ones skip the parse.
    if any(value > km for value in log.values()):
        for stamp in _spike_days(log, day, km):
            del log[stamp]
    earlier = [value for stamp, value in log.items() if stamp < key]
    if earlier and km < max(earlier):
        return False
    if key in log and km <= log[key]:
        return False
    log[key] = round(float(km), 1)
    # A later day holding less than this reading was itself a stale value.
    for stamp in [stamp for stamp, value in log.items() if stamp > key and value < km]:
        del log[stamp]
    if len(log) > MAX_DAYS:
        for stamp in sorted(log)[: len(log) - MAX_DAYS]:
            del log[stamp]
    return True


def readings(log: Mapping[str, float]) -> list[tuple[date, float]]:
    """The log as ``(day, km)`` pairs, oldest first; unparsable keys skipped."""

    pairs: list[tuple[date, float]] = []
    for stamp, km in log.items():
        try:
            pairs.append((date.fromisoformat(stamp), float(km)))
        except TypeError, ValueError:
            continue
    return sorted(pairs)


def km_per_day(
    log: Mapping[str, float],
    today: date,
    *,
    window_days: int = PACE_WINDOW_DAYS,
    min_span_days: int = PACE_MIN_SPAN_DAYS,
) -> Optional[float]:
    """The car's average km a day over the last ``window_days``, or ``None``.

    Measured between the oldest reading inside the window and the newest one.
    ``None`` until those two readings are at least ``min_span_days`` apart -- a
    young log gives no pace rather than a guess.
    """

    since = today - timedelta(days=window_days)
    inside = [(day, km) for day, km in readings(log) if since <= day <= today]
    if len(inside) < 2:
        return None
    (first_day, first_km), (last_day, last_km) = inside[0], inside[-1]
    span = (last_day - first_day).days
    if span < min_span_days:
        return None
    return max(0.0, (last_km - first_km) / span)


def reading_on_or_before(log: Mapping[str, float], day: date) -> Optional[tuple[date, float]]:
    """The newest reading taken on or before ``day``, or ``None``."""

    found = None
    for stamp, km in readings(log):
        if stamp > day:
            break
        found = (stamp, km)
    return found
