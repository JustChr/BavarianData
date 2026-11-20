"""How a reading's time and number are read, in one place.

BMW stamps every value it sends with the time the car measured it, as ISO 8601
in UTC (``2026-11-20T18:04:11.000Z``; every stamp in the recorded cars and
replays carries the ``Z``). The integration writes its own records' times the
same way, from timezone-aware datetimes. Both used to be parsed by half a dozen
private helpers that disagreed on the one case that matters: a stamp without a
timezone came back as ``None`` from some, as UTC from others, and as a *naive*
datetime from the rest -- which raises ``TypeError`` the first time it is
compared with an aware one. Ordering readings by BMW's stamp, never by arrival,
is how stale values are told from fresh ones, so the rule lives here.

Home Assistant-free, so the rule is unit-tested without an HA install.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_time(value: Any) -> datetime | None:
    """A timestamp as BMW or our own stores write it, or ``None``.

    ISO 8601 with ``Z`` or an offset. A stamp without a timezone is UTC, which
    is what BMW's are and what ours were written as. Anything that is not a
    non-empty string, or does not parse, is ``None``.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def as_float(value: Any) -> float | None:
    """``value`` as Python's ``float()`` reads it, or ``None`` if it can't.

    Deliberately lenient: numeric strings count, as BMW sends some numbers
    quoted. Callers with a stricter idea of a number (no booleans, no NaN, not
    negative) keep their own rule.
    """

    try:
        return float(value)
    except TypeError, ValueError:
        return None
