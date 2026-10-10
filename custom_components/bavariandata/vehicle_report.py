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
