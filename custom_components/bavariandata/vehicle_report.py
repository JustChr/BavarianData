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

Kept free of Home Assistant imports so the rules are unit-testable.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# How long a new odometer reading must hold before it is announced. Long enough
# that a car streaming live (1 km steps, GPS every few seconds) is past its
# drive; a stop of this length mid-drive -- a long jam -- gives a second event,
# each carrying its own distance.
REPORT_QUIET_S = 5 * 60

SEED = "seed"
ARM = "arm"
IGNORE = "ignore"


def report_decision(odometer_km: Optional[float], last: Optional[Mapping[str, Any]]) -> str:
    """What a new odometer reading means: seed the record, arm the timer, or nothing.

    ``last`` is the persisted record of the last announced reading. A reading
    that is not higher than it is a repeat (heartbeat, REST catch-up) or a
    glitch -- an odometer never runs backwards.
    """

    if odometer_km is None:
        return IGNORE
    if last is None or not isinstance(last.get("odometer_km"), (int, float)):
        return SEED
    if odometer_km > last["odometer_km"]:
        return ARM
    return IGNORE


def report_payload(
    vin: str,
    *,
    odometer_km: float,
    timestamp: Optional[str],
    last: Mapping[str, Any],
    values: Mapping[str, Optional[float]],
) -> dict[str, Any]:
    """The event data: the new reading, the one before it, and what came with it.

    ``values`` holds whatever the car last reported for fuel, range, charge and
    position; a key the car does not send stays ``None`` rather than missing, so
    an automation can rely on the shape.
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
