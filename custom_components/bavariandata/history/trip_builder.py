"""Builds a trip record from the stream.

Home Assistant-free (see ``models.py``). The coordinator owns *when* a trip
starts and stops -- it watches the motion / ignition signals and BMW's own
completed-segment batch -- and this module owns what the resulting record looks
like. Distance is the odometer delta over the trip; BMW's ``travelledDistance``
is used only as a fallback for when the odometer didn't tick during the window.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .trips import Trip

# Below either threshold a "trip" is almost certainly a parking manoeuvre or a
# spurious ignition blip, not a drive worth logging.
MIN_TRIP_KM = 0.5
MIN_TRIP_S = 120


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
    ) -> None:
        self.vin = vin
        self.start = start
        self.start_place = start_place
        self.soc_start = soc_start
        self.mileage_start = mileage_start
        self.location_assumed = location_assumed

    def _distance_km(
        self, mileage_end: Optional[float], travelled_km: Optional[float]
    ) -> Optional[float]:
        """Odometer delta, falling back to BMW's own segment distance.

        A non-positive odometer span (unchanged, or a reading that went
        backwards) is discarded in favour of the fallback rather than logging a
        zero-length trip.
        """

        if self.mileage_start is not None and mileage_end is not None:
            span = mileage_end - self.mileage_start
            if span > 0:
                return round(span, 1)
        if travelled_km is not None and travelled_km > 0:
            return round(travelled_km, 1)
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
