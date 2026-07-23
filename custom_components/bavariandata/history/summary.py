"""Aggregations over recorded sessions.

Home Assistant-free (see ``models.py``) so the month bucketing and the totals
are unit-testable -- these feed the user-facing cost sensors, where a quietly
wrong number is the worst possible failure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from .models import ChargingSession

# Identity by default; the caller passes Home Assistant's local-time converter.
# Month boundaries are a local-time concept -- a session at 01:00 CEST on the
# 1st belongs to the new month even though it is still the 31st in UTC.
Localizer = Callable[[datetime], datetime]


def _identity(value: datetime) -> datetime:
    return value


def sessions_in_month(
    sessions: Iterable[ChargingSession],
    *,
    year: int,
    month: int,
    localize: Localizer = _identity,
) -> list[ChargingSession]:
    result = []
    for session in sessions:
        local = localize(session.start)
        if local.year == year and local.month == month:
            result.append(session)
    return result


def summarise(sessions: Iterable[ChargingSession]) -> dict[str, Any]:
    """Totals for a set of sessions.

    ``cost`` is ``None`` unless at least one session carried one. Mixed
    currencies also yield ``None``: summing euros and pounds into one number
    would be worse than showing nothing.
    """

    sessions = list(sessions)
    energy = 0.0
    cost = 0.0
    currencies: set[str] = set()
    costed = 0
    partial = False

    for session in sessions:
        if session.energy_kwh:
            energy += session.energy_kwh
        entry = session.cost
        if not entry:
            continue
        amount = entry.get("amount")
        if amount is None:
            continue
        cost += float(amount)
        costed += 1
        if entry.get("currency"):
            currencies.add(entry["currency"])
        if entry.get("partial"):
            partial = True

    summary: dict[str, Any] = {
        "sessions": len(sessions),
        "energy_kwh": round(energy, 3),
        "cost": None,
        "currency": None,
        # True when at least one session's cost is understated, so the total is
        # a floor rather than a figure.
        "partial": partial,
        "distance_km": None,
        "cost_per_100km": None,
    }

    if costed and len(currencies) <= 1:
        summary["cost"] = round(cost, 2)
        summary["currency"] = next(iter(currencies), None)

    distance = _distance_km(sessions)
    if distance:
        summary["distance_km"] = round(distance, 1)
        if summary["cost"] is not None:
            summary["cost_per_100km"] = round(summary["cost"] / distance * 100, 2)

    return summary


def _distance_km(sessions: list[ChargingSession]) -> Optional[float]:
    """Distance covered between the first and last charge of the period.

    Odometer readings taken at each session end are the only distance signal a
    charging record has. Two readings are the minimum that can describe a gap,
    and a non-positive span means the odometer didn't move or went backwards.
    """

    readings = [s.mileage_km for s in sessions if s.mileage_km is not None]
    if len(readings) < 2:
        return None
    span = max(readings) - min(readings)
    return span if span > 0 else None
