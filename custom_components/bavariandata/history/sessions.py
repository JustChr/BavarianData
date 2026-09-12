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

from datetime import datetime, timedelta
from typing import Any, Optional

from .models import ChargingSession, _iso, _parse  # shared ISO (de)serialisation helpers

# A point per minute is plenty to draw a charging curve, and keeps a typical AC
# session well under a hundred points.
MIN_SAMPLE_INTERVAL_S = 60
# Hard bound on stored points. A very long session (or an unusually chatty
# stream) decimates instead of growing the store without limit; 480 points is
# eight hours at one per minute.
MAX_CURVE_POINTS = 480


# How far the SoC at session open may sit above the last reading taken before it
# before the charge is judged to have been running already. One percent is the
# resolution BMW streams SoC at and two is comfortably inside the jitter of two
# readings taken minutes apart; beyond that the pack genuinely gained charge we
# did not watch. Measured against real data: ordinary sessions sit at 0-1 points
# while a genuinely missed start showed 14.
LATE_START_SOC_MARGIN = 3.0


# Slack in the SoC-derived ceiling on session energy, in percentage points.
#
# BMW streams SoC as a whole percent, so a delta of *n* points really lies
# anywhere in [n-1, n+1] once both ends' rounding is allowed for -- and if the
# ends are truncated rather than rounded, up to n+2. Two points is therefore the
# smallest slack that cannot clip a genuine reading, which is the only thing
# that matters here: the ceiling exists to catch an integration that ran away by
# several kWh, not to shave the last hundred watt-hours off an honest one.
SOC_CEILING_MARGIN_PERCENT = 2.0


# How far before a session opened a state-of-charge reading may have been taken
# and still be treated as belonging to that session. BMW stamps its own
# timestamps on stream messages and they trail ours by seconds, so a reading
# that genuinely arrived at the plug-in can carry a timestamp fractionally
# before the moment the session opened. Two minutes absorbs that skew and
# nothing more: the readings this rejects are hours or days old.
SOC_SESSION_SKEW = timedelta(seconds=120)


# --- The bound wallbox meter ----------------------------------------------
# How far the meter's own delta may sit *below* the energy the pack absorbed
# before the reading is rejected. Below 1.0 by physics: the grid cannot deliver
# less than the battery took, so anything under the battery figure is either the
# wrong entity or a meter that was not counting this car. The 10 % allowance is
# for our own side of the comparison -- the battery figure is an integration of
# a bursty power stream bounded by a whole-percent SoC ceiling, not a
# measurement -- and not for real charging losses, which go the other way.
GRID_METER_MIN_RATIO = 0.9
# ...and how far above. AC charging losses run 5-15 %, more on a cold pack or a
# trickle charge; 80 % is not a loss, it is a different meter. This is the check
# that catches the commonest misconfiguration by far: binding the *house* import
# meter instead of the wallbox's, which would otherwise inflate every cost in
# the ledger by whatever else the house was doing.
GRID_METER_MAX_RATIO = 1.8
# Sessions this small are not worth cross-checking: on a 0.2 kWh top-up the
# ratio test is dominated by the meter's own resolution.
GRID_METER_MIN_KWH = 0.2


