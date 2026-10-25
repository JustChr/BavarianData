"""A fuel log for petrol and diesel cars: refuels, and consumption between them.

A combustion car's only running record of what it burns is its tank reading.
BMW streams it every few minutes while the car is awake -- in litres
(``fuelSystem.remainingFuel``), as a percentage (``fuelSystem.level``) or both
-- and the counters that would say it outright (OBFCM, ``obfcm.py``) are missing
on many cars (#130: an X5 40d and a 118i send none).

So the log watches the tank for the one event that cannot be mistaken: a
**refuel**, a jump of several litres while the car stands still. Between two
refuels, what the tank lost over the odometer's kilometres is the consumption.
That is the brim-to-brim method drivers use with a pump receipt, with the tank
reading standing in for the receipt.

A single reading is coarse: BMW calls the litres accurate to about 6 L (the
float), and #130 saw them jitter by 1 L mid-trip. Two things keep that out of
the figure. A window must cover :data:`MIN_WINDOW_KM`, so 6 L of error is at
most a few tenths of a litre per 100 km; and a refuel only counts as one when
the rise is far beyond the jitter (:data:`REFUEL_MIN_LITRES`). A window whose
figure is still implausible (a refuel missed while the car was offline makes
it negative or absurd) is dropped, not shown.

Home Assistant-free on purpose; ``store.py`` persists the log and the
coordinator feeds it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, MutableMapping, Optional

# A rise this large between two readings is a refuel. Far beyond the ±1 L the
# float wanders by, and below any refuel worth the stop.
REFUEL_MIN_LITRES = 5.0
# The same for a car that streams only a percentage: 8 % of a 50-60 L tank.
REFUEL_MIN_PERCENT = 8.0
# A refuel happens standing still, but the car may not report again until it
# has driven off. A rise within this many kilometres still counts: the refuel
# keeps the odometer of the reading before it (where the old window ends) and
# of the one after (where the next begins), so the few kilometres between are
# left out of both rather than credited to the wrong tank. Beyond it, the gap
# is a drive, and the rise is not trusted as a refuel.
REFUEL_MAX_KM = 25.0
# After the pump the float settles upward for a while, and BMW may report the
# level in steps. Readings this soon after a refuel, before the car has moved,
# raise its "after" level instead of starting anything new.
SETTLE = timedelta(minutes=30)
SETTLE_KM = 2.0

# A consumption window must be at least this long: 6 L of float error over
# 150 km is 4 l/100 km at worst on one window, but the average over several is
# what the view leads with, and the drift cancels across refuels.
MIN_WINDOW_KM = 150.0
# Outside this, a window is a missed refuel or a bad reading, not a car.
MIN_L_PER_100KM = 2.0
MAX_L_PER_100KM = 30.0

# The average the view leads with covers the last year of windows.
AVERAGE_DAYS = 365
# Enough refuels for years of driving; the log is a few numbers each.
MAX_REFUELS = 300

FuelLog = MutableMapping[str, Any]

CHANGED_READING = "reading"
CHANGED_REFUEL = "refuel"


def _parse(stamp: Any) -> Optional[datetime]:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def note_reading(
    log: FuelLog,
    when: datetime,
    *,
    litres: Optional[float] = None,
    percent: Optional[float] = None,
    km: Optional[float] = None,
) -> Optional[str]:
    """Feed one tank reading; what changed, or ``None`` when it was ignored.

    ``when`` is the car's own timestamp for the reading, ``km`` the odometer
    at the time (``None`` when unknown). A reading older than the last one is a
    replay (a REST catch-up, a restored state) and is ignored. Answers
    :data:`CHANGED_REFUEL` when a refuel was added or settled, which is worth
    saving at once, else :data:`CHANGED_READING`.
    """

    litres = _num(litres)
    percent = _num(percent)
    km = _num(km)
    if litres is None and percent is None:
        return None
    last = log.get("last") if isinstance(log.get("last"), dict) else None
    last_at = _parse(last.get("t")) if last else None
    if last_at is not None and when <= last_at:
        return None

    # An odometer never runs back: a lower one is a stale value (#130 saw a
    # 118i repeat one mid-trip), so the last known reading stands in.
    last_km = _num(last.get("km")) if last else None
    if km is not None and last_km is not None and km < last_km:
        km = last_km
    if km is None:
        km = last_km

    reading = {"t": when.isoformat(), "litres": litres, "percent": percent, "km": km}
    refuels = log.setdefault("refuels", [])
    if not isinstance(refuels, list):
        refuels = log["refuels"] = []

    changed = CHANGED_READING
    if last is not None and _settles(refuels, reading, when):
        changed = CHANGED_REFUEL
    elif last is not None:
        rise_l = _rise(last.get("litres"), litres)
        rise_pct = _rise(last.get("percent"), percent)
        moved = (km - last_km) if (km is not None and last_km is not None) else 0.0
        jumped = (rise_l is not None and rise_l >= REFUEL_MIN_LITRES) or (
            rise_l is None and rise_pct is not None and rise_pct >= REFUEL_MIN_PERCENT
        )
        if jumped and moved <= REFUEL_MAX_KM:
            refuels.append(
                {
                    "t": reading["t"],
                    "km": km,
                    "km_before": last_km,
                    "before_l": _num(last.get("litres")),
                    "after_l": litres,
                    "before_pct": _num(last.get("percent")),
                    "after_pct": percent,
                }
            )
            if len(refuels) > MAX_REFUELS:
                del refuels[: len(refuels) - MAX_REFUELS]
            changed = CHANGED_REFUEL

    log["last"] = reading
    return changed


def _rise(before: Any, after: Optional[float]) -> Optional[float]:
    start = _num(before)
    if start is None or after is None:
        return None
    return after - start


def _settles(refuels: list[dict[str, Any]], reading: dict[str, Any], when: datetime) -> bool:
    """Raise the newest refuel's "after" level if this reading is its settling."""

    if not refuels:
        return False
    newest = refuels[-1]
    at = _parse(newest.get("t"))
    if at is None or when - at > SETTLE:
        return False
    km, refuel_km = _num(reading["km"]), _num(newest.get("km"))
    if km is not None and refuel_km is not None and km - refuel_km > SETTLE_KM:
        return False
    for key, field in (("litres", "after_l"), ("percent", "after_pct")):
        value = _num(reading[key])
        current = _num(newest.get(field))
        if value is not None and (current is None or value > current):
            newest[field] = value
    return True


