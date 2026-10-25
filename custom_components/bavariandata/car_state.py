"""Everything the coordinator remembers about one car, in one place.

The coordinator used to keep each car's state as some fifty ``{vin: value}``
dicts side by side -- the SoC tracker in one, the session builder in another,
the trip's close timer in a third -- so "everything about this car" was spread
over the whole class, and a car's state was only as consistent as every place
that touched one of them. A :class:`Vehicle` holds it all, split by what it
is for:

=================  ==========================================================
``charge``         the SoC tracker, the estimate and its rate, charging
                   power, and the energy counters (they exist with history
                   off too)
``session``        the charging session being recorded: builder, cost, mix,
                   held energy, the restored-session and close debounce state
``trip``           the drive being recorded and how its close is decided
``gps``            the position stream: pairing BMW's two-part fix, movement
``report``         the vehicle-report debounce and the phone's route for it
=================  ==========================================================

A field that is ``None`` is what an absent dict key used to be. Nothing here
imports Home Assistant; the timers' cancel callbacks are opaque values.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from .history.energy_mix import MixAccumulator
from .history.held_energy import HeldEnergy
from .history.pricing import CostAccumulator
from .history.sessions import SessionBuilder
from .history.trip_builder import GpsTracker, TripBuilder
from .phone_route import PhoneRoute
from .soc_tracking import SocTracking

Cancel = Callable[[], None]


class _ReadOnlyWhenEmpty:
    """Refuses writes on :data:`NO_CAR`'s parts, so a read path can't fill one."""

    _frozen = False

    def __setattr__(self, name: str, value: Any) -> None:
        if self._frozen:
            raise AttributeError(f"{type(self).__name__} of no car is read-only")
        object.__setattr__(self, name, value)

    def take(self, name: str) -> Any:
        """A field's value, leaving it unset: what ``dict.pop(vin, None)`` was.

        On :data:`NO_CAR` every field is already unset, so nothing is written.
        """

        value = getattr(self, name)
        if value is not None:
            setattr(self, name, None)
        return value


@dataclass
class DescriptorState:
    value: Any
    unit: Optional[str]
    timestamp: Optional[str]


@dataclass
class ChargeState(_ReadOnlyWhenEmpty):
    """The battery and what flows into it, whether or not history is on."""

    tracking: Optional[SocTracking] = None
    soc_rate: Optional[float] = None
    soc_estimate: Optional[float] = None
    # What drives the estimate right now, "meter" or "stream" -- kept so a
    # switch alone, with the figure unchanged, still updates the entity.
    soc_source: Optional[str] = None
    # Whole minutes until a home charge reaches its target (``charge_eta``).
    charge_eta: Optional[int] = None
    charging_power_w: Optional[float] = None
    direct_power_w: Optional[float] = None
    ac_voltage_v: Optional[float] = None
    ac_current_a: Optional[float] = None
    ac_phase_count: Optional[int] = None
    # Energy delivered, integrated from effective charging power over time.
    # ``lifetime`` is monotonic (feeds the HA Energy dashboard); ``session``
    # resets when a new charging session starts.
    energy_lifetime_wh: Optional[float] = None
    energy_session_wh: Optional[float] = None
    energy_session_start: Optional[datetime] = None
    # SoC at the moment the current session opened, for the energy ceiling.
    energy_session_soc: Optional[float] = None
    # Unbounded integral behind ``energy_session_wh``. Kept so the exposed total
    # can rise again the moment a fresh SoC reading grants the room, instead of
    # the charge being written off while the reading was pending.
    energy_session_raw_wh: Optional[float] = None
    energy_last_time: Optional[datetime] = None

    def tracker(self) -> SocTracking:
        """The SoC tracker, made on first use."""

        if self.tracking is None:
            self.tracking = SocTracking()
        return self.tracking


@dataclass
class SessionState(_ReadOnlyWhenEmpty):
    """The charging session being recorded (``history`` on) and its close."""

    builder: Optional[SessionBuilder] = None
    cost: Optional[CostAccumulator] = None
    # Running source mix (PV / house battery / grid). Separate from the cost
    # accumulator because the two can be configured independently: a solar
    # share is worth having with no tariff set, and a tariff with no PV.
    mix: Optional[MixAccumulator] = None
    # What the SoC ceiling is holding back, with the mix and price each
    # increment arrived at (see ``history/held_energy.py``).
    held: Optional[HeldEnergy] = None
    # Set while a charge restored from the store at startup waits for the
    # stream's word either way: when the snapshot was taken.
    restored_at: Optional[datetime] = None
    # Closes a restored session the stream never confirmed; without it an
    # outage that spanned the end of a charge would leave the record open.
    restored_timer: Optional[Cancel] = None
    # When the in-progress charge was last written to the store. Refreshed on
    # a cadence: a charge runs for hours and each write rewrites the document.
    saved_at: Optional[datetime] = None
    # SoC a resumed session was last seen at before the restart. Set only
    # between the resume and the first SoC reading that can measure what the
    # gap cost (see ``_apply_gap_credit``).
    gap_credit_soc: Optional[float] = None
    # Where the bound wallbox meter stood when this session's cost was last
    # billed: a cursor, so no kilowatt-hour is billed twice.
    grid_meter_billed: Optional[float] = None
    # The close debounce: a pending timer means charging just stopped and we
    # wait to see whether it resumes (a flap) before committing the close.
    close_timer: Optional[Cancel] = None
    # When, and on which status, the charge behind a pending close stopped:
    # the record ends there, and the plugged-in hold counts from it.
    stopped: Optional[tuple[datetime, str]] = None


@dataclass
class ClosedTrip:
    """The trip stored last, with the readings it was closed on."""

    trip: Any
    mileage_start: Optional[float]
    odometer: Optional[float]
    soc: Optional[float]
    closed_at: datetime


@dataclass
class TripState(_ReadOnlyWhenEmpty):
    """The drive being recorded, and the evidence its open and close rest on."""

    builder: Optional[TripBuilder] = None
    # The stationary-close debounce's cancel callback.
    close_timer: Optional[Cancel] = None
    # When an unevidenced close was first held off. Set only while a trip is
    # held open through stream silence; bounded by ``TRIP_SILENT_HOLD_MAX_S``.
    held_since: Optional[datetime] = None
    # The last *positive* evidence the car is standing still: a fix showing
    # no movement, or an explicit ``isMoving: false``. Newer than the last
    # movement means "the car stopped"; older (or none) while the debounce
    # expires means "the stream stopped" -- see ``_hold_close_on_silence``.
    last_stop_evidence: Optional[datetime] = None
    # Driver-door state. ``None`` on cars that don't stream the door, where
    # detection falls back to GPS alone.
    driver_door_open: Optional[bool] = None
    # When the driver door opened during a trip too soon after the last
    # movement to count as an arrival on the spot -- the i5 sends it within a
    # second of the final fix. Kept until the car moves on.
    door_arrival: Optional[datetime] = None
    # The odometer and SoC as of the last fix that showed the car parked, for
    # the next trip's start. A car that streams in snapshots (an i4) sends both
    # ahead of the fix that first shows it moving, already moved on.
    parked_reading: Optional[tuple[Optional[float], Optional[float]]] = None
    # The trip stored last, while a reading that lands after its close may
    # still be its own (see ``_settle_late_reading``), and when one came in.
    closed: Optional[ClosedTrip] = None
    late_reading_at: Optional[datetime] = None
    pending_start: Optional[tuple[datetime, Optional[tuple[float, float]]]] = None
    # The zone the trip started in (name and entity id, for "left a zone"),
    # and whether this trip already left it.
    start_zone: Optional[tuple[str, str]] = None
    zone_left_fired: bool = False
    # Trip-capture bookkeeping (``trip_debug`` only): when the close timer is
    # due, for the countdown on each fix line, and the open trip's stats.
    close_due: Optional[datetime] = None
    capture: Optional[dict[str, Any]] = None
    # Serialises trip detection. Each MQTT message is its own task, so two
    # fixes can interleave at the geocode ``await`` in ``_open_trip`` and both
    # pass the "no open trip" check; the lock makes check-then-open atomic.
    lock: Optional[asyncio.Lock] = None


@dataclass
class GpsState(_ReadOnlyWhenEmpty):
    """The position stream: BMW's two-part fix and what it says about movement."""

    # Movement detection from the position stream: the i5 streams no
    # motion/ignition, and its ``trip.segment`` batches come mid-drive.
    tracker: Optional[GpsTracker] = None
    # The last fix classified as movement. A segment batch must not close a
    # trip the track still shows to be under way.
    last_move: Optional[datetime] = None
    # The last settled position (every complete fix, moving or not): where a
    # new trip's track starts, rather than at the first movement fix.
    last_position: Optional[tuple[float, float]] = None
    # Pairing of the two-message fix (lat and lon arrive separately): which
    # halves arrived since the last complete fix, since when (the stale escape
    # hatch), and BMW's timestamp of the last fix processed, which drops BMW's
    # duplicate redelivery of the same position.
    pending_parts: Optional[set[str]] = None
    pending_since: Optional[datetime] = None
    last_fix_ts: Optional[datetime] = None
    # Wall-clock of the last fix, for the inter-fix gap (``trip_debug``).
    last_fix_at: Optional[datetime] = None