def measured_grid_kwh(
    meter_start: Optional[float],
    meter_end: Optional[float],
    *,
    battery_kwh: Optional[float] = None,
    cross_check: bool = True,
) -> Optional[float]:
    """What the bound wallbox meter says this charge drew, or ``None``.

    ``grid_kwh`` is the one field in a record that is *measured* rather than
    derived, and the whole ledger leans on that (cost, the efficiency layer's
    grid-side balance, the CSV export). So it is only ever filled in from a
    reading that survives every check available:

    - Both ends must be present and the meter must have gone **up**. A
      non-positive delta is a meter that reset, restarted at zero, or never
      counted this car -- never a charge that drew nothing, because a session
      only exists once charging was reported.
    - It must be *physically consistent* with the energy the pack absorbed.
      Deliver-less-than-absorbed is impossible; deliver nearly twice is a
      different meter. See the ratio constants.

    ``cross_check`` is turned off for a session whose own battery figure is
    known to be a floor (``late_start`` / ``interrupted``): comparing against a
    number that is admittedly short would reject exactly the measurements that
    are most valuable, since the meter is the only thing that saw the part we
    missed.
    """

    if meter_start is None or meter_end is None:
        return None
    delta = meter_end - meter_start
    if delta <= 0:
        return None
    if (
        cross_check
        and battery_kwh is not None
        and battery_kwh >= GRID_METER_MIN_KWH
        and not (
            battery_kwh * GRID_METER_MIN_RATIO
            <= delta
            <= battery_kwh * GRID_METER_MAX_RATIO
        )
    ):
        return None
    return round(delta, 3)


# How stale a snapshotted in-progress session may be and still be picked up as
# the *same* charge when Home Assistant comes back. A restart takes seconds and
# an update a few minutes; beyond this the car has very likely been unplugged
# and driven, so resuming would staple two unrelated charges together. The
# snapshot is still used -- it is closed as the record it already is, ending at
# its last sample -- just never continued.
OPEN_SESSION_MAX_AGE_S = 3600.0


def open_session_is_resumable(
    saved_at: Optional[datetime],
    now: datetime,
    *,
    max_age_s: float = OPEN_SESSION_MAX_AGE_S,
) -> bool:
    """Whether a snapshotted session may continue as the same charge.

    A snapshot with no timestamp, or one from the future (a clock change), is
    not resumable: without a trustworthy age there is no way to tell a
    thirty-second restart from a three-day outage, and the safe answer is to
    close the record we already have rather than to keep accruing into it.
    """

    if saved_at is None:
        return False
    age = (now - saved_at).total_seconds()
    if age < 0:
        return False
    return age <= max_age_s


def soc_is_from_session(
    reading_at: Optional[datetime],
    session_start: Optional[datetime],
    *,
    skew: timedelta = SOC_SESSION_SKEW,
) -> bool:
    """Whether a SoC reading was taken during the session it would describe.

    Not every car streams ``batteryManagement.header``. On one that doesn't, the
    only SoC ever held is whatever the REST bootstrap left there, and it never
    moves again -- so a session's opening and closing readings are the same stale
    number and the delta between them is a flat zero that says nothing about the
    charge.

    Reported as an arc that zero is merely wrong ("38 -> 38%"). Fed to
    :func:`energy_ceiling_kwh` it is destructive: a rise of zero pins every
    session to the margin alone, 1.4 kWh on a 71 kWh pack however much the car
    actually took. A real iX recorded four DC charges that way, each peaking
    above 100 kW and each filed as 1.4 kWh.

    So the reading has to postdate the session's own start. That is exactly the
    condition under which a delta against ``soc_start`` means anything, and it
    fails closed: without a live SoC there is no arc and no ceiling, and the
    energy figure stands on the power integration alone.
    """

    if reading_at is None or session_start is None:
        return False
    return reading_at >= session_start - skew


def energy_ceiling_kwh(
    soc_start: Optional[float],
    soc_now: Optional[float],
    capacity_kwh: Optional[float],
    *,
    margin_percent: float = SOC_CEILING_MARGIN_PERCENT,
) -> Optional[float]:
    """Most energy a session can plausibly have put into the pack, in kWh.

    Battery-side energy and state of charge measure the same thing, so the pack
    cannot have absorbed materially more than its SoC rose: ``ΔSoC × capacity``.
    That makes SoC a *physical bound* on the integrated power figure even though
    it is far too coarse to be the figure itself -- one percent is ~0.8 kWh on a
    78 kWh pack.

    The bound exists because BMW does not sample charging power evenly: it
    arrives in bursts with hour-long gaps, and a left Riemann sum holding one
    unrepresentative sample across such a gap runs away. A real session
    integrated a 3.54 kW reading for 162 minutes and claimed 11.10 kWh where the
    battery had taken 5.46.

    ``None`` when SoC or capacity is unknown -- an unbounded total is better than
    an invented limit. Never a *correction*: a session whose integration
    under-read is left alone, because nothing here can tell that it did.
    """

    if soc_start is None or soc_now is None or not capacity_kwh or capacity_kwh <= 0:
        return None
    gained = max(0.0, soc_now - soc_start)
    return (gained + margin_percent) / 100.0 * capacity_kwh


