"""A report trip's route, from a phone that rode along.

A car that reports only once parked (an i3, iDrive 6 and older) sends nothing
while driving, so its trip is a straight line from where it last stood to where
it stands now. Its driver's phone, though, keeps reporting its position to Home
Assistant through the companion app the whole way. This module keeps those
positions per car while a drive may be under way, and hands the report trip the
ones that fall inside it.

The car stays the reference:

* **Distance, duration and charge are the car's own.** The phone draws the line
  on the map and nothing else; a GPS track under-reads a winding road anyway.
* **The phone must end where the car parked.** The car's own report says where
  it stopped. A phone whose last point is further away than
  :data:`END_MATCH_KM` was not in this car -- someone else drove it, or the
  phone stayed home -- and its points are dropped; the trip keeps its line.
* **No start, no route.** Without a start (a mark, or the in-car sensor turning
  on) nothing says which of the phone's positions belong to the drive.

With an in-car sensor set, positions are only kept while it is on, so a walk to
the bakery is never held. Without one, the phone's positions are held in memory
for up to :data:`MAX_AGE_S` and only the ones between the trip's start mark and
its arrival are used. Either way nothing reaches disk unless it becomes a trip's
route, and that only with route recording on. A restart mid-drive loses the
positions held so far: the route then starts where the phone was after it.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .history.trip_builder import GPS_MOVE_THRESHOLD_KM, haversine_km
from .vehicle_report import MARK_MAX_AGE_S

# A fix less certain than this is left out: indoors or in a garage the phone's
# position can wander by hundreds of metres.
MAX_ACCURACY_M = 100.0
# The furthest the phone's last point may be from where the car reported it
# parked. A phone that left the car with its driver still stands within a short
# walk; one that was never in it is usually kilometres away.
END_MATCH_KM = 0.5
# Positions held without an in-car sensor: as long as a start mark lives.
MAX_AGE_S = MARK_MAX_AGE_S
# A bound on what is held per car between two reports. The trip's own track
# decimates past ``MAX_TRACK_POINTS``; this only keeps memory bounded when a car
# never reports (or the phone is tracked for days without a drive).
MAX_POINTS = 5000


@dataclass
class PhoneRoute:
    """One car's phone positions, waiting for the report that ends the drive."""

    # Only keep positions while the phone is in the car. Set when the account
    # names an in-car sensor; ``in_car`` is then what that sensor last said.
    gated: bool = False
    in_car: bool = False
    points: list[tuple[datetime, float, float]] = field(default_factory=list)

    def set_in_car(self, on: bool) -> bool:
        """Note what the in-car sensor says; True when it has just turned on."""

        turned_on = on and not self.in_car
        self.in_car = on
        return turned_on

    def add(
        self, at: datetime, latitude: float, longitude: float, accuracy_m: Optional[float]
    ) -> bool:
        """Hold one phone position, if it can belong to a drive. True if held."""

        if self.gated and not self.in_car:
            return False
        if accuracy_m is not None and accuracy_m > MAX_ACCURACY_M:
            return False
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return False
        if self.points:
            _at, lat, lon = self.points[-1]
            # A phone in a queue still reports; ~50 m apart is what the live
            # detector counts as movement too.
            if haversine_km(lat, lon, latitude, longitude) < GPS_MOVE_THRESHOLD_KM:
                return False
        self.points.append((at, latitude, longitude))
        self._trim(at)
        return True

    def _trim(self, now: datetime) -> None:
        oldest = now - timedelta(seconds=MAX_AGE_S)
        self.points = [p for p in self.points if p[0] >= oldest][-MAX_POINTS:]

    def take(
        self,
        start: Optional[datetime],
        end: datetime,
        end_fix: Optional[tuple[float, float]],
    ) -> list[tuple[datetime, float, float]]:
        """The drive's route from ``start`` to ``end``, or ``[]`` if it has none.

        Everything up to ``end`` is let go either way: it belongs to this trip
        or to none.
        """

        inside = [p for p in self.points if start is not None and start <= p[0] <= end]
        self.points = [p for p in self.points if p[0] > end]
        if not inside or end_fix is None:
            return []
        _at, lat, lon = inside[-1]
        if haversine_km(lat, lon, *end_fix) > END_MATCH_KM:
            return []
        return inside
