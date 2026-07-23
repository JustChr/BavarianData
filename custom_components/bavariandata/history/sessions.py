"""Builds a charging session from live stream samples.

Home Assistant-free (see ``models.py``). The coordinator owns *when* a session
starts and stops -- it already detects that transition to fire the
``bavariandata_charging_*`` events -- and this module owns what the resulting
record looks like.

Energy is deliberately *not* re-integrated here: the coordinator already
integrates charging power into its own accumulator, and computing it twice from
the same samples would only create two numbers that can disagree. The builder
takes the final figure at close.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .models import ChargingSession

# A point per minute is plenty to draw a charging curve, and keeps a typical AC
# session well under a hundred points.
MIN_SAMPLE_INTERVAL_S = 60
# Hard bound on stored points. A very long session (or an unusually chatty
# stream) decimates instead of growing the store without limit; 480 points is
# eight hours at one per minute.
MAX_CURVE_POINTS = 480


class SessionBuilder:
    """Accumulates one in-progress charging session."""

    def __init__(
        self,
        vin: str,
        start: datetime,
        *,
        soc_start: Optional[float] = None,
        target_soc: Optional[float] = None,
        location: Optional[dict[str, Any]] = None,
        location_assumed: bool = False,
    ) -> None:
        self.vin = vin
        self.start = start
        self.soc_start = soc_start
        self.soc_end = soc_start
        self.target_soc = target_soc
        self.location = location
        self.location_assumed = location_assumed
        self.peak_power_kw: Optional[float] = None
        self._curve: list[list[float]] = []
        self._interval = MIN_SAMPLE_INTERVAL_S
        self._last_offset: Optional[int] = None
        self._last_power: Optional[float] = None

    def _offset(self, at: datetime) -> int:
        # Clock skew between BMW's timestamps and ours could put a sample before
        # the start; clamp rather than emitting a negative x value.
        return max(0, int((at - self.start).total_seconds()))

    def sample(self, at: datetime, power_kw: Optional[float]) -> None:
        """Record a power reading.

        The peak comes from *every* reading, not only the stored ones -- a
        downsampled curve would otherwise miss a short spike between points.
        """

        if power_kw is None:
            return
        if self.peak_power_kw is None or power_kw > self.peak_power_kw:
            self.peak_power_kw = round(power_kw, 3)

        offset = self._offset(at)
        self._last_offset = offset
        self._last_power = round(power_kw, 3)
        if self._curve and offset - self._curve[-1][0] < self._interval:
            return
        self._curve.append([offset, self._last_power])
        if len(self._curve) > MAX_CURVE_POINTS:
            self._decimate()

    def _decimate(self) -> None:
        """Halve the resolution in place once the point budget is exceeded."""

        self._curve = self._curve[::2]
        self._interval *= 2

    def note_soc(self, soc: Optional[float]) -> None:
        if soc is not None:
            self.soc_end = soc

    def close(
        self,
        at: datetime,
        *,
        soc_end: Optional[float] = None,
        energy_kwh: Optional[float] = None,
        cost: Optional[dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> ChargingSession:
        """Finish the session and return the record to persist."""

        self.note_soc(soc_end)
        end_offset = self._offset(at)
        # Carry the last reading out to the end so the curve doesn't appear to
        # stop early when the final samples fell inside the downsample window.
        if self._last_power is not None and (
            not self._curve or self._curve[-1][0] < end_offset
        ):
            self._curve.append([end_offset, self._last_power])

        return ChargingSession(
            vin=self.vin,
            start=self.start,
            end=at,
            soc_start=self.soc_start,
            soc_end=self.soc_end,
            target_soc=self.target_soc,
            energy_kwh=None if energy_kwh is None else round(energy_kwh, 3),
            peak_power_kw=self.peak_power_kw,
            power_curve=self._curve,
            location=self.location,
            location_assumed=self.location_assumed,
            cost=cost,
            end_reason=reason,
        )
