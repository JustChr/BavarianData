"""The running state-of-charge estimate between BMW's sparse SoC readings.

BMW streams the SoC (``batteryManagement.header``) only when it feels like it:
a charging car can go silent for hours and then jump nine percent the moment
someone opens the app and wakes it. In between, this extrapolates from the last
reading at the rate the streamed charging power implies.

Kept free of Home Assistant imports so it can be unit-tested; the coordinator
owns one instance per VIN and decides what to feed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# ``charging.status`` tokens that mean energy is flowing into the pack.
CHARGING_ACTIVE_STATUSES = frozenset({"CHARGINGACTIVE", "CHARGING_IN_PROGRESS"})


def is_charging_status(status: Any) -> bool:
    """Whether a ``charging.status`` value means the car is charging.

    Case-insensitive on purpose: the stream sends ALL_CAPS, but a value restored
    across a restart comes back as the sensor's lowercase enum slug -- and an
    exact match silently read every restored ``chargingactive`` as not charging.
    """

    return isinstance(status, str) and status.strip().upper() in CHARGING_ACTIVE_STATUSES


@dataclass
class SocTracking:
    energy_kwh: Optional[float] = None
    max_energy_kwh: Optional[float] = None
    last_update: Optional[datetime] = None
    last_power_w: Optional[float] = None
    last_power_time: Optional[datetime] = None
    charging_active: bool = False
    last_soc_percent: Optional[float] = None
    rate_per_hour: Optional[float] = None
    estimated_percent: Optional[float] = None
    last_estimate_time: Optional[datetime] = None
    target_soc_percent: Optional[float] = None
    # Last SoC seen while not charging -- the reference a new session is checked
    # against to tell whether it caught the whole charge.
    soc_before_charge: Optional[float] = None
    # The charge was running when we last stopped, and the stream has not spoken
    # since. Keeps the *estimate* climbing across a restart -- BMW can stay silent
    # for hours mid-charge -- without claiming a live charge anywhere else:
    # ``charging_active`` drives session transitions, energy integration and
    # what a charge controller is told, and those wait for the stream.
    restored_charging: bool = False
    # Earliest moment the restored charge can have added anything, so an
    # extrapolation anchored on an older reading never reaches back past it.
    charging_since: Optional[datetime] = None
    # When a restored estimate was last true. It outranks an older restored
    # reading, which would otherwise rewind the estimate to the last SoC BMW
    # happened to send (see ``update_actual_soc``).
    restored_estimate_at: Optional[datetime] = None

    def update_max_energy(self, value: Optional[float]) -> None:
        if value is None:
            return
        self.max_energy_kwh = value
        if self.last_soc_percent is not None and self.energy_kwh is None:
            self.energy_kwh = value * self.last_soc_percent / 100.0
        self._recalculate_rate()

    def update_actual_soc(
        self, percent: float, timestamp: Optional[datetime], *, restored: bool = False
    ) -> None:
        if not 0.0 <= percent <= 100.0:
            # Not a state of charge: a sentinel or a corrupt value. The raw
            # sensor still shows what BMW sent; the estimate and every session
            # boundary read from here, so it must not become their anchor.
            return
        # Remember the last reading taken while the car was *not* charging. A
        # session that opens well above it caught only part of a charge already
        # under way (see ``sessions._is_late_start``); it is never used to
        # backdate ``soc_start``, only to judge whether the record is whole.
        if not self.charging_active:
            self.soc_before_charge = percent
        self.last_soc_percent = percent
        ts = timestamp or datetime.now(timezone.utc)
        self.last_update = ts
        if self.max_energy_kwh:
            self.energy_kwh = self.max_energy_kwh * percent / 100.0
        else:
            self.energy_kwh = None
        if restored and self.restored_estimate_at is not None and self.restored_estimate_at > ts:
            # A restored estimate was extrapolated *from* this reading and is
            # newer; letting the reading win would throw that progress away.
            return
        self.estimated_percent = percent
        self.last_estimate_time = ts

    def adopt_estimate(self, percent: float, at: datetime) -> bool:
        """Take a restored estimate as the anchor, unless a reading is newer."""

        if self.last_update is not None and at <= self.last_update:
            return False
        self.estimated_percent = percent
        self.last_estimate_time = at
        self.restored_estimate_at = at
        return True

    def update_power(self, power_w: Optional[float], timestamp: Optional[datetime]) -> None:
        if power_w is None:
            return
        target_time = timestamp or datetime.now(timezone.utc)
        # Advance the running estimate to the moment this power sample was taken
        # so the previous charging rate is accounted for before we swap in the
        # new value.
        self.estimate(target_time)
        self.last_power_w = power_w
        self.last_power_time = target_time
        self._recalculate_rate()

    def update_status(self, status: Optional[str]) -> None:
        if status is None:
            return
        self.charging_active = is_charging_status(status)
        # The stream has spoken; the restored guess is no longer needed.
        self.restored_charging = False
        self.charging_since = None
        self._recalculate_rate()

    def restore_status(self, status: Optional[str], since: Optional[datetime]) -> None:
        """Take a charging status restored across a restart.

        Only the estimate believes it (``restored_charging``); ``charging_active``
        is left off so the first live status is still a real transition. Pass
        ``since`` only when a restored charge agrees the car was charging --
        without one, the status alone is too stale to extrapolate on.
        """

        self.charging_active = False
        self.restored_charging = since is not None and is_charging_status(status)
        self.charging_since = since if self.restored_charging else None
        self._recalculate_rate()

    def update_target_soc(
        self, percent: Optional[float], timestamp: Optional[datetime] = None
    ) -> None:
        if percent is None:
            self.target_soc_percent = None
            return
        self.target_soc_percent = percent
        if (
            self.estimated_percent is not None
            and self.last_soc_percent is not None
            and self.last_soc_percent <= percent
            and self.estimated_percent > percent
        ):
            self.estimated_percent = percent
            self.last_estimate_time = timestamp or datetime.now(timezone.utc)

    def estimate(self, now: datetime) -> Optional[float]:
        if self.estimated_percent is None:
            base = self.last_soc_percent
            if base is None:
                return None
            self.estimated_percent = base
            self.last_estimate_time = self.last_update or now
            return self.estimated_percent

        if self.last_estimate_time is None:
            self.last_estimate_time = now
            return self.estimated_percent

        start = self.last_estimate_time
        if self.charging_since is not None and start < self.charging_since:
            start = self.charging_since
        delta_seconds = (now - start).total_seconds()
        if delta_seconds <= 0:
            return self.estimated_percent

        rate = self.current_rate_per_hour()
        if rate in (None, 0):
            self.last_estimate_time = now
            return self.estimated_percent

        previous_estimate = self.estimated_percent
        increment = rate * (delta_seconds / 3600.0)
        self.estimated_percent = (self.estimated_percent or 0.0) + increment
        if (
            self.target_soc_percent is not None
            and rate > 0
            and previous_estimate is not None
            and previous_estimate <= self.target_soc_percent <= self.estimated_percent
        ):
            self.estimated_percent = self.target_soc_percent
        if self.estimated_percent > 100.0:
            self.estimated_percent = 100.0
        elif self.estimated_percent < 0.0:
            self.estimated_percent = 0.0
        self.last_estimate_time = now
        return self.estimated_percent

    def current_rate_per_hour(self) -> Optional[float]:
        if not self._extrapolating():
            return None
        return self.rate_per_hour

    def _extrapolating(self) -> bool:
        return self.charging_active or self.restored_charging

    def _recalculate_rate(self) -> None:
        if not self._extrapolating():
            self.rate_per_hour = None
            return
        if self.last_power_w in (None, 0) or self.max_energy_kwh in (None, 0):
            return
        self.rate_per_hour = (self.last_power_w / 1000.0) / self.max_energy_kwh * 100.0
