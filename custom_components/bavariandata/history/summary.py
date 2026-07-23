"""Aggregations over recorded sessions and trips.

Home Assistant-free (see ``models.py``) so the month bucketing and the totals
are unit-testable -- these feed the user-facing cost sensors and the trip
"month in review", where a quietly wrong number is the worst possible failure.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean
from typing import Any, Callable, Iterable, Optional

from .models import ChargingSession
from .trips import CLASS_BUSINESS, CLASS_COMMUTE, CLASS_PRIVATE, Trip

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


def trips_in_month(
    trips: Iterable[Trip],
    *,
    year: int,
    month: int,
    localize: Localizer = _identity,
) -> list[Trip]:
    """Trips whose *start* falls in the given local-time month.

    A trip is attributed to the month it began in -- a drive that crosses
    midnight into a new month still belongs to the evening it started.
    """

    result = []
    for trip in trips:
        local = localize(trip.start)
        if local.year == year and local.month == month:
            result.append(trip)
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


# --- trips: the "month in review" -----------------------------------------

# The two star ratings BMW streams per segment, averaged into one driving-style
# score. Kept as named keys so the builder and the summary can't drift on them.
_STYLE_KEYS = ("accel_stars", "brake_stars")


def _style_score(stats: dict[str, Any]) -> Optional[float]:
    """A single 0-5 driving-style score from BMW's accel/brake stars.

    Averages whichever of the two ratings a segment carried; returns ``None``
    when it carried neither, so a trip with no style data doesn't count as zero.
    """

    values = [
        float(stats[key])
        for key in _STYLE_KEYS
        if isinstance(stats.get(key), (int, float))
    ]
    return mean(values) if values else None


def _dest_label(trip: Trip) -> Optional[str]:
    """A named destination for the top-destinations tally, or ``None``.

    Unknown/unnamed endpoints are skipped rather than lumped into one bogus
    "Unknown" bucket that would always win.
    """

    place = trip.end_place or {}
    label = place.get("label")
    if not label or label == "Unknown":
        return None
    return label


def _iso_week(dt: datetime) -> str:
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def driving_summary(
    trips: Iterable[Trip],
    *,
    prev_trips: Optional[Iterable[Trip]] = None,
    cost_per_100km: Optional[float] = None,
    currency: Optional[str] = None,
) -> dict[str, Any]:
    """The whole "month in review" object the trips card renders.

    ``trips`` is the current month's trips (already filtered -- use
    :func:`trips_in_month`); ``prev_trips`` is the previous month's, for the
    month-over-month delta. Every figure is omitted (``None``/absent) rather than
    faked when its inputs are missing, so the card can hide what it can't show
    (roadmap rule 4). All aggregation lives here, not in the card's JS.
    """

    trips = list(trips)
    total_km = round(sum(t.distance_km or 0.0 for t in trips), 1)
    count = len(trips)

    by_class = {CLASS_BUSINESS: 0.0, CLASS_PRIVATE: 0.0, CLASS_COMMUTE: 0.0}
    unclassified_km = 0.0
    for trip in trips:
        km = trip.distance_km or 0.0
        if trip.classification in by_class:
            by_class[trip.classification] += km
        else:
            unclassified_km += km

    def _pct(value: float) -> Optional[float]:
        return round(value / total_km * 100, 1) if total_km > 0 else None

    split = {
        "business_km": round(by_class[CLASS_BUSINESS], 1),
        "private_km": round(by_class[CLASS_PRIVATE], 1),
        "commute_km": round(by_class[CLASS_COMMUTE], 1),
        "unclassified_km": round(unclassified_km, 1),
        "business_percent": _pct(by_class[CLASS_BUSINESS]),
        "private_percent": _pct(by_class[CLASS_PRIVATE]),
        "commute_percent": _pct(by_class[CLASS_COMMUTE]),
    }

    # Consumption: use each trip's own energy/distance so best/worst are self
    # consistent with what the row shows. Best = most efficient (lowest).
    consumptions = [
        (trip, trip.consumption_kwh_per_100km)
        for trip in trips
        if trip.consumption_kwh_per_100km is not None
    ]
    avg_consumption = (
        round(mean(value for _t, value in consumptions), 1) if consumptions else None
    )
    best = min(consumptions, key=lambda pair: pair[1], default=None)
    worst = max(consumptions, key=lambda pair: pair[1], default=None)

    def _trip_ref(pair) -> Optional[dict[str, Any]]:
        if pair is None:
            return None
        trip, value = pair
        return {
            "id": trip.id,
            "label": _dest_label(trip) or "Unknown",
            "consumption": value,
            "distance_km": trip.distance_km,
        }

    recuperation = sum(
        float(t.stats.get("recuperation_kwh"))
        for t in trips
        if isinstance(t.stats.get("recuperation_kwh"), (int, float))
    )

    # Driving style: overall score plus a week-over-week trend for the sparkline.
    scored = [(t, _style_score(t.stats)) for t in trips]
    scored = [(t, s) for t, s in scored if s is not None]
    style_score = round(mean(s for _t, s in scored), 2) if scored else None
    weekly: dict[str, list[float]] = {}
    for trip, score in scored:
        weekly.setdefault(_iso_week(trip.start), []).append(score)
    style_trend = [
        {"week": week, "score": round(mean(scores), 2)}
        for week, scores in sorted(weekly.items())
    ]

    destinations = Counter(
        label for label in (_dest_label(t) for t in trips) if label is not None
    )
    top_destinations = [
        {"label": label, "count": n} for label, n in destinations.most_common(3)
    ]

    longest = max(
        (t for t in trips if t.distance_km),
        key=lambda t: t.distance_km,
        default=None,
    )

    prev_km = round(sum(t.distance_km or 0.0 for t in (prev_trips or [])), 1)
    mom_delta_km = round(total_km - prev_km, 1)
    mom_delta_percent = (
        round((total_km - prev_km) / prev_km * 100, 1) if prev_km > 0 else None
    )

    est_cost = None
    if cost_per_100km is not None and total_km > 0:
        est_cost = {
            "amount": round(total_km / 100 * cost_per_100km, 2),
            "currency": currency,
        }

    return {
        "total_km": total_km,
        "trip_count": count,
        "avg_trip_km": round(total_km / count, 1) if count else None,
        "split": split,
        "avg_consumption_kwh_per_100km": avg_consumption,
        "best_trip": _trip_ref(best),
        "worst_trip": _trip_ref(worst),
        "recuperation_kwh": round(recuperation, 1) if recuperation else None,
        "style_score": style_score,
        "style_trend": style_trend,
        "top_destinations": top_destinations,
        "longest_trip_km": round(longest.distance_km, 1) if longest else None,
        "prev_total_km": prev_km,
        "mom_delta_km": mom_delta_km,
        "mom_delta_percent": mom_delta_percent,
        "estimated_cost": est_cost,
    }
