"""The vehicle-report event: when the car has reported a new odometer reading.

Older cars (iDrive 6 and earlier) stream nothing while driving: they send one
burst when parked -- odometer, fuel, position -- and a logbook built on them has
to know when that burst is in (discussion #56). ``bavariandata_vehicle_report``
says so once per new odometer reading, after the reading has held for
:data:`REPORT_QUIET_S`.

What must *not* fire it, and why the rules below look the way they do:

* **A heartbeat.** An iX1 repeats its unchanged odometer every few minutes with
  a fresh timestamp (issue #62), so a new timestamp is not a report; a new
  *value* is.
* **A car still driving.** The i5 streams the odometer at 1 km steps during a
  drive, so the event waits until the value has stopped moving.
* **A restart.** REST catch-ups go through the same message path as the stream
  and hand back the car's last values. Compared against memory, every restart
  would look like a new reading -- a phantom trip in a logbook. The last
  *announced* reading is persisted instead, and the very first one we ever see
  only seeds it: with nothing to compare against, a report cannot be told from
  a replay.
* **A wrong reading.** The record is what every later reading is measured
  against, so a wrong one must never get into it: a jump the car could not have
  driven -- a kilometre value labelled as miles is 1.6 times too high -- would be
  announced as one huge trip, and every true reading after it would look like
  the odometer running backwards, silencing the event for good. A reading that
  is backwards or too far ahead is kept aside as a *candidate* instead. If the
  next reading follows on plausibly from the candidate rather than from the
  record, the two agree and the record was the wrong one (a garbage first
  reading, say): the event resumes from the candidate. Either way one drive may
  go unannounced -- never a made-up one, and never all the rest.

Kept free of Home Assistant imports so the rules are unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

# How long a new odometer reading must hold before it is announced. Long enough
# that a car streaming live (1 km steps, GPS every few seconds) is past its
# drive; a stop of this length mid-drive -- a long jam -- gives a second event,
# each carrying its own distance.
REPORT_QUIET_S = 5 * 60

# The fastest average a step between two readings may imply, and the allowance
# on top for the odometer's whole-kilometre steps and for clock jitter. Far
# above any real drive -- the check is there to catch a reading off by a unit or
# by garbage, which lands thousands of kilometres out.
MAX_SPEED_KMH = 250.0
SLACK_KM = 5.0

SEED = "seed"
ARM = "arm"
CANDIDATE = "candidate"
IGNORE = "ignore"


@dataclass(frozen=True)
class ReportDecision:
    """What to do with a reading; ``baseline`` is what an ``ARM`` is measured from."""

    kind: str
    baseline: Optional[Mapping[str, Any]] = None


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _km(record: Optional[Mapping[str, Any]]) -> Optional[float]:
    if record is None:
        return None
    value = record.get("odometer_km")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def plausible_step(
    base: Mapping[str, Any], odometer_km: float, *, timestamp: Optional[str], now: datetime
) -> bool:
    """Whether the car could have driven from ``base`` to this reading.

    The time allowed is the longer of the car's own clock (between the two
    readings' timestamps) and ours (since ``base`` was recorded): a car with a
    wrong clock, or a long drive while Home Assistant was off, must never have a
    true reading refused. With neither to go by, a forward step is accepted, as
    before this check existed.
    """

    base_km = _km(base)
    if base_km is None or odometer_km <= base_km:
        return False
    elapsed: list[float] = []
    reading_at, base_at = _parse(timestamp), _parse(base.get("timestamp"))
    if reading_at is not None and base_at is not None:
        elapsed.append((reading_at - base_at).total_seconds())
    if (recorded_at := _parse(base.get("recorded_at"))) is not None:
        elapsed.append((now - recorded_at).total_seconds())
    if not elapsed:
        return True
    hours = max(0.0, max(elapsed)) / 3600
    return odometer_km - base_km <= SLACK_KM + MAX_SPEED_KMH * hours


def report_decision(
    odometer_km: Optional[float],
    last: Optional[Mapping[str, Any]],
    *,
    timestamp: Optional[str] = None,
    now: datetime,
) -> ReportDecision:
    """What a new odometer reading means: seed, arm, set aside, or nothing.

    ``last`` is the persisted record of the last announced reading, possibly
    carrying a ``candidate``. A reading equal to the record is a repeat
    (heartbeat, REST catch-up).
    """

    if odometer_km is None:
        return ReportDecision(IGNORE)
    last_km = _km(last)
    if last is None or last_km is None:
        return ReportDecision(SEED)
    if odometer_km == last_km:
        return ReportDecision(IGNORE)
    if plausible_step(last, odometer_km, timestamp=timestamp, now=now):
        return ReportDecision(ARM, last)
    candidate = last.get("candidate")
    if isinstance(candidate, Mapping) and _km(candidate) is not None:
        if odometer_km == _km(candidate):
            return ReportDecision(IGNORE)
        if plausible_step(candidate, odometer_km, timestamp=timestamp, now=now):
            return ReportDecision(ARM, candidate)
    return ReportDecision(CANDIDATE)


def report_payload(
    vin: str,
    *,
    odometer_km: float,
    timestamp: Optional[str],
    last: Mapping[str, Any],
    values: Mapping[str, Optional[float]],
) -> dict[str, Any]:
    """The event data: the new reading, the one before it, and what came with it.

    ``last`` is the reading the report is measured from. ``values`` holds
    whatever the car last reported for fuel, range, charge and position; a key
    the car does not send stays ``None`` rather than missing, so an automation
    can rely on the shape.
    """

    previous_km = last.get("odometer_km")
    distance = None
    if isinstance(previous_km, (int, float)):
        distance = round(odometer_km - previous_km, 1)
    return {
        "vin": vin,
        "timestamp": timestamp,
        "odometer_km": odometer_km,
        "previous_odometer_km": previous_km,
        "previous_timestamp": last.get("timestamp"),
        "distance_km": distance,
        **dict(values),
    }


# --- Trips from reports ------------------------------------------------------
#
# The end-of-trip trip mode (``trip_detection: reports``) builds one trip per
# announced report: from where the car stood at the previous report to where it
# stands now, the odometer difference in between. The burst says where the car
# went, never when it set off -- the previous report is when it *parked* there,
# and the car may have stood for days. So the start time comes from outside, if
# at all: ``bavariandata.mark_trip_start``, called by whatever knows the driver
# got in (a phone joining the car's Bluetooth, in discussion #56). Without a mark
# the trip is recorded without a duration rather than with an invented one.

# How long before a report a start mark may lie and still belong to its trip.
# Longer than any drive; it stops a Bluetooth connect with no drive after it from
# turning the next trip, days later, into a three-day one.
MARK_MAX_AGE_S = 12 * 3600
# Marks kept per car. A trip needs one; the rest only guard against a phone
# that reconnects mid-drive, or a webhook that arrives twice.
MAX_MARKS = 10


def add_start_mark(record: Optional[Mapping[str, Any]], at: datetime) -> dict[str, Any]:
    """The report record with a start mark added, in time order, bounded.

    Kept in the record so a mark survives a restart between the driver getting
    in and the car's report; a record that does not exist yet is started with
    the mark alone, and the first reading seeds it as usual.
    """

    marks = {at.isoformat(): at}
    for mark in (record or {}).get("starts") or ():
        if isinstance(mark, str) and (parsed := _parse(mark)) is not None:
            marks[mark] = parsed
    ordered = sorted(marks, key=marks.__getitem__)
    return {**(record or {}), "starts": ordered[-MAX_MARKS:]}


def take_start_mark(
    marks: Any, *, previous_at: Optional[datetime], end_at: datetime
) -> tuple[Optional[datetime], list[str]]:
    """The trip's start from the marks, and the marks left for later trips.

    A trip ends at ``end_at`` (the report, by the car's clock) and cannot have
    started before the car was last seen parked (``previous_at``). The earliest
    mark between the two is the start: a second Bluetooth connect during the
    drive is a reconnect, not a departure. A mark after ``end_at`` is already the
    next trip -- the driver set off again before this report was announced -- and
    is kept; every other one is spent.
    """

    parsed = [(p, m) for m in marks or () if isinstance(m, str) and (p := _parse(m)) is not None]
    earliest = end_at.timestamp() - MARK_MAX_AGE_S
    floor = max(earliest, previous_at.timestamp()) if previous_at is not None else earliest
    inside = [p for p, _m in parsed if floor < p.timestamp() <= end_at.timestamp()]
    later = [m for p, m in sorted(parsed) if p > end_at]
    return (min(inside) if inside else None), later


def report_time(record: Optional[Mapping[str, Any]]) -> Optional[datetime]:
    """When a recorded reading was taken: by the car's clock, else by ours."""

    if record is None:
        return None
    return _parse(record.get("timestamp")) or _parse(record.get("recorded_at"))


def fuel_stats(start_l: Any, end_l: Any) -> dict[str, float]:
    """The fuel figures a report trip carries in its ``stats``.

    ``fuel_used_l`` only when the level fell: a rise means the car was refuelled
    on the way, and the difference then says nothing about the drive.
    """

    stats: dict[str, float] = {}
    numbers = []
    for key, value in (("fuel_start_l", start_l), ("fuel_end_l", end_l)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stats[key] = float(value)
            numbers.append(float(value))
    if len(numbers) == 2 and numbers[0] >= numbers[1]:
        stats["fuel_used_l"] = round(numbers[0] - numbers[1], 1)
    return stats
