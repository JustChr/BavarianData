"""Hourly buckets that turn recorded history into long-term statistics.

Home Assistant-free (see ``models.py``) so the bucketing arithmetic is
unit-testable: this is the code that decides which hour a kilowatt-hour is
attributed to, and a quietly wrong bucket would show up as a phantom spike on
somebody's Energy dashboard.

Home Assistant's long-term statistics are hour-aligned in **UTC**, so everything
here works in UTC and leaves local-time month bucketing to ``summary.py``.

A record is spread across the hours it actually spans, weighted by how much of
each hour it occupied. That is an approximation of *when* the energy flowed --
we do not replay the power curve -- but the **total is exact**, and the shape is
right for the two cases that matter: an AC home charge draws near-constant power
for hours, and a DC fast charge is over inside one or two buckets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .models import ChargingSession
from .trips import Trip

HOUR = timedelta(hours=1)

# Beyond this a record's timeline is not believable (a stream gap left a session
# "open" for days, say). Rather than smearing the energy across two days of
# buckets, attribute the whole amount to the hour it started in: the total stays
# right even when the duration doesn't.
MAX_SPAN = timedelta(hours=48)


def floor_hour(value: datetime) -> datetime:
    """The UTC hour a timestamp falls in -- the key statistics are indexed by."""

    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def spread(
    start: datetime, end: Optional[datetime], total: Optional[float]
) -> dict[datetime, float]:
    """Distribute ``total`` over the UTC hours between ``start`` and ``end``.

    Each hour gets the share of the record that fell inside it. An open-ended,
    zero-length or implausibly long record collapses into its starting hour.
    """

    if not total:
        return {}
    amount = float(total)
    begin = start.astimezone(timezone.utc)
    first = floor_hour(begin)

    if end is None:
        return {first: amount}
    finish = end.astimezone(timezone.utc)
    span = (finish - begin).total_seconds()
    if span <= 0 or finish - begin > MAX_SPAN:
        return {first: amount}

    buckets: dict[datetime, float] = {}
    cursor = begin
    while cursor < finish:
        hour = floor_hour(cursor)
        chunk_end = min(hour + HOUR, finish)
        share = (chunk_end - cursor).total_seconds() / span
        buckets[hour] = buckets.get(hour, 0.0) + amount * share
        cursor = chunk_end
    return buckets


def session_energy_kwh(session: ChargingSession) -> Optional[float]:
    """The kWh figure a statistic should carry for one session.

    Prefers the measured grid figure over our integrated battery-side one: the
    Energy dashboard is about what came out of the wall, and ``grid_kwh`` is only
    ever set from something that actually measured it (see ``models.py``).
    """

    if session.grid_kwh is not None:
        return session.grid_kwh
    return session.energy_kwh


def _accumulate(
    target: dict[datetime, float], buckets: dict[datetime, float]
) -> None:
    for hour, value in buckets.items():
        target[hour] = target.get(hour, 0.0) + value


def hourly_energy(sessions: Iterable[ChargingSession]) -> dict[datetime, float]:
    """Charged energy per UTC hour, in kWh."""

    buckets: dict[datetime, float] = {}
    for session in sessions:
        _accumulate(
            buckets, spread(session.start, session.end, session_energy_kwh(session))
        )
    return buckets


def hourly_cost(
    sessions: Iterable[ChargingSession],
) -> tuple[dict[datetime, float], Optional[str]]:
    """Charging cost per UTC hour, plus the currency it is denominated in.

    Mixed currencies yield no series at all: summing euros and pounds into one
    running total would be worse than publishing nothing (``summary.summarise``
    refuses the same way).
    """

    buckets: dict[datetime, float] = {}
    currencies: set[str] = set()

    for session in sessions:
        entry = session.cost or {}
        amount = entry.get("amount")
        if amount is None:
            continue
        if entry.get("currency"):
            currencies.add(entry["currency"])
        _accumulate(buckets, spread(session.start, session.end, float(amount)))

    if len(currencies) > 1:
        return {}, None
    return buckets, next(iter(currencies), None)


def hourly_distance(trips: Iterable[Trip]) -> dict[datetime, float]:
    """Driven distance per UTC hour, in km."""

    buckets: dict[datetime, float] = {}
    for trip in trips:
        _accumulate(buckets, spread(trip.start, trip.end, trip.distance_km))
    return buckets


def cumulative(
    buckets: dict[datetime, float], *, precision: int = 3
) -> list[dict[str, Any]]:
    """Turn per-hour amounts into the running-sum rows statistics expect.

    Home Assistant stores a ``sum``-type statistic as a monotonic meter reading,
    so the caller has to hand it totals, not deltas. ``state`` mirrors ``sum``:
    there is no real meter behind these numbers, and pretending otherwise would
    only invite the two to disagree.

    The whole series is always regenerated from the store, so the sums restart
    from zero every time -- which is exactly what keeps them consistent after
    retention prunes the oldest records.
    """

    running = 0.0
    rows: list[dict[str, Any]] = []
    for hour in sorted(buckets):
        running += buckets[hour]
        value = round(running, precision)
        rows.append({"start": hour, "state": value, "sum": value})
    return rows
