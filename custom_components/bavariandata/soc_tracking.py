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
from typing import Any, Optional, Protocol


class MeterModel(Protocol):
    """What the tracker needs of ``history.meter_rate.MeterRate``."""

    def points(self, kwh: float, hours: float) -> float: ...


# ``charging.status`` tokens that mean energy is flowing into the pack.
CHARGING_ACTIVE_STATUSES = frozenset({"CHARGINGACTIVE", "CHARGING_IN_PROGRESS"})

# The fastest a home wallbox can advance its meter: 22 kW is the AC ceiling
# (three phases at 32 A). A larger step is not this car charging -- a meter
# replaced or reset to some other count, or a reading after a long outage that
# covers more than the charge -- and is taken as a new baseline instead.
METER_MAX_KW = 22.0
# ...plus a fixed allowance, because a meter that reports in coarse steps can
# land one step "early" relative to our clock.
METER_SLACK_KWH = 0.5
# The least time the meter's charging rate is measured over (``_note_meter_rate``).
METER_RATE_WINDOW_S = 300.0
# A meter that has not moved for this long is a paused charge, rate zero. Long
# enough that a wallbox reporting in coarse steps is not mistaken for one.
METER_IDLE_S = 900.0


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
    # The bound wallbox meter, while it drives the estimate (``follow_meter``).
    # ``meter_kwh`` is the reading the current estimate already accounts for,
    # ``meter_at`` when it was last accepted, ``meter_model`` what turns its
    # kWh into points (``history.meter_rate.MeterRate``) -- ``None`` whenever
    # the stream's power drives instead.
    meter_kwh: Optional[float] = None
    meter_at: Optional[datetime] = None
    meter_model: Optional[MeterModel] = None
    # The charging rate as the meter measures it (``_note_meter_rate``);
    # ``None`` until enough time is covered, and the stream's rate is shown
    # meanwhile.
    meter_rate_per_hour: Optional[float] = None
    meter_window_points: float = 0.0
    meter_window_hours: float = 0.0

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
        # The estimate starts again from a measurement; the meter's next reading
        # becomes its new baseline, so nothing it counted before is added twice.
        self.meter_kwh = None
        self.meter_at = None

    def adopt_estimate(
        self, percent: float, at: datetime, *, meter_kwh: Optional[float] = None
    ) -> bool:
        """Take a restored estimate as the anchor, unless a reading is newer.

        ``meter_kwh`` is the wallbox reading that estimate accounted for, when
        the meter was driving it. Restoring it is what lets the first reading
        after a restart add exactly what the car took while Home Assistant was
        down, instead of guessing at it.
        """

        if self.last_update is not None and at <= self.last_update:
            return False
        self.estimated_percent = percent
        self.last_estimate_time = at
        self.restored_estimate_at = at
        if meter_kwh is not None:
            self.meter_kwh = meter_kwh
            self.meter_at = at
        return True

    def follow_meter(
        self, meter_kwh: Optional[float], model: Optional[MeterModel], now: datetime
    ) -> None:
        """Drive the estimate from the wallbox meter, or hand it back to the stream.

        ``model`` is what the ledger learned (``history.meter_rate``); the
        caller passes ``None`` for either argument whenever the meter may not
        be trusted for this car right now -- not charging, unplugged, away from
        home, another car charging, too few sessions learned, the meter
        unavailable. The meter then lets go, and the stream's power takes over
        *from now*: the time the meter drove is already in the estimate and
        must not be extrapolated a second time.

        While it drives, the estimate moves by what the meter counted, less the
        car's own overhead for the time it took. That covers what BMW's power
        stream misses -- hours of silence, a solar-following charge -- and,
        with ``meter_kwh`` restored, a restart.
        """

        if meter_kwh is None or model is None or self.estimated_percent is None:
            if self.meter_model is not None:
                self.last_estimate_time = now
            self.meter_model = None
            self.meter_kwh = None
            self.meter_at = None
            self._reset_meter_window(None)
            return
        if self.meter_model is None and self.meter_kwh is None:
            # Taking over from the stream: account for its stretch up to now.
            # Not after a restart that restored a meter reading -- the meter's
            # own advance covers that stretch, and both would count it twice.
            self.estimate(now)
        if self.meter_model is None:
            self._reset_meter_window(now)
        self.meter_model = model
        self.last_estimate_time = now
        if self.meter_kwh is None or self.meter_at is None:
            self.meter_kwh = meter_kwh
            self.meter_at = now
            return
        delta = meter_kwh - self.meter_kwh
        if delta != 0:
            hours = max((now - self.meter_at).total_seconds(), 0.0) / 3600.0
            if delta < 0 or delta > METER_MAX_KW * hours + METER_SLACK_KWH:
                # A reset, a replaced meter, or a jump no wallbox can make: start
                # counting from here rather than add a figure that isn't this charge.
                self.meter_kwh = meter_kwh
                self.meter_at = now
                self._reset_meter_window(now)
                return
            previous = self.estimated_percent
            # ``hours`` runs from the last reading that *moved*, so a pause is
            # inside it; the model only charges overhead for time energy flowed.
            gained = model.points(delta, hours)
            estimate = previous + gained
            target = self.target_soc_percent
            if target is not None and previous <= target <= estimate:
                estimate = target
            self.estimated_percent = min(estimate, 100.0)
            self.meter_kwh = meter_kwh
            self.meter_at = now
            self._note_meter_rate(gained, hours)
        elif (now - self.meter_at).total_seconds() >= METER_IDLE_S:
            # Nothing has flowed for a while: a paused solar charge adds nothing.
            self.meter_rate_per_hour = 0.0
            self._reset_meter_window(now)

    def _reset_meter_window(self, now: Optional[datetime]) -> None:
        self.meter_window_points = 0.0
        self.meter_window_hours = 0.0
        if now is None:
            self.meter_rate_per_hour = None

    def _note_meter_rate(self, gained: float, hours: float) -> None:
        """Publish the meter's charging rate once enough time is covered.

        Each step's points are set against the time since the *previous*
        change, so a wallbox that reports once an hour gives its hourly rate,
        not one step's worth packed into a few minutes. Steps are pooled until
        :data:`METER_RATE_WINDOW_S` is covered, so a wallbox reporting every few
        seconds does not make the rate jump on every tick.
        """

        self.meter_window_points += gained
        self.meter_window_hours += hours
        if self.meter_window_hours * 3600.0 < METER_RATE_WINDOW_S:
            return
        self.meter_rate_per_hour = self.meter_window_points / self.meter_window_hours
        self.meter_window_points = 0.0
        self.meter_window_hours = 0.0

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

        if self.last_estimate_time is None or self.meter_model is not None:
            # While the meter drives, only ``follow_meter`` moves the estimate.
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
        if self.meter_model is not None and self.meter_rate_per_hour is not None:
            # The rate the estimate is actually climbing at, not BMW's power.
            return self.meter_rate_per_hour
        return self.rate_per_hour

    def minutes_to_target(self) -> Optional[float]:
        """Minutes until the estimate reaches the charge target, or ``None``.

        Only while the wallbox meter drives the estimate, so only at home: the
        meter's rate is what the estimate actually climbs at, and on an AC
        charge it holds steady to the target. Backtested on the i5's 11 kW
        charge to 80 % at 1-3 min off, where BMW's own figure ran 7-26 min long.
        Away from home the only rate is BMW's power, and a DC charge tapers
        hard as the pack fills -- a straight line from the current rate would
        promise far too early -- so there BMW's own figure is the better one.
        A paused (solar) charge has no rate and so no answer either.
        """

        rate = self.meter_rate_per_hour
        if self.meter_model is None or rate is None or rate <= 0:
            return None
        if self.estimated_percent is None or self.target_soc_percent is None:
            return None
        left = max(self.target_soc_percent - self.estimated_percent, 0.0)
        return left / rate * 60.0

    def _extrapolating(self) -> bool:
        return self.charging_active or self.restored_charging

    def _recalculate_rate(self) -> None:
        if not self._extrapolating():
            self.rate_per_hour = None
            return
        if self.last_power_w in (None, 0) or self.max_energy_kwh in (None, 0):
            return
        self.rate_per_hour = (self.last_power_w / 1000.0) / self.max_energy_kwh * 100.0