def windows(log: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Consumption between consecutive refuels, oldest first.

    Only windows that can be measured in litres, cover :data:`MIN_WINDOW_KM`
    and come out plausible are returned.
    """

    refuels = [r for r in (log.get("refuels") or []) if isinstance(r, dict)]
    out: list[dict[str, Any]] = []
    for start, end in zip(refuels, refuels[1:], strict=False):
        km_end = end.get("km_before") if end.get("km_before") is not None else end.get("km")
        window = _window(start, end.get("before_l"), km_end, end.get("t"))
        if window is not None:
            out.append(window)
    return out


def _window(
    start: Mapping[str, Any], litres_now: Any, km_now: Any, until: Any
) -> Optional[dict[str, Any]]:
    after, before = _num(start.get("after_l")), _num(litres_now)
    km_from, km_to = _num(start.get("km")), _num(km_now)
    if after is None or before is None or km_from is None or km_to is None:
        return None
    km = km_to - km_from
    used = after - before
    if km < MIN_WINDOW_KM or used <= 0:
        return None
    rate = used / km * 100.0
    if not MIN_L_PER_100KM <= rate <= MAX_L_PER_100KM:
        return None
    return {
        "from": start.get("t"),
        "to": until,
        "km": round(km, 1),
        "litres": round(used, 1),
        "l_per_100km": round(rate, 1),
    }


def report(log: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Everything the Fuel view shows, newest refuel first.

    ``average`` covers the measurable windows of the last :data:`AVERAGE_DAYS`,
    as total litres over total kilometres -- a long window weighs what it
    drove, not one vote each. ``since_refuel`` is the open window since the
    newest refuel, given a figure only once it is as long as a closed one must be.
    """

    refuels = [r for r in (log.get("refuels") or []) if isinstance(r, dict)]
    closed = windows(log)
    by_end = {window["to"]: window for window in closed}

    listed = []
    for refuel in reversed(refuels):
        added_l = _rise(refuel.get("before_l"), _num(refuel.get("after_l")))
        added_pct = _rise(refuel.get("before_pct"), _num(refuel.get("after_pct")))
        window = by_end.get(refuel.get("t"))
        listed.append(
            {
                "t": refuel.get("t"),
                "km": refuel.get("km"),
                "added_l": round(added_l, 1) if added_l is not None else None,
                "added_percent": round(added_pct, 1) if added_pct is not None else None,
                "after_l": refuel.get("after_l"),
                "after_percent": refuel.get("after_pct"),
                # The consumption of the window this refuel closed.
                "l_per_100km": window["l_per_100km"] if window else None,
                "window_km": window["km"] if window else None,
            }
        )

    cutoff = now - timedelta(days=AVERAGE_DAYS)
    recent = [w for w in closed if (_parse(w["to"]) or now) >= cutoff]
    average = None
    if recent:
        km = sum(w["km"] for w in recent)
        litres = sum(w["litres"] for w in recent)
        average = {
            "l_per_100km": round(litres / km * 100.0, 1),
            "km": round(km, 1),
            "litres": round(litres, 1),
            "windows": len(recent),
        }

    since = None
    last = log.get("last") if isinstance(log.get("last"), dict) else None
    if refuels and last:
        newest = refuels[-1]
        km_from, km_to = _num(newest.get("km")), _num(last.get("km"))
        after, current = _num(newest.get("after_l")), _num(last.get("litres"))
        since = {
            "km": (
                round(km_to - km_from, 1) if km_from is not None and km_to is not None else None
            ),
            "litres": (
                round(after - current, 1) if after is not None and current is not None else None
            ),
            "l_per_100km": None,
        }
        open_window = _window(newest, last.get("litres"), last.get("km"), last.get("t"))
        if open_window is not None:
            since["l_per_100km"] = open_window["l_per_100km"]

    return {
        "refuels": listed,
        "average": average,
        "since_refuel": since,
        "last": dict(last) if last else None,
    }
