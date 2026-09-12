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

from .energy_mix import merge_mix
from .models import ChargingSession, _iso
from .trips import CLASS_BUSINESS, CLASS_COMMUTE, CLASS_PRIVATE, Trip

__all__ = [
    "SIDE_AUTO",
    "SIDE_BATTERY",
    "SIDE_GRID",
    "driving_summary",
    "energy_balance",
    "fleet_consumption_kwh_per_100km",
    "sessions_in_month",
    "summarise",
    "trips_in_month",
]

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
        # Prefer the measured grid figure over the battery-side estimate, and
        # count imported sessions that carry only ``grid_kwh`` -- otherwise
        # BMW-imported charging is invisible in the monthly total (the
        # statistics and export paths already use this same rule).
        effective = session.effective_energy_kwh
        if effective:
            energy += effective
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
        # Where the month's energy came from, summed across the sessions that
        # could attribute theirs (see ``energy_mix.merge_mix``). ``None`` when
        # none of them could, which is not the same as "no solar".
        "energy_mix": merge_mix([session.energy_mix for session in sessions]),
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


# BMW's ``recuperationTotal`` is documented as an *average per 100 km* -- "the
# average electrical energy in kWh/100 km recuperated during the last logged
# drive" -- not a kWh total for the drive. Adding those up across a month
# produces a number with no meaning at all (ten drives at 5 kWh/100 km is not
# 50 of anything), so they are averaged, weighted by the distance each one
# describes. The older ``recuperation_kwh`` stats key is read too: records
# written before the unit was understood hold the same per-100 km figure under
# the wrong name, and re-reading them correctly is free.
_RECUP_KEYS = ("recuperation_kwh_per_100km", "recuperation_kwh")


def _recuperation_value(stats: dict[str, Any]) -> Optional[float]:
    for key in _RECUP_KEYS:
        value = stats.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _recuperation_per_100km(trips: list[Trip]) -> Optional[float]:
    """Distance-weighted mean recuperation, in kWh/100 km.

    Weighted rather than a plain mean for the same reason consumption is: a long
    drive's figure describes more kilometres than a short one's. Trips with no
    distance fall back to counting once, so a car that reports recuperation but
    no distance still yields something rather than nothing.
    """

    weighted = 0.0
    total_weight = 0.0
    for trip in trips:
        value = _recuperation_value(trip.stats)
        if value is None:
            continue
        weight = trip.distance_km or 1.0
        weighted += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return round(weighted / total_weight, 1)


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


def fleet_consumption_kwh_per_100km(trips: Iterable[Trip]) -> Optional[float]:
    """Battery-side consumption across many trips: total energy over total distance.

    Deliberately *not* the mean of the per-trip figures. Averaging ratios weights
    a 1 km hop the same as a 200 km run, so a handful of short drives -- the ones
    whose SoC quantisation already over-reads (see
    :data:`trips.MIN_CONSUMPTION_SOC_DELTA`) -- drag the headline far above
    anything the car ever used. Summing first weights every kilometre once, which
    is what "average consumption" means.

    Only trips that carry a usable consumption figure contribute, so the energy
    and the distance always describe the same set of drives.
    """

    energy = 0.0
    distance = 0.0
    for trip in trips:
        if trip.consumption_kwh_per_100km is None:
            continue
        energy += trip.energy_kwh or 0.0
        distance += trip.distance_km or 0.0
    if distance <= 0 or energy <= 0:
        return None
    return round(energy / distance * 100, 1)


# Shortest odometer span the balance will report a figure for. Over a few
# kilometres the correction term (a whole-percent SoC reading at each end) is
# larger than the energy actually used, so the answer would be quantisation
# noise wearing a decimal point.
MIN_BALANCE_DISTANCE_KM = 50.0
# Absurdity bounds. Not a judgement about efficient driving -- winter short-trip
# figures genuinely reach the forties -- only a catch for a corrupted odometer
# or a capacity that isn't this car's, where the arithmetic is meaningless
# rather than merely unflattering.
MIN_PLAUSIBLE_KWH_PER_100KM = 5.0
MAX_PLAUSIBLE_KWH_PER_100KM = 80.0


