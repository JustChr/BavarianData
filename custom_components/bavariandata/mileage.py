"""How far the car is driven, and how that compares with a mileage allowance.

Everything here is read off the daily odometer log (``history/odometer.py``):
totals for this month and year, the last twelve months, the car's pace -- and,
when the car has a mileage allowance, where it stands against it. Two kinds of
allowance exist, chosen under Configure -> *Mileage allowance*:

* **lease** (or financing): a start date, an end date, an allowance a year and
  the odometer at handover. The total is the yearly allowance times the
  contract's length.
* **yearly**: a limit that renews every year on the same day -- an insurance
  mileage class, a company-car rule. The current period runs from the latest
  anniversary of the start date; the odometer at that anniversary comes from
  the log (only the first period uses the reading the user typed in).

"Balance" is the plan to date minus what was driven: **positive is under
plan** (km to spare), negative is over.

A month's distance is the odometer at the start of the next month minus the
odometer at the start of this one, each interpolated between the readings
around it. A month the log does not cover from end to end is ``partial`` (the
log's first month, the current one) or absent.

Home Assistant-free on purpose; the sensor, the service and the card read
:func:`mileage_report`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Optional

from .const import (
    OPTION_MILEAGE_ALLOWANCE_KM,
    OPTION_MILEAGE_END,
    OPTION_MILEAGE_KIND,
    OPTION_MILEAGE_START,
    OPTION_MILEAGE_START_KM,
)
from .history.odometer import km_per_day, readings

KIND_NONE = "none"
KIND_LEASE = "lease"
KIND_YEARLY = "yearly"
KINDS = (KIND_NONE, KIND_LEASE, KIND_YEARLY)

# The keys of one car's allowance, stored together under the car's VIN in the
# options (``vehicle_options.MILEAGE_OPTIONS``).
KEY_KIND = OPTION_MILEAGE_KIND
KEY_START = OPTION_MILEAGE_START
KEY_END = OPTION_MILEAGE_END
KEY_PER_YEAR = OPTION_MILEAGE_ALLOWANCE_KM
KEY_START_KM = OPTION_MILEAGE_START_KM

MONTHS_SHOWN = 12


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except TypeError, ValueError:
        return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if number >= 0 else None


def odometer_at(log: Mapping[str, float], day: date) -> Optional[float]:
    """The odometer at the **start** of ``day``, or ``None`` outside the log.

    A day's reading is its highest, so it stands for the end of that day (the
    start of the next). Between two readings the odometer is interpolated; on or
    after the newest it is the newest (nothing has been driven that we know of).
    """

    pairs = readings(log)
    if not pairs:
        return None
    # Position of each reading: the start of the day after it was taken.
    points = [(stamp + timedelta(days=1), km) for stamp, km in pairs]
    if day < points[0][0]:
        return None
    if day >= points[-1][0]:
        return points[-1][1]
    for (left_day, left_km), (right_day, right_km) in zip(points, points[1:], strict=False):
        if left_day <= day <= right_day:
            span = (right_day - left_day).days
            return left_km + (right_km - left_km) * (day - left_day).days / span
    return None


def _month_start(day: date, back: int = 0) -> date:
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def _distance_since(log: Mapping[str, float], since: date, today: date) -> Optional[dict]:
    """Km driven from the start of ``since`` to now; partial when the log starts later."""

    pairs = readings(log)
    if not pairs:
        return None
    latest = pairs[-1][1]
    start = odometer_at(log, since)
    partial = start is None
    if start is None:
        start = pairs[0][1]
    return {"km": round(max(0.0, latest - start)), "partial": partial}


def monthly_km(log: Mapping[str, float], today: date, months: int = MONTHS_SHOWN) -> list[dict]:
    """The last ``months`` calendar months, oldest first: ``{"month", "km", "partial"}``.

    ``km`` is ``None`` for a month the log does not reach at all.
    """

    pairs = readings(log)
    first_point = pairs[0][0] + timedelta(days=1) if pairs else None
    rows: list[dict] = []
    for back in range(months - 1, -1, -1):
        start = _month_start(today, back)
        end = _month_start(today, back - 1)
        row: dict[str, Any] = {"month": start.strftime("%Y-%m"), "km": None, "partial": False}
        if pairs:
            km_end = odometer_at(log, min(end, today + timedelta(days=1)))
            km_start = odometer_at(log, start)
            if km_start is None and km_end is not None and first_point < end:
                km_start = pairs[0][1]
                row["partial"] = True
            if km_start is not None and km_end is not None:
                row["km"] = round(max(0.0, km_end - km_start))
                row["partial"] = row["partial"] or back == 0
        rows.append(row)
    return rows


def overview(log: Mapping[str, float], today: date) -> dict[str, Any]:
    """The figures every car gets, allowance or not."""

    pairs = readings(log)
    pace = km_per_day(log, today)
    return {
        "odometer_km": pairs[-1][1] if pairs else None,
        "since": pairs[0][0].isoformat() if pairs else None,
        "this_month": _distance_since(log, _month_start(today), today),
        "this_year": _distance_since(log, date(today.year, 1, 1), today),
        "km_per_day": round(pace, 1) if pace is not None else None,
        "projected_year_km": round(pace * 365) if pace is not None else None,
        "months": monthly_km(log, today),
    }


def allowance_settings(values: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """A car's stored allowance, validated, or ``None`` when it has none (or a broken one)."""

    kind = values.get(KEY_KIND)
    if kind not in (KIND_LEASE, KIND_YEARLY):
        return None
    start = parse_date(values.get(KEY_START))
    per_year = _number(values.get(KEY_PER_YEAR))
    if start is None or not per_year:
        return None
    settings = {
        "kind": kind,
        "start": start,
        "per_year_km": per_year,
        "start_km": _number(values.get(KEY_START_KM)),
    }
    if kind == KIND_LEASE:
        end = parse_date(values.get(KEY_END))
        if end is None or end <= start:
            return None
        settings["end"] = end
    return settings


