"""Builds a trip record from the stream.

Home Assistant-free (see ``models.py``). The coordinator owns *when* a trip
starts and stops -- it watches the motion / ignition signals and BMW's own
completed-segment batch -- and this module owns what the resulting record looks
like. Distance is the odometer delta over the trip; BMW's ``travelledDistance``
is used only as a fallback for when the odometer didn't tick during the window.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from .trips import Trip

# Below either threshold a "trip" is almost certainly a parking manoeuvre or a
# spurious ignition blip, not a drive worth logging.
MIN_TRIP_KM = 0.5
MIN_TRIP_S = 120

# Straight-line step below which a new GPS fix is treated as a parked car's
# jitter rather than movement: a stationary fix wanders a few tens of metres,
# so anything under ~50 m must not open (or keep alive) a trip.
GPS_MOVE_THRESHOLD_KM = 0.05

# Decimal places kept for a recorded track point: 5 dp is ~1.1 m, plenty to draw
# a route without persisting a needlessly precise fix. A point carries a third
# element -- whole seconds since the trip started -- when its fix time is known,
# so it is [lat, lon] or [lat, lon, t]; second resolution is enough because
# points are movement-gated well above what a car covers in a second.
TRACK_PRECISION = 5
# Hard bound on stored track points. A long drive decimates in place (halving the
# resolution) rather than growing the store without limit; 2000 points is a very
# detailed multi-hour route.
MAX_TRACK_POINTS = 2000


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points, in kilometres.

    Trip distance on a vehicle that streams neither an odometer nor BMW's
    ``travelledDistance`` (see ``coordinator``) can only be reconstructed by
    summing the straight-line hops along the GPS track. It under-reads a winding
    road slightly, but it is the only distance signal such a car provides.
    """

    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def is_gps_movement(step_km: float) -> bool:
    """True when a GPS step is large enough to be real movement, not jitter."""

    return step_km >= GPS_MOVE_THRESHOLD_KM


# Average speed across a gap in the fixes below which the car cannot have been
# driving for most of it. Walking pace: a car crawling in a jam still beats it
# over ten minutes, but a car parked in a signal-dead garage cannot.
SILENT_STOP_MAX_KMH = 3.0


def silence_implies_stop(
    gap_s: Optional[float],
    step_km: Optional[float],
    *,
    min_gap_s: float,
    max_kmh: float = SILENT_STOP_MAX_KMH,
) -> bool:
    """True when a long silence between fixes covered too little ground to drive.

    The coordinator holds a trip open when the position stream goes quiet, because
    silence alone doesn't mean the car stopped (see ``_hold_close_on_silence``).
    When the fixes come back, this decides which it was: a car that reappears far
    away was driving all along and the trip continues, while one that reappears
    where it vanished spent that time parked -- somewhere the fix simply couldn't
    be had, an underground garage being the obvious case -- so the trip really did
    end back then.

    Averaged over the whole gap rather than judged on the step alone, so a long
    silence needs proportionally more ground covered to read as driving. Only
    applied to gaps longer than ``min_gap_s`` (the close debounce): below that no
    close was ever due, so there is nothing to decide.
    """

    if gap_s is None or step_km is None or gap_s <= min_gap_s or gap_s <= 0:
        return False
    return step_km / (gap_s / 3600.0) < max_kmh


class GpsTracker:
    """Turns a stream of GPS fixes into per-fix movement steps.

    Deliberately tiny and HA-free: it remembers the previous fix and reports the
    straight-line distance to the next one. The coordinator decides what that
    means (open / extend / close a trip) -- this only does the arithmetic, so it
    can be unit-tested without an HA install like the rest of ``history/``.
    """

    def __init__(self) -> None:
        self.last_lat: Optional[float] = None
        self.last_lon: Optional[float] = None

    def step(self, lat: float, lon: float) -> float:
        """Distance (km) from the previous fix to this one; 0 for the first."""

        if self.last_lat is None or self.last_lon is None:
            self.last_lat, self.last_lon = lat, lon
            return 0.0
        km = haversine_km(self.last_lat, self.last_lon, lat, lon)
        self.last_lat, self.last_lon = lat, lon
        return km


