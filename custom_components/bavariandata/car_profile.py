"""How a car reports, measured from what it sends -- never from its model.

The cars seen so far differ along a few traits that cut across models: whether
the state of charge streams at all or only comes with a REST fetch (the X3 30e,
issue #25), whether values arrive on change or as repeats with a fresh timestamp
(the iX1's odometer, issue #62), and whether the car reports while driving or
once after parking (the iDrive 6 G31, discussion #56). Rules that read silence
as evidence depend on exactly these traits, so they are measured per car here
and shown in the diagnostics download, where one dump answers them all.

Only counts, gaps and kilometre steps are kept: no value, position or VIN.
Descriptor names are catalogue identifiers. Live counters, like the arrival
tally: a restart starts the profile over.

Home Assistant-free on purpose, so it is unit-tested; the coordinator feeds it.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from statistics import median
from typing import Any, Deque, Dict, Optional

STREAM = "stream"
REST = "rest"

SOC = "vehicle.drivetrain.batteryManagement.header"
GPS_LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
ODOMETERS = frozenset({"vehicle.vehicle.mileage", "vehicle.vehicle.travelledDistance"})

# Batches closer together than this belong to one spell of the car being awake.
AWAKE_GAP_S = 600
# A streamed value stamped this long before it arrived is stale: older cars send
# service fields dated months back (#56), a MINI a trip-end value days old (#100).
STALE_AFTER_S = 86400
# Enough to describe a car; bounded so a long uptime costs nothing.
MAX_SAMPLES = 500
# Before a car has been watched this long, a SoC that never streamed may simply
# not have changed yet: a parked i5 can stay quiet for four hours.
MIN_HOURS_FOR_SOC_VERDICT = 24
MIN_STEPS_FOR_DRIVE_VERDICT = 3
# The i5 ticks its 1 km odometer while driving; the G31's burst jumps by a trip.
LIVE_MAX_KM_PER_STEP = 2
END_OF_TRIP_MIN_KM_PER_STEP = 5
# Descriptors arriving this often are judged on their repeats.
MIN_ARRIVALS_TO_JUDGE_REPEATS = 5
MOSTLY_HEARTBEAT_SHARE = 0.8
# Batches this large are bursts worth asking "one timestamp for all?".
MIN_BURST_SIZE = 5


def _median(samples: Any) -> Optional[float]:
    return round(median(samples), 1) if samples else None


class CarProfile:
    """One car's reporting traits since the integration started."""

    def __init__(self, started_at: datetime) -> None:
        self.started_at = started_at
        self._batches = {STREAM: 0, REST: 0}
        self._soc = {STREAM: 0, REST: 0}
        self._changed = 0
        self._heartbeat = 0
        self._duplicate = 0
        # descriptor -> [stream arrivals, heartbeats among them]
        self._repeats: Dict[str, list[int]] = {}
        self._placeholders: Dict[str, int] = {}
        self._stale: Dict[str, int] = {}
        self._lags: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self._awake_gaps: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self._sleeps = 0
        self._last_stream_batch: Optional[datetime] = None
        self._sizes: Deque[int] = deque(maxlen=MAX_SAMPLES)
        self._bursts = 0
        self._single_stamp_bursts = 0
        self._last_odometer: Optional[float] = None
        self._moving_fixes = 0
        self._step_km: Deque[float] = deque(maxlen=MAX_SAMPLES)
        self._step_fixes: Deque[int] = deque(maxlen=MAX_SAMPLES)
        self._batch_size = 0
        self._batch_stamps: set[str] = set()

    def observe(
        self,
        descriptor: str,
        value: Any,
        timestamp: Optional[str],
        parsed_ts: Optional[datetime],
        previous: Any,
        now: datetime,
        source: str,
    ) -> None:
        """One descriptor with a value, before it replaces ``previous``.

        ``previous`` is the coordinator's state for the descriptor (anything
        with ``value`` and ``timestamp``), or None the first time.
        """

        self._batch_size += 1
        if descriptor == SOC:
            self._soc[source] += 1
        if descriptor in ODOMETERS:
            self._observe_odometer(value, source)
        if source != STREAM:
            return
        if timestamp:
            self._batch_stamps.add(timestamp)
        if parsed_ts is not None:
            lag = max((now - parsed_ts).total_seconds(), 0.0)
            self._lags.append(lag)
            if lag > STALE_AFTER_S:
                self._stale[descriptor] = self._stale.get(descriptor, 0) + 1
        tally = self._repeats.setdefault(descriptor, [0, 0])
        tally[0] += 1
        if previous is None or previous.value != value:
            self._changed += 1
            if descriptor == GPS_LAT:
                self._moving_fixes += 1
        elif previous.timestamp != timestamp:
            self._heartbeat += 1
            tally[1] += 1
        else:
            self._duplicate += 1

    def observe_placeholder(self, descriptor: str) -> None:
        """BMW's stand-in for no reading arrived (``placeholders.py``)."""

        self._placeholders[descriptor] = self._placeholders.get(descriptor, 0) + 1

    def finish_batch(self, now: datetime, source: str) -> None:
        """Close the batch the ``observe`` calls since the last one belong to."""

        size, stamps = self._batch_size, self._batch_stamps
        self._batch_size, self._batch_stamps = 0, set()
        if not size:
            return
        self._batches[source] += 1
        if source != STREAM:
            return
        self._sizes.append(size)
        if size >= MIN_BURST_SIZE:
            self._bursts += 1
            if len(stamps) == 1:
                self._single_stamp_bursts += 1
        if self._last_stream_batch is not None:
            gap = (now - self._last_stream_batch).total_seconds()
            if gap < AWAKE_GAP_S:
                self._awake_gaps.append(max(gap, 0.0))
            else:
                self._sleeps += 1
        self._last_stream_batch = now

    def _observe_odometer(self, value: Any, source: str) -> None:
        try:
            km = float(value)
        except TypeError, ValueError:
            return
        last = self._last_odometer
        if source == STREAM and last is not None and km > last:
            self._step_km.append(km - last)
            self._step_fixes.append(self._moving_fixes)
        if last is None or km != last:
            # A REST fetch moves the mark too, so the next streamed reading
            # isn't taken for one long step.
            self._moving_fixes = 0
        self._last_odometer = km

    def soc_source(self, now: datetime) -> Optional[str]:
        """``stream``, ``rest_only``, or None while it can't be told yet."""

        if self._soc[STREAM]:
            return "stream"
        hours = (now - self.started_at).total_seconds() / 3600
        if self._soc[REST] and hours >= MIN_HOURS_FOR_SOC_VERDICT:
            return "rest_only"
        return None

    def drive_reporting(self) -> Optional[str]:
        """``live``, ``end_of_trip``, or None while it can't be told yet."""

        if len(self._step_km) < MIN_STEPS_FOR_DRIVE_VERDICT:
            return None
        step = median(self._step_km)
        if step <= LIVE_MAX_KM_PER_STEP:
            return "live"
        if step >= END_OF_TRIP_MIN_KM_PER_STEP:
            return "end_of_trip"
        return None

    def as_dict(self, now: datetime) -> dict[str, Any]:
        """The profile for the diagnostics download."""

        stream_arrivals = self._changed + self._heartbeat + self._duplicate
        mostly_heartbeat = sorted(
            descriptor
            for descriptor, (arrivals, heartbeats) in self._repeats.items()
            if arrivals >= MIN_ARRIVALS_TO_JUDGE_REPEATS
            and heartbeats / arrivals >= MOSTLY_HEARTBEAT_SHARE
        )
        return {
            "observed_since": self.started_at.isoformat(),
            "observed_hours": round((now - self.started_at).total_seconds() / 3600, 1),
            "batches": dict(self._batches),
            "soc_source": self.soc_source(now),
            "soc_arrivals": dict(self._soc),
            "drive_reporting": self.drive_reporting(),
            "odometer_steps": len(self._step_km),
            "median_km_per_step": _median(self._step_km),
            "median_moving_fixes_per_step": _median(self._step_fixes),
            "cadence": {
                "median_awake_gap_s": _median(self._awake_gaps),
                "sleeps": self._sleeps,
                "median_batch_size": _median(self._sizes),
                "bursts": self._bursts,
                "single_timestamp_bursts": self._single_stamp_bursts,
            },
            "repeats": {
                "changed": self._changed,
                "heartbeat": self._heartbeat,
                "duplicate": self._duplicate,
                "heartbeat_share": round(self._heartbeat / stream_arrivals, 2)
                if stream_arrivals
                else None,
                "mostly_heartbeat": mostly_heartbeat,
            },
            "median_lag_s": _median(self._lags),
            "stale_on_stream": dict(sorted(self._stale.items())),
            "placeholders": dict(sorted(self._placeholders.items())),
        }