@dataclass
class ReportState(_ReadOnlyWhenEmpty):
    """The vehicle-report debounce (``vehicle_report.py``) and the phone's route."""

    timer: Optional[Cancel] = None
    # The odometer reading the debounce is waiting on.
    pending: Optional[float] = None
    # Phone positions held for a report trip's route (``phone_route.py``).
    phone_route: Optional[PhoneRoute] = None


@dataclass
class Vehicle:
    """One car's whole state in the coordinator."""

    vin: str
    charge: ChargeState = field(default_factory=ChargeState)
    session: SessionState = field(default_factory=SessionState)
    trip: TripState = field(default_factory=TripState)
    gps: GpsState = field(default_factory=GpsState)
    report: ReportState = field(default_factory=ReportState)
    # Descriptors the stream delivered since this instance started --
    # situations are judged on these alone, never on a value restored after a
    # restart (``vehicle_triggers``).
    live_descriptors: set[str] = field(default_factory=set)


def _no_car() -> Vehicle:
    car = Vehicle("", live_descriptors=frozenset())  # type: ignore[arg-type]
    for part in (car.charge, car.session, car.trip, car.gps, car.report):
        object.__setattr__(part, "_frozen", True)
    return car


# What a read of a car the coordinator has never heard of sees: every field
# unset, and nothing writable -- reads must not create a car. Writes go through
# ``CardataCoordinator._car``.
NO_CAR = _no_car()
