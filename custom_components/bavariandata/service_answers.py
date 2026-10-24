"""What the services work out, apart from Home Assistant.

``services.py`` finds the account and the car (``service_target.py``), reads
the stores and raises Home Assistant's errors; the arithmetic between is here,
where the test suite runs it (``tests/test_service_answers.py``): reading a
call's month, dates and limit, the car a trip id names, the cost a backfilled
session gets, the export's files, the evcc block, and the portal activation's
attribute choice and error messages. A value a caller got wrong raises a
``refusal.Refusal`` naming the message the user sees; the handler turns it into
a translated ``ServiceValidationError``.
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

from .descriptors import descriptors_for_sections
from .evcc import ALL_TOPICS, evcc_yaml
from .history.export import MIME_CSV, MIME_HTML, sessions_csv, trips_csv
from .history.pricing import MODE_FIXED, fixed_cost
from .refusal import Refusal
from .stream_activation import DEFAULT_STREAM_ATTRIBUTES


def as_limit(value: Any) -> Optional[int]:
    """Coerce a service-call ``limit`` to an int (the number selector yields a
    float, which would break list slicing). Empty/garbage means no limit."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def cardata_timestamp(value: datetime) -> str:
    """An aware datetime as the ISO 8601 UTC string BMW's REST API expects."""

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_cardata_timestamp(value: Any, parsed: Optional[datetime], local_tz: Any) -> Optional[str]:
    """Normalise a service-call date/time into BMW's ISO 8601 UTC format.

    ``parsed`` is ``value`` read as a datetime (``None`` if it is not one).
    The datetime selector delivers a naive local ``datetime`` (or its string
    form); a hand-typed value may already be a full ISO 8601 string. Empty means
    "not supplied" so the caller can fall back to a default window, and a value
    that is not a datetime passes through unchanged.
    """

    if value in (None, ""):
        return None
    if parsed is None:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return cardata_timestamp(parsed)


def day_bound(parsed: datetime | date, *, end_of_day: bool) -> datetime:
    """A ``from``/``to`` argument as a UTC instant.

    A bare date names a whole day, so which end of it we mean depends on which
    bound it is: ``from: 2026-08-01`` starts at midnight, while ``to:
    2026-08-31`` has to run to the end of the 31st or a request for August
    silently drops everything that happened on its last day. An explicit
    date/time is always taken at its word. Naive is UTC, as Home Assistant's
    ``dt_util.as_utc`` has always read it here.
    """

    if not isinstance(parsed, datetime):
        parsed = datetime.combine(parsed, dt_time.max if end_of_day else dt_time.min)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def month_arg(raw: Optional[str], today: date) -> tuple[int, int]:
    """Parse a 'YYYY-MM' argument, defaulting to ``today``'s (local) month.

    Local, not UTC: a month is what the user's calendar says it is.
    """

    if not raw:
        return today.year, today.month
    try:
        year, month = (int(part) for part in str(raw).split("-", 1))
    except (ValueError, TypeError) as err:
        raise Refusal("month_format", value=repr(raw)) from err
    if not 1 <= month <= 12:
        raise Refusal("month_number", month=month, value=repr(raw))
    return year, month


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def trip_vin(trip_id: str, vins: Iterable[str]) -> Optional[str]:
    """The car a trip id names: it starts with its car's VIN."""

    return next((vin for vin in vins if trip_id.startswith(f"{vin}-")), None)


def backfill_cost(pricing: Any) -> Callable[[Optional[float]], Optional[dict[str, Any]]]:
    """The cost an imported BMW session gets.

    Only for a fixed tariff: a dynamic tariff's historical prices can't be
    reconstructed after the fact.
    """

    def cost(grid_kwh: Optional[float]) -> Optional[dict[str, Any]]:
        if grid_kwh and pricing.mode == MODE_FIXED:
            return fixed_cost(grid_kwh, pricing.fixed_price, pricing.currency)
        return None

    return cost


def window_holds(start: Optional[datetime], end: Optional[datetime], now: datetime) -> bool:
    """Whether a ``from``/``to`` window contains *now*.

    A drive still under way belongs to whatever window contains now: asking for
    March plainly doesn't mean the drive happening today, but asking for this
    month just as plainly does -- which is what the card's month view asks for
    every time.
    """

    return (start is None or start <= now) and (end is None or now <= end)


def export_stem(vin: Optional[str], label: str) -> str:
    """A file name the user can find again after ten monthly exports."""

    return f"bavariandata-{(vin or 'vehicle').lower()}-{label}"


def csv_files(
    stem: str,
    sessions: list,
    trips: list,
    wanted: str,
    localize: Callable[[datetime], datetime],
) -> list[dict[str, Any]]:
    """The CSV export: one file for charging, one for trips, as asked."""

    files: list[dict[str, Any]] = []
    if wanted in ("charging", "both"):
        files.append(
            {
                "filename": f"{stem}-charging.csv",
                "mime": MIME_CSV,
                "rows": len(sessions),
                "content": sessions_csv(sessions, localize=localize),
            }
        )
    if wanted in ("trips", "both"):
        files.append(
            {
                "filename": f"{stem}-trips.csv",
                "mime": MIME_CSV,
                "rows": len(trips),
                "content": trips_csv(trips, localize=localize),
            }
        )
    return files


def html_file(stem: str, rows: int, content: str) -> dict[str, Any]:
    return {"filename": f"{stem}.html", "mime": MIME_HTML, "rows": rows, "content": content}


def evcc_config(
    *,
    vin: str,
    config: Any,
    live: tuple[str, ...],
    mqtt_available: bool,
    title: Optional[str],
    capacity_kwh: Optional[float],
) -> dict[str, Any]:
    """The evcc ``custom`` vehicle block for a car, and the topics behind it.

    It reports which topics are actually live, because a plugin pointed at a
    topic that never gets a message makes evcc log a read error on every update
    cycle.
    """

    return {
        "vin": vin,
        "enabled": config.publishes(vin),
        # Stated plainly: the snippet is useless without a broker, and this is
        # the one thing a user cannot tell from the YAML itself.
        "mqtt_available": mqtt_available,
        "topic_prefix": config.prefix,
        "topics": [f"{config.prefix}/{vin}/{suffix}" for suffix in ALL_TOPICS],
        "published_topics": list(live),
        "yaml": evcc_yaml(
            prefix=config.prefix,
            vin=vin,
            title=title,
            capacity_kwh=capacity_kwh,
            topics=live or None,
        ),
    }


def activation_attributes(
    data: Mapping[str, Any], stored_sections: Optional[Iterable[str]]
) -> list[str]:
    """What the portal is asked to stream.

    In order: an explicit ``attributes`` list, the clusters named in
    ``sections``, the entry's stored "Choose streamed data" selection, or the
    default cluster set.
    """

    if data.get("attributes"):
        attributes = list(data["attributes"])
    elif data.get("sections"):
        attributes = descriptors_for_sections(data["sections"])
    elif stored_sections:
        attributes = descriptors_for_sections(stored_sections)
    else:
        attributes = list(DEFAULT_STREAM_ATTRIBUTES)
    if not attributes:
        raise Refusal("no_attributes")
    return attributes


def activation_error(kind: str, message: str) -> Refusal:
    """A portal failure, as the user should read it.

    The commonest is an expired session -- portal cookies (incl. the bot-defense
    ones) are short-lived and cannot be refreshed here.
    """

    if kind == "auth":
        return Refusal("portal_session_rejected")
    if kind == "not_found":
        return Refusal("portal_not_found")
    return Refusal("portal_failed", error=message)