# Which side of the charger a balance should describe. ``"auto"`` takes whatever
# each session carries (the ledger's usual grid-preferred rule) and reports which
# it got; the explicit sides answer only when *every* contributing session can be
# read on that side, because a hole in the sum is a quietly understated figure
# rather than a missing one.
SIDE_AUTO = "auto"
SIDE_BATTERY = "battery"
SIDE_GRID = "grid"


def _side_energy(session: ChargingSession, side: str) -> Optional[float]:
    if side == SIDE_BATTERY:
        return session.energy_kwh
    if side == SIDE_GRID:
        return session.grid_kwh
    return session.effective_energy_kwh


def energy_balance(
    sessions: Iterable[ChargingSession],
    *,
    battery_capacity_kwh: Optional[float] = None,
    side: str = SIDE_AUTO,
) -> Optional[dict[str, Any]]:
    """Consumption over a period, measured from the charging ledger alone.

    The per-trip route cannot beat the resolution of the SoC signal it is built
    from. This sidesteps that entirely, and deliberately reads *nothing* from the
    trip record -- not even the distance. Every charging session carries an
    odometer reading and an SoC, taken at the moment it ended, so two sessions
    bracket a window whose distance and energy are both known without a single
    drive having to have been detected::

        used     = energy delivered between the two readings
                   - (soc_end_last - soc_end_first) / 100 * capacity
        distance = odometer_last - odometer_first

    That independence is the whole point. Trip detection shipped after charging
    history did, and any month where a drive was missed -- a dead stream, an
    upgrade, a garage with no signal -- would otherwise divide a full month of
    charging by a partial month of driving and report a wildly inflated figure.
    Real data made that concrete: one such month read 86.8 kWh/100 km off the
    trip distance and 20.4 off the odometer.

    The opening session's own energy is excluded: its readings are taken when it
    *finished*, so what it delivered arrived before the window opened. Charged
    energy is integrated from streamed charging power rather than read off a
    quantised SoC, so only the correction term carries a rounding step, and a
    month of driving dwarfs it.

    **Which side of the charger the answer describes depends on the ledger**, and
    the returned ``source`` says which: ``"grid"`` when every contributing
    session carried a measured ``grid_kwh`` (a bound wallbox, or BMW's own
    import), otherwise ``"battery"`` -- because ``energy_kwh`` is integrated
    battery-side charging power, not what came out of the wall (see
    :class:`~.models.ChargingSession`). A grid-side figure includes charging
    losses and reads above the car's own display; a battery-side one is the same
    quantity the trip figures measure, just measured far better. Presenting
    either without saying which is how a number ends up quietly meaning
    something other than its label.

    ``side`` overrides that: ask for :data:`SIDE_BATTERY` or :data:`SIDE_GRID`
    and the figure is only returned when every session that delivered energy in
    the window can be read on that side. A caller comparing the two sides -- to
    quantify the charging loss, or to turn consumption into range -- must have
    both describing the same kilometres, and a session missing from one sum
    would understate that side instead of declining to answer.

    Returns ``None`` rather than a guess whenever the inputs can't support an
    answer: fewer than two odometer readings, too short a span, an unknown
    capacity, or a result outside anything a road vehicle produces.
    """

    if not battery_capacity_kwh or battery_capacity_kwh <= 0:
        return None

    # Only sessions that carry both an odometer and an SoC can bound a window.
    bounded = sorted(
        (
            session
            for session in sessions
            if session.mileage_km is not None and session.soc_end is not None
        ),
        key=lambda session: session.start,
    )
    if len(bounded) < 2:
        return None

    first, last = bounded[0], bounded[-1]
    distance = last.mileage_km - first.mileage_km
    if distance < MIN_BALANCE_DISTANCE_KM:
        return None

    contributing = [
        session for session in bounded[1:] if session.effective_energy_kwh
    ]
    # An explicit side must be able to read every one of them: a session that
    # only carries the other side's figure would silently drop out of the sum
    # and make the window look thriftier than it was.
    if side != SIDE_AUTO and any(
        _side_energy(session, side) is None for session in contributing
    ):
        return None
    charged = sum(_side_energy(session, side) or 0.0 for session in contributing)
    if charged <= 0:
        return None
    # Grid-side only when every last kWh of it was actually measured at the grid.
    # One estimated session in the total makes the whole figure battery-side --
    # calling a mixture "at the plug" would overstate it by the losses of the
    # part that never saw a meter.
    source = (
        side
        if side != SIDE_AUTO
        else (
            "grid"
            if all(session.grid_kwh is not None for session in contributing)
            else "battery"
        )
    )

    # A battery fuller at the close than at the open means some of what was
    # charged is still aboard and was not driven on; emptier means the window
    # was partly run off charge that arrived before it.
    stored_delta = (last.soc_end - first.soc_end) / 100.0 * battery_capacity_kwh
    used = charged - stored_delta
    if used <= 0:
        return None

    rate = round(used / distance * 100, 1)
    if not MIN_PLAUSIBLE_KWH_PER_100KM <= rate <= MAX_PLAUSIBLE_KWH_PER_100KM:
        return None

    return {
        "kwh_per_100km": rate,
        # "grid" (includes charging losses) or "battery" (what the car uses).
        # Never omitted: a consumption figure without its side is ambiguous by
        # exactly the size of the charging loss.
        "source": source,
        "used_kwh": round(used, 1),
        "charged_kwh": round(charged, 1),
        "battery_delta_kwh": round(stored_delta, 1),
        "distance_km": round(distance, 1),
        "soc_start": first.soc_end,
        "soc_end": last.soc_end,
        # The window really measured, which is the first charge to the last --
        # not the whole month. A caller showing the figure can say so.
        "from": _iso(first.end or first.start),
        "to": _iso(last.end or last.start),
    }