class TripBuilder:
    """Accumulates one in-progress trip."""

    def __init__(
        self,
        vin: str,
        start: datetime,
        *,
        start_place: Optional[dict[str, Any]] = None,
        soc_start: Optional[float] = None,
        mileage_start: Optional[float] = None,
        location_assumed: bool = False,
        record_track: bool = False,
    ) -> None:
        self.vin = vin
        self.start = start
        self.start_place = start_place
        self.soc_start = soc_start
        self.mileage_start = mileage_start
        self.location_assumed = location_assumed
        # Distance summed from the GPS track, for cars that stream neither an
        # odometer nor BMW's ``travelledDistance`` (see ``add_gps_km``).
        self.gps_km = 0.0
        # Route polyline, only when the user opted in. Off by default so the
        # builder never persists a coordinate unless route recording is on. Each
        # point is [lat, lon] or [lat, lon, t] (t = seconds since start).
        self.record_track = record_track
        self.track: list[list[float]] = []
        self._track_stride = 1
        self._track_seen = 0

    def add_gps_km(self, km: float) -> None:
        """Fold one GPS hop into the running track distance.

        Called for every movement step while the trip is open; the accumulated
        total is used only when no more accurate distance exists at close.
        """

        if km and km > 0:
            self.gps_km += km

    def add_track_point(
        self, lat: float, lon: float, at: Optional[datetime] = None
    ) -> None:
        """Append a fix to the route polyline, when route recording is on.

        A no-op unless the user opted in, so a coordinate is only ever stored
        deliberately. When the fix's time ``at`` is supplied the point is stored
        as ``[lat, lon, t]`` where ``t`` is whole seconds since the trip started;
        omit it and the point is the legacy ``[lat, lon]`` (no time). ``t`` is
        what lets a map play the route back in real time and colour it by
        pace -- neither reconstructable after the fact, which is why the time is
        captured at record time or lost.

        Consecutive fixes at the *same coordinate* (a car sitting still still
        streams its position) are skipped -- deliberately comparing only the
        lat/lon so a differing ``t`` can't defeat the dedupe; the gap to the next
        stored point's ``t`` then encodes the dwell for free. Once the point
        budget is exhausted the track decimates in place -- mirroring the
        charging power curve, timestamps riding along -- so a very long drive
        can't grow the record without bound.
        """

        if not self.record_track:
            return
        coord = [round(lat, TRACK_PRECISION), round(lon, TRACK_PRECISION)]
        # Sub-sample at the current stride so decimation stays uniform: after a
        # halving, only every other subsequent fix is a candidate too.
        self._track_seen += 1
        if (self._track_seen - 1) % self._track_stride != 0:
            return
        if self.track and self.track[-1][:2] == coord:
            return
        point = coord if at is None else coord + [self._seconds_since_start(at)]
        self.track.append(point)
        if len(self.track) > MAX_TRACK_POINTS:
            self._decimate_track()

    def _seconds_since_start(self, at: datetime) -> int:
        """Whole seconds from the trip's start to a fix, floored at zero.

        A fix that timestamps just before the recorded start (clock skew, or an
        opening seed resolved a beat late) clamps to 0 rather than going
        negative and breaking a monotonic playback timeline.
        """

        return max(0, int((at - self.start).total_seconds()))

    def _decimate_track(self) -> None:
        """Halve the track resolution in place once the point budget is exceeded."""

        self.track = self.track[::2]
        self._track_stride *= 2

    def _distance_km(
        self, mileage_end: Optional[float], travelled_km: Optional[float]
    ) -> Optional[float]:
        """Odometer delta, then BMW's segment distance, then the GPS track.

        A non-positive odometer span (unchanged, or a reading that went
        backwards) is discarded in favour of the next source rather than logging
        a zero-length trip. The GPS track is last because it is the least
        accurate, but on a vehicle that streams no odometer it is all there is.
        """

        if self.mileage_start is not None and mileage_end is not None:
            span = mileage_end - self.mileage_start
            if span > 0:
                return round(span, 1)
        if travelled_km is not None and travelled_km > 0:
            return round(travelled_km, 1)
        if self.gps_km > 0:
            return round(self.gps_km, 1)
        return None

    def close(
        self,
        at: datetime,
        *,
        end_place: Optional[dict[str, Any]] = None,
        soc_end: Optional[float] = None,
        mileage_end: Optional[float] = None,
        energy_kwh: Optional[float] = None,
        travelled_km: Optional[float] = None,
        stats: Optional[dict[str, Any]] = None,
        classification: Optional[str] = None,
        classification_source: Optional[str] = None,
    ) -> Trip:
        """Finish the trip and return the record to persist."""

        return Trip(
            vin=self.vin,
            start=self.start,
            end=at,
            start_place=self.start_place,
            end_place=end_place,
            distance_km=self._distance_km(mileage_end, travelled_km),
            soc_start=self.soc_start,
            soc_end=soc_end,
            energy_kwh=None if energy_kwh is None else round(energy_kwh, 3),
            classification=classification,
            classification_source=classification_source,
            stats=dict(stats or {}),
            location_assumed=self.location_assumed
            or end_place is None
            or end_place.get("label") == "Unknown",
            track=list(self.track),
        )


def is_noise_trip(trip: Trip) -> bool:
    """True for a record too small to be a real drive.

    A known-short distance is the clearest signal; when distance is unknown we
    fall back to a minimum duration so a genuine long drive with a dead odometer
    isn't discarded.
    """

    if trip.distance_km is not None:
        return trip.distance_km < MIN_TRIP_KM
    duration = trip.duration_s
    return duration is not None and duration < MIN_TRIP_S
