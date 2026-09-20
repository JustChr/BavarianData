"""What the car really uses, and how far that reaches.

Home Assistant-free (see ``models.py``) so every figure here is unit-testable --
and this is a module where a plausible-looking wrong number does real damage: a
range figure is something people plan a journey on.

Everything derives from the charging ledger, via
:func:`~.summary.energy_balance`: two charging sessions bracket a window whose
distance (odometer at each end) and energy (what was delivered in between,
corrected for the charge still aboard) are both known, without a single drive
having to have been detected. Nothing here reads a trip record, and nothing here
spends REST quota.

Two properties of that source shape this whole module:

* **The window has to earn its answer.** A balance needs two odometer readings
  and at least :data:`~.summary.MIN_BALANCE_DISTANCE_KM` between them, so a
  fixed "last 30 days" would go blank for anyone who charges rarely, and a fixed
  "all time" would still be quoting last winter in June. The windows are
  therefore tried shortest-first (:data:`EFFICIENCY_WINDOWS_DAYS`) and the first
  one that can answer wins -- the most recent figure that is actually supported
  by data. Which window produced it is returned alongside it, because
  "17.5 kWh/100 km" means different things over 30 days and over a year.
* **Which side of the charger it describes is load-bearing.** Range must be
  computed from battery-side consumption and a battery-side capacity, or the
  charging losses get driven as if they were kilometres. Grid-side is the right
  figure for what charging costs. The two are never mixed silently: each figure
  carries its ``source``, and the charging loss between them is only reported
  when both describe the *same* window (see :func:`efficiency_profile`).

A session that BMW imported carries only a grid figure, so a window containing
one cannot be read battery-side at all; ``energy_balance`` declines rather than
summing a hole, and the next longer window is tried. That is why an older
imported charge does not poison the recent figure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

from .models import ChargingSession
from .summary import (
    SIDE_BATTERY,
    SIDE_GRID,
    energy_balance,
    sessions_in_month,
)

__all__ = [
    "EFFICIENCY_WINDOWS_DAYS",
    "MIN_SOC_FOR_FULL_RANGE",
    "TREND_MONTHS",
    "consumption",
    "efficiency_profile",
    "monthly_consumption",
    "real_range",
]

# Tried shortest-first; the first window that can produce a balance wins. A
# month is the shortest span that reliably brackets two charges on a car in
# normal use; a year is the longest that still says something about *now*.
EFFICIENCY_WINDOWS_DAYS = (30, 90, 365)

# How many calendar months the seasonal trend looks back over. Twelve makes the
# winter-vs-summer comparison the whole point of the trend possible.
TREND_MONTHS = 12

# Below this state of charge, scaling BMW's *remaining* range up to a
# full-battery figure divides by a small number and multiplies its own error
# with it -- at 5 % SoC a 10 km error becomes 200. The comparison is simply
# omitted down there rather than published as a wild one.
MIN_SOC_FOR_FULL_RANGE = 20.0

Localizer = Callable[[datetime], datetime]


def _identity(value: datetime) -> datetime:
    return value


def _within(sessions: list[ChargingSession], *, days: int, now: datetime) -> list[ChargingSession]:
    """Sessions that *ended* within the last ``days``.

    Keyed on the end (falling back to the start) because that is when the
    odometer and SoC readings a balance uses were taken.
    """

    cutoff = now - timedelta(days=days)
    return [s for s in sessions if (s.end or s.start) >= cutoff]


def consumption(
    sessions: Iterable[ChargingSession],
    *,
    battery_capacity_kwh: Optional[float],
    now: datetime,
    side: str = SIDE_BATTERY,
    windows: Iterable[int] = EFFICIENCY_WINDOWS_DAYS,
) -> Optional[dict[str, Any]]:
    """Recent consumption, from the shortest window that can support a figure.

    Returns the :func:`~.summary.energy_balance` dict with ``window_days``
    added -- the number of days the answer came from, or ``None`` when it took
    the whole ledger to find one. ``None`` when no window could answer at all.
    """

    sessions = list(sessions)
    for days in windows:
        result = energy_balance(
            _within(sessions, days=days, now=now),
            battery_capacity_kwh=battery_capacity_kwh,
            side=side,
        )
        if result is not None:
            return {**result, "window_days": days}
    result = energy_balance(sessions, battery_capacity_kwh=battery_capacity_kwh, side=side)
    if result is not None:
        return {**result, "window_days": None}
    return None


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def monthly_consumption(
    sessions: Iterable[ChargingSession],
    *,
    battery_capacity_kwh: Optional[float],
    now: datetime,
    months: int = TREND_MONTHS,
    localize: Localizer = _identity,
    side: str = SIDE_BATTERY,
) -> list[dict[str, Any]]:
    """Consumption per calendar month, oldest first -- the seasonal story.

    One entry per month that could produce a balance of its own; a month whose
    charging couldn't bracket enough distance is *omitted* rather than carried
    as a zero, so a sparse month never reads as a thrifty one. Each entry names
    the distance its figure came from, so a card can weight or caption it.

    The month's window runs from its first charge to its last, which is what a
    balance can honestly measure -- not the calendar month's edges.
    """

    sessions = list(sessions)
    local_now = localize(now)
    year, month = local_now.year, local_now.month
    series: list[dict[str, Any]] = []
    for _ in range(max(0, months)):
        balance = energy_balance(
            sessions_in_month(sessions, year=year, month=month, localize=localize),
            battery_capacity_kwh=battery_capacity_kwh,
            side=side,
        )
        if balance is not None:
            series.append(
                {
                    "month": f"{year:04d}-{month:02d}",
                    "kwh_per_100km": balance["kwh_per_100km"],
                    "distance_km": balance["distance_km"],
                    "source": balance["source"],
                }
            )
        year, month = _previous_month(year, month)
    series.reverse()
    return series


def real_range(
    *,
    kwh_per_100km: Optional[float],
    capacity_kwh: Optional[float],
    soc_percent: Optional[float] = None,
    bmw_range_km: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """How far the measured consumption reaches on the measured capacity.

    ``capacity_kwh`` and ``kwh_per_100km`` must both be battery-side: usable
    pack energy divided by what the car takes out of the pack per 100 km. Feed
    it a grid-side consumption figure and the charging losses are driven as
    kilometres that were never available.

    ``soc_percent`` adds the figure people actually read -- how far it goes from
    *here* -- and ``bmw_range_km`` (the car's own remaining-range estimate) adds
    the comparison. BMW's number is scaled up to a full battery only above
    :data:`MIN_SOC_FOR_FULL_RANGE`, where the division doesn't amplify its error
    past usefulness.

    ``None`` when either input is missing: a range from an assumed capacity is
    exactly the kind of confident wrong number this layer refuses to print.
    """

    if not kwh_per_100km or kwh_per_100km <= 0:
        return None
    if not capacity_kwh or capacity_kwh <= 0:
        return None

    full_km = capacity_kwh / kwh_per_100km * 100
    result: dict[str, Any] = {
        "full_km": round(full_km, 1),
        "kwh_per_100km": kwh_per_100km,
        "capacity_kwh": round(capacity_kwh, 1),
        "now_km": None,
        "soc_percent": None,
        "bmw_km": None,
        "bmw_full_km": None,
        "vs_bmw_percent": None,
    }

    if soc_percent is not None and 0 <= soc_percent <= 100:
        result["soc_percent"] = round(soc_percent, 1)
        result["now_km"] = round(full_km * soc_percent / 100.0, 1)

    if bmw_range_km is not None and bmw_range_km > 0:
        result["bmw_km"] = round(bmw_range_km, 1)
        if soc_percent is not None and soc_percent >= MIN_SOC_FOR_FULL_RANGE:
            result["bmw_full_km"] = round(bmw_range_km / (soc_percent / 100.0), 1)
        if result["now_km"] is not None:
            # Positive: the car goes further than its own dashboard promises.
            result["vs_bmw_percent"] = round(
                (result["now_km"] - bmw_range_km) / bmw_range_km * 100, 1
            )

    return result


# Why the profile has no figure to show. Reported so the entity and the card can
# say which input is missing instead of both going silently blank -- "not enough
# charging history yet" and "this car never streamed its capacity" need
# different answers from the user.
STATUS_OK = "ok"
STATUS_NO_CONSUMPTION = "not_enough_history"
STATUS_NO_CAPACITY = "no_capacity"


def efficiency_profile(
    sessions: Iterable[ChargingSession],
    *,
    battery_capacity_kwh: Optional[float] = None,
    usable_capacity_kwh: Optional[float] = None,
    soc_percent: Optional[float] = None,
    bmw_range_km: Optional[float] = None,
    now: datetime,
    localize: Localizer = _identity,
    months: int = TREND_MONTHS,
) -> dict[str, Any]:
    """Everything the efficiency view and the real-range entity read.

    ``battery_capacity_kwh`` is BMW's own usable-energy figure; when battery
    health has learned a capacity it trusts, pass it as ``usable_capacity_kwh``
    and it is preferred -- a measured pack beats a nameplate one, and
    ``capacity_source`` says which was used. Both are battery-side, which is
    what makes the range arithmetic legitimate.

    The grid-side figure is deliberately recomputed over **the same window** the
    battery-side one came from. Comparing a 30-day battery figure against a
    365-day grid figure would report a "charging loss" that is really a change
    in driving between two different periods.

    ``measured_loss_percent`` is named apart from the ``charging_loss_percent``
    *option* on purpose: that one is an assumption the user supplies so cost can
    be grossed up from battery-side energy, while this one is the gap between
    two measured sums. They must never be confused -- and the option never
    reaches a stored ``grid_kwh``, so this figure cannot be the setting fed
    back to itself.
    """

    sessions = list(sessions)
    capacity = usable_capacity_kwh or battery_capacity_kwh
    capacity_source = "measured" if usable_capacity_kwh else "bmw"

    battery = consumption(
        sessions,
        battery_capacity_kwh=battery_capacity_kwh,
        now=now,
        side=SIDE_BATTERY,
    )
    grid = None
    loss_percent = None
    if battery is not None:
        window = battery["window_days"]
        # Pinned to the battery figure's own window rather than run through
        # ``consumption`` again: that would fall back to a longer window when
        # this one has no grid figure, and the loss would then be measured
        # across two different periods of driving.
        scope = sessions if window is None else _within(sessions, days=window, now=now)
        balance = energy_balance(scope, battery_capacity_kwh=battery_capacity_kwh, side=SIDE_GRID)
        if balance is not None:
            grid = {**balance, "window_days": window}
        if grid is not None and grid["kwh_per_100km"] > battery["kwh_per_100km"]:
            # Only ever a loss: a grid figure *below* the battery one means the
            # two sums cover different energy, not that charging created any.
            loss_percent = round((1 - battery["kwh_per_100km"] / grid["kwh_per_100km"]) * 100, 1)

    ranges = real_range(
        kwh_per_100km=None if battery is None else battery["kwh_per_100km"],
        capacity_kwh=capacity,
        soc_percent=soc_percent,
        bmw_range_km=bmw_range_km,
    )

    if battery is None:
        status = STATUS_NO_CONSUMPTION
    elif not capacity:
        status = STATUS_NO_CAPACITY
    else:
        status = STATUS_OK

    return {
        "status": status,
        "consumption": battery,
        "grid_consumption": grid,
        "measured_loss_percent": loss_percent,
        "capacity_kwh": None if not capacity else round(capacity, 1),
        "capacity_source": None if not capacity else capacity_source,
        "range": ranges,
        "trend": monthly_consumption(
            sessions,
            battery_capacity_kwh=battery_capacity_kwh,
            now=now,
            months=months,
            localize=localize,
        ),
    }