def _is_late_start(
    soc_before: Optional[float], soc_start: Optional[float]
) -> bool:
    """True when the pack was already fuller at open than we last saw it.

    Charging only raises SoC, so a session opening meaningfully above the last
    pre-charge reading means it had been running before we noticed -- a restart
    mid-charge, or a status transition the stream never sent.
    """

    if soc_before is None or soc_start is None:
        return False
    return soc_start - soc_before > LATE_START_SOC_MARGIN


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
        soc_before: Optional[float] = None,
    ) -> None:
        self.vin = vin
        self.start = start
        self.soc_start = soc_start
        # Left unknown until a reading actually arrives during the session.
        # Seeding it from ``soc_start`` would turn "we never saw the end" into a
        # confident claim that the charge moved the pack not at all -- which on a
        # car that doesn't stream SoC is every session it ever records.
        self.soc_end: Optional[float] = None
        self.target_soc = target_soc
        self.location = location
        self.location_assumed = location_assumed
        # The last SoC seen while the car was *not* charging. Only used to judge
        # whether this session caught the whole charge -- never to replace
        # ``soc_start``, because the energy integration starts when we notice the
        # charge too, and moving one end without the other would make the record
        # internally inconsistent rather than more accurate.
        self.late_start = _is_late_start(soc_before, soc_start)
        self.peak_power_kw: Optional[float] = None
        # True once this session has survived a restart (see
        # ``ChargingSession.interrupted``): set by :meth:`from_dict`, never here.
        self.interrupted = False
        self._curve: list[list[float]] = []
        self._interval = MIN_SAMPLE_INTERVAL_S
        self._last_offset: Optional[int] = None
        self._last_power: Optional[float] = None
        # Wall-clock time of the most recent sample. The curve stores offsets,
        # which are useless for deciding *when* a session that was interrupted
        # should be recorded as having ended -- that has to be the last moment we
        # actually watched it charge, not whenever we noticed it had stopped.
        self.last_sample_at: Optional[datetime] = None
        # The bound wallbox meter's own cumulative total, first and latest.
        # Sampled rather than read once at each end so the figure survives a
        # restart and so a charge that ends while the meter is briefly
        # unavailable still has a usable last reading.
        self.grid_meter_start: Optional[float] = None
        self.grid_meter_last: Optional[float] = None

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
        self.last_sample_at = at
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

    def note_grid_meter(self, reading: Optional[float]) -> None:
        """Record where the bound wallbox meter stands.

        The *first* reading becomes the baseline and is never revised: a later
        one would silently shorten the window the delta covers. A reading that
        has gone backwards is a meter reset, and the session cannot be measured
        across one -- so the baseline is dropped rather than producing a delta
        that spans a discontinuity.
        """

        if reading is None:
            return
        if self.grid_meter_start is None:
            self.grid_meter_start = reading
        elif (
            self.grid_meter_last is not None and reading < self.grid_meter_last
        ):
            self.grid_meter_start = None
            self.grid_meter_last = None
            return
        self.grid_meter_last = reading

    def measured_grid_kwh(self, *, battery_kwh: Optional[float] = None) -> Optional[float]:
        """The meter's verdict on this session, or ``None`` if unusable.

        The cross-check is skipped for a session whose battery-side energy is
        itself admittedly a floor -- see :func:`measured_grid_kwh`.
        """

        return measured_grid_kwh(
            self.grid_meter_start,
            self.grid_meter_last,
            battery_kwh=battery_kwh,
            cross_check=not (self.late_start or self.interrupted),
        )

    def to_dict(self) -> dict[str, Any]:
        """Snapshot the in-progress session so a restart cannot lose it.

        Everything the builder needs to carry on accumulating, including the
        downsampling state -- restoring a curve without its interval would make
        the resumed half of a long session denser than the first half.
        """

        return {
            "vin": self.vin,
            "start": _iso(self.start),
            "soc_start": self.soc_start,
            "soc_end": self.soc_end,
            "target_soc": self.target_soc,
            "location": self.location,
            "location_assumed": self.location_assumed,
            "late_start": self.late_start,
            "interrupted": self.interrupted,
            "peak_power_kw": self.peak_power_kw,
            "curve": [list(point) for point in self._curve],
            "interval": self._interval,
            "last_offset": self._last_offset,
            "last_power": self._last_power,
            "last_sample_at": _iso(self.last_sample_at),
            "grid_meter_start": self.grid_meter_start,
            "grid_meter_last": self.grid_meter_last,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["SessionBuilder"]:
        """Rebuild a snapshotted session, or ``None`` if it is unusable.

        Always comes back with ``interrupted`` set: by definition the only way
        to be restored is to have survived something that stopped us watching.
        ``late_start`` is taken from the snapshot rather than recomputed --
        whether the charge had been running before we noticed was decided when
        the session opened, and the pre-charge reading behind that judgement is
        long gone.
        """

        if not isinstance(data, dict):
            return None
        vin = data.get("vin")
        start = _parse(data.get("start"))
        if not vin or start is None:
            return None
        builder = cls(
            vin,
            start,
            soc_start=data.get("soc_start"),
            target_soc=data.get("target_soc"),
            location=data.get("location"),
            location_assumed=bool(data.get("location_assumed")),
        )
        builder.late_start = bool(data.get("late_start"))
        builder.interrupted = True
        builder.soc_end = data.get("soc_end")
        builder.peak_power_kw = data.get("peak_power_kw")
        builder._curve = [
            list(point) for point in data.get("curve") or [] if len(point) >= 2
        ]
        try:
            builder._interval = int(data.get("interval") or MIN_SAMPLE_INTERVAL_S)
        except (TypeError, ValueError):
            builder._interval = MIN_SAMPLE_INTERVAL_S
        last_offset = data.get("last_offset")
        builder._last_offset = None if last_offset is None else int(last_offset)
        builder._last_power = data.get("last_power")
        builder.last_sample_at = _parse(data.get("last_sample_at"))
        # Absent from snapshots written before the wallbox meter was wired up;
        # such a session simply gets no measured grid figure, which is the
        # behaviour it already had.
        builder.grid_meter_start = data.get("grid_meter_start")
        builder.grid_meter_last = data.get("grid_meter_last")
        return builder

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
        # Not one SoC reading landed while this charge ran, so nothing here
        # describes what it did to the pack -- ``soc_start`` is only the last
        # value we happened to be holding, which on a car that doesn't stream
        # SoC can be days old and belong to a different charge entirely. Drop
        # the arc rather than half-assert it, which also leaves the fields open
        # for BMW's own charging history to fill in on the next import (see
        # ``cardata_history._enrich_in_place``).
        soc_start = self.soc_start if self.soc_end is not None else None
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
            soc_start=soc_start,
            soc_end=self.soc_end,
            target_soc=self.target_soc,
            energy_kwh=None if energy_kwh is None else round(energy_kwh, 3),
            peak_power_kw=self.peak_power_kw,
            power_curve=self._curve,
            location=self.location,
            location_assumed=self.location_assumed,
            cost=cost,
            end_reason=reason,
            late_start=self.late_start,
            interrupted=self.interrupted,
            # Measured, not derived: only ever set from the bound wallbox meter
            # (or later, from BMW's own charging history during enrichment).
            grid_kwh=self.measured_grid_kwh(battery_kwh=energy_kwh),
        )