def driving_summary(
    trips: Iterable[Trip],
    *,
    prev_trips: Optional[Iterable[Trip]] = None,
    cost_per_100km: Optional[float] = None,
    currency: Optional[str] = None,
    sessions: Optional[Iterable[ChargingSession]] = None,
    battery_capacity_kwh: Optional[float] = None,
) -> dict[str, Any]:
    """The whole "month in review" object the trips card renders.

    ``trips`` is the current month's trips (already filtered -- use
    :func:`trips_in_month`); ``prev_trips`` is the previous month's, for the
    month-over-month delta. ``sessions`` is the same month's charging, which
    together with ``battery_capacity_kwh`` yields the plug-side
    :func:`energy_balance` -- the headline consumption figure, because it is the
    only one the SoC signal's resolution doesn't limit. Every figure is omitted
    (``None``/absent) rather than faked when its inputs are missing, so the card
    can hide what it can't show (roadmap rule 4). All aggregation lives here, not
    in the card's JS.
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
    # consistent with what the row shows. Best = most efficient (lowest). Trips
    # whose SoC drop was too small to divide by report no figure at all, so they
    # can neither be nominated nor weigh on the average.
    consumptions = [
        (trip, trip.consumption_kwh_per_100km)
        for trip in trips
        if trip.consumption_kwh_per_100km is not None
    ]
    avg_consumption = fleet_consumption_kwh_per_100km(trips)
    best = min(consumptions, key=lambda pair: pair[1], default=None)
    worst = max(consumptions, key=lambda pair: pair[1], default=None)
    balance = energy_balance(
        sessions or [], battery_capacity_kwh=battery_capacity_kwh
    )

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

    recuperation = _recuperation_per_100km(trips)

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
        # Battery-side, from the trips that carried a usable figure -- comparable
        # with the car's own display, and consistent with the per-trip rows.
        "avg_consumption_kwh_per_100km": avg_consumption,
        # From the charging ledger -- the headline, and the one that survives a
        # missed drive or a month of nothing but short hops. Its side is
        # whichever the ledger can actually measure (``SIDE_AUTO``): grid when a
        # measured grid figure exists, battery otherwise, and the ``source`` key
        # says which. Do not describe it as grid-side -- on an install with no
        # grid figure it is battery-side, the same quantity the trip average
        # measures, which is exactly why the card prints the pair only when the
        # balance really is grid-side.
        "energy_balance": balance,
        "best_trip": _trip_ref(best),
        "worst_trip": _trip_ref(worst),
        "recuperation_kwh_per_100km": recuperation,
        "style_score": style_score,
        "style_trend": style_trend,
        "top_destinations": top_destinations,
        "longest_trip_km": round(longest.distance_km, 1) if longest else None,
        "prev_total_km": prev_km,
        "mom_delta_km": mom_delta_km,
        "mom_delta_percent": mom_delta_percent,
        "estimated_cost": est_cost,
    }
