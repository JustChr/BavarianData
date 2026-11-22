"""When the device tracker may show a position: only a whole fix.

BMW streams a fix's latitude and longitude as two messages a moment apart, both
stamped with the fix's own time. Showing each as it lands plots a phantom point
in between (the fresh half with the previous fix's mate): on the map, in the
recorder, and across a zone's edge as a home/not_home flip that a person entity
or an automation acts on. So the tracker shows a pair only once both halves
carry the same timestamp. A half that waits longer than ``GPS_PAIR_STALE_S`` for
its mate is shown anyway, as trip detection does, so a car that breaks the
pairing degrades instead of freezing on its last position.

No Home Assistant imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .car_signals import GPS_PAIR_STALE_S

_LOGGER = logging.getLogger(__name__)


def _coordinate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError, TypeError:
        _LOGGER.debug("Unable to parse coordinate: %s", value)
        return None


@dataclass
class FixGate:
    """The position a tracker shows, moved only by a whole fix."""

    position: tuple[float, float] | None = None
    # Since when the two halves have disagreed (the stale escape).
    waiting_since: datetime | None = None

    def offer(
        self,
        lat: Any,
        lat_ts: str | None,
        lon: Any,
        lon_ts: str | None,
        now: datetime,
    ) -> bool:
        """Take the newest halves; True when the shown position changed.

        A missing timestamp (a restored or REST reading) cannot be paired, so
        it is taken as it stands.
        """

        if lat_ts is not None and lon_ts is not None and lat_ts != lon_ts:
            if self.waiting_since is None:
                self.waiting_since = now
            if (now - self.waiting_since).total_seconds() < GPS_PAIR_STALE_S:
                return False  # half a fix; wait for its mate
        self.waiting_since = None
        latitude, longitude = _coordinate(lat), _coordinate(lon)
        position = None if latitude is None or longitude is None else (latitude, longitude)
        if position == self.position:
            return False
        self.position = position
        return True