def anniversary(start: date, year: int) -> date:
    try:
        return start.replace(year=year)
    except ValueError:  # 29 February in a common year
        return date(year, 2, 28)


def contract_years(start: date, end: date) -> float:
    """A contract's length in years, counted in months: 1 Jul to 30 Jun is one year.

    The end date is the last day of the contract, so the length runs to the day
    after it. Whole months first; the days left over are a fraction of the
    month they fall in. A day count over 365.25 made a 36-month lease 2.998
    years and its allowance 44,969 km instead of 45,000.
    """

    stop = end + timedelta(days=1)
    months = (stop.year - start.year) * 12 + stop.month - start.month
    anchor = _add_months(start, months)
    if anchor > stop:
        months -= 1
        anchor = _add_months(start, months)
    following = _add_months(start, months + 1)
    fraction = (stop - anchor).days / max(1, (following - anchor).days)
    return (months + fraction) / 12


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    # The 31st of a shorter month falls on its last day.
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def yearly_period(start: date, today: date) -> tuple[date, date]:
    """The current yearly period: from the latest anniversary of ``start`` on or before today."""

    begin = anniversary(start, today.year)
    if begin > today:
        begin = anniversary(start, today.year - 1)
    if begin < start:
        begin = start
    return begin, anniversary(start, begin.year + 1)


def allowance_status(
    settings: Mapping[str, Any], log: Mapping[str, float], today: date
) -> dict[str, Any]:
    """Where the car stands against its allowance today.

    ``status`` is ``ok``, ``not_started`` (the period starts in the future),
    ``ended`` (a lease past its end: the figures are its final ones) or
    ``unknown`` (no odometer at the period's start, or none now).
    """

    kind = settings["kind"]
    if kind == KIND_LEASE:
        begin, end = settings["start"], settings["end"]
        total = settings["per_year_km"] * contract_years(begin, end)
        start_km = settings["start_km"]
    else:
        begin, end = yearly_period(settings["start"], today)
        total = settings["per_year_km"]
        start_km = settings["start_km"] if begin == settings["start"] else None
        if start_km is None:
            start_km = odometer_at(log, begin)
    report: dict[str, Any] = {
        "kind": kind,
        "start": begin.isoformat(),
        "end": end.isoformat(),
        "allowance_km": round(total),
        "per_year_km": round(settings["per_year_km"]),
    }
    pairs = readings(log)
    if today < begin:
        return {**report, "status": "not_started"}
    if start_km is None or not pairs:
        return {**report, "status": "unknown"}

    total_days = (end - begin).days
    on_day = min(today, end)
    elapsed = max(1, (on_day - begin).days)
    used = max(0.0, pairs[-1][1] - start_km)
    plan = total * min(1.0, elapsed / total_days)
    pace = km_per_day(log, today)
    if pace is None:
        pace = used / elapsed
    days_left = max(0, (end - today).days)
    projected = used + pace * days_left
    report.update(
        {
            "status": "ended" if today >= end else "ok",
            "used_km": round(used),
            "plan_km": round(plan),
            "balance_km": round(plan - used),
            "remaining_km": round(total - used),
            "days_left": days_left,
            "km_per_day_left": round((total - used) / days_left, 1) if days_left else None,
            "projected_km": round(projected),
            "projected_over_km": round(projected - total),
        }
    )
    return report


def mileage_report(
    log: Mapping[str, float], today: date, allowance: Optional[Mapping[str, Any]] = None
) -> dict[str, Any]:
    """The overview, plus ``allowance`` (``None`` when the car has none)."""

    settings = allowance_settings(allowance or {})
    report = overview(log, today)
    report["allowance"] = allowance_status(settings, log, today) if settings else None
    return report
