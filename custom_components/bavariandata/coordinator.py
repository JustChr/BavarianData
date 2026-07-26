"""State coordinator for BMW CarData streaming payloads."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DIAGNOSTIC_LOG_INTERVAL,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_CHARGING_COMPLETE,
)
from .debug import debug_enabled
from .history.classify import classify_trip
from .history.health import usable_capacity
from .history.pricing import (
    MODE_FIXED,
    CostAccumulator,
    PricingConfig,
    billable_energy,
    resolve_cost,
)
from .history.sessions import SessionBuilder
from .history.trips import SOURCE_AUTO, place
from .history.trip_builder import (
    GpsTracker,
    TripBuilder,
    is_gps_movement,
    is_noise_trip,
)
from .units import normalize_unit

# Trip-detection descriptors (roadmap Phase 3). Motion is powertrain-agnostic and
# the cleanest start signal; ignition corroborates it; BMW's own completed-segment
# batch is the authoritative close and carries the per-trip statistics.
DESC_MOVING = "vehicle.isMoving"
DESC_IGNITION = "vehicle.drivetrain.engine.isIgnitionOn"
# Live position, the trip signal for cars that stream neither motion nor
# ignition. Latitude and longitude arrive as separate messages, so the current
# fix is read back from stored state rather than assumed present in one batch.
DESC_GPS_LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
DESC_GPS_LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
# The cumulative odometer descriptor differs by model: most cars stream
# ``vehicle.vehicle.mileage``; the i5 streams ``vehicle.vehicle.travelledDistance``
# instead (same thing -- a lifetime total in km). Trying both in order means trip
# distance and charging-cost mileage come from BMW's own figure wherever either
# is present, falling back to the GPS track only when neither is.
DESC_ODOMETER = ("vehicle.vehicle.mileage", "vehicle.vehicle.travelledDistance")
DESC_SEG_PREFIX = "vehicle.trip.segment.end."
DESC_SEG_DISTANCE = "vehicle.trip.segment.end.travelledDistance"
DESC_SEG_RECUP = "vehicle.trip.segment.accumulated.drivetrain.electricEngine.recuperationTotal"
DESC_SEG_CONSUMPTION = "vehicle.trip.segment.accumulated.drivetrain.electricEngine.energyConsumptionComfort"
DESC_SEG_ACCEL_STARS = "vehicle.trip.segment.accumulated.acceleration.starsAverage"
DESC_SEG_BRAKE_STARS = "vehicle.trip.segment.accumulated.chassis.brake.starsAverage"
DESC_SEG_ECO = "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveEcoPro"
DESC_SEG_ELECTRIC = "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveElectric"
# Driver door. When a car streams it (the i5 does), it brackets a drive far more
# precisely than GPS jitter: the door closing means the driver just got in (a
# drive is imminent), and the door opening after the car has stopped means they
# have arrived. Used as an *accelerator* only -- GPS stays the fallback for cars
# that don't stream it -- so trip detection never depends on it.
DESC_DRIVER_DOOR = "vehicle.cabin.door.row1.driver.isOpen"

# GPS-quality descriptors, folded into every ``[trip.gps]`` capture line. They
# bear directly on the detector's worst failure mode: a run of "no movement"
# fixes that is really the *fix* being lost, not the car stopping, splits one
# drive in two. Seeing fix state / satellite count / heading alongside each step
# tells the two apart. Same ``currentLocation.*`` group as lat/lon, so a car that
# streams position very likely streams these too (when selected).
DESC_GPS_FIX = "vehicle.cabin.infotainment.navigation.currentLocation.fixStatus"
DESC_GPS_SATS = "vehicle.cabin.infotainment.navigation.currentLocation.numberOfSatellites"
DESC_GPS_HEADING = "vehicle.cabin.infotainment.navigation.currentLocation.heading"

# Curated candidate trip-lifecycle / motion signals, surfaced on a ``[trip.watch]``
# line in trip-capture mode. NONE is used for detection today -- the capture
# exists to learn which (if any) the car actually streams and which cleanly
# bracket a drive, so a future detector can lean on something sturdier than GPS
# jitter + a 5-minute debounce. Chosen from the catalogue as the fields most
# likely to mark "driving": a speed, the HV system / plug state, the ignition
# trio (to confirm on a real drive they truly stay silent on this car), the
# driver door + central lock (entry/exit brackets), and active-navigation hints.
TRIP_WATCH_DESCRIPTORS = (
    "vehicle.isMoving",
    "vehicle.drivetrain.engine.isIgnitionOn",
    "vehicle.drivetrain.engine.isActive",
    "vehicle.vehicle.avgSpeed",
    "vehicle.vehicle.speedRange.lowerBound",
    "vehicle.vehicle.speedRange.upperBound",
    "vehicle.drivetrain.electricEngine.charging.hvStatus",
    "vehicle.drivetrain.electricEngine.charging.connectorStatus",
    "vehicle.cabin.door.row1.driver.isOpen",
    "vehicle.cabin.door.lock.status",
    "vehicle.cabin.infotainment.navigation.currentLocation.altitude",
    "vehicle.cabin.infotainment.navigation.destinationSet.distance",
    "vehicle.cabin.infotainment.navigation.remainingRange",
)

# Once the car has been at rest this long with no new segment batch, treat the
# trip as ended. Long enough to ride out a traffic light, short enough that a
# parked car's trip closes promptly.
TRIP_CLOSE_DEBOUNCE_S = 300

# A driver-door-close start marker is only honoured for opening a trip within
# this long -- so an unrelated door-close hours before a drive can't seed it.
PENDING_START_MAX_S = 300
# A driver-door-open ends an open trip only once the car has been stationary at
# least this long, so a door read that flickers open mid-move can't close a live
# drive. Short, because a driver opens their door only after actually stopping.
DOOR_ARRIVAL_STOP_S = 20
# BMW streams latitude and longitude as two separate messages ~1 s apart, so a
# fix is only complete once *both* have arrived. Detection waits for that (see
# ``_process_gps_signal``); this guard stops a frozen single component from
# stalling GPS processing forever if the pairing assumption ever fails.
GPS_PAIR_STALE_S = 120

# BMW's ``charging.status`` can briefly drop out of CHARGINGACTIVE mid-charge (a
# momentary NOCHARGING/PAUSED blip on the stream) and come straight back. Closing
# the session on the transition would split one plug-in into two records -- one
# then enriched by BMW's import, the other orphaned and double-counted -- so the
# close is debounced: if charging resumes within this window the same session
# continues instead. Long enough to ride out a flap, short enough that a genuine
# unplug still records promptly.
CHARGE_CLOSE_DEBOUNCE_S = 120

# A ``trip.segment.end.*`` field is only a completed-trip signal if its own
# timestamp is recent: BMW ships the "last trip end" fields (e.g. ``hvSoc``) in
# every telematic snapshot with the *previous* drive's timestamp, so an old one
# would otherwise close a live GPS trip the moment the car parks and polls.
SEG_FRESH_S = 900

# --- Trip-capture diagnostics (OPTION_TRIP_DEBUG) --------------------------
# A dedicated logger so the trip capture is visible on its own, independent of
# the generic ``debug_log`` toggle (which only raises the parent logger). Lines
# are emitted at INFO so they appear whatever the integration's log level, and
# only ever when the user has explicitly switched trip-capture mode on.
_TRIPLOG = logging.getLogger(f"{__name__}.tripcapture")
# Basename of the raw-batch NDJSON capture written to the HA config dir while
# trip-capture mode is on. One record per MQTT batch, replayable offline.
TRIP_CAPTURE_FILE = "bavariandata_trip_capture.ndjson"
# Stop appending once the capture file reaches this size, so a toggle left on by
# accident can't fill the disk. ~25 MB is many hours of driving.
TRIP_CAPTURE_MAX_BYTES = 25 * 1024 * 1024
# Every field of a segment/accumulated batch, captured whole (not just the
# ``end.*`` subset the detector reduces to a boolean) so we can tell whether any
# variant ever carries a real trip end.
DESC_SEG_CAPTURE_PREFIX = "vehicle.trip.segment."

# --- Stream-health repairs -------------------------------------------------
# A diagnostics download turns "it doesn't work" into 30-second triage, but a
# repair issue is what actually gets a stuck stream in front of the user. Both
# link to the Wiki's Troubleshooting page so the fix is one click away; the URL
# lives here rather than in the translation strings because hassfest forbids URLs
# inside translations/*.json.
WIKI_TROUBLESHOOTING = (
    "https://github.com/JustChr/BavarianData/wiki/Troubleshooting-and-FAQ"
)
# How long the stream may go without a single message before we raise a repair.
# Long enough that a car simply parked in a garage for a weekend (BMW streams
# most descriptors only on a state change) doesn't trip it, short enough that a
# genuinely broken selection surfaces within a couple of days.
NO_DATA_REPAIR_AFTER_S = 48 * 60 * 60
# How long an "unauthorized" (MQTT rc=5) condition must persist -- reauth having
# failed to clear it -- before it is a repair rather than a transient blip the
# reconnect logic is already handling.
UNAUTHORIZED_REPAIR_AFTER_S = 30 * 60
# Bounded ring buffer of connection transitions (status + rc/reason), surfaced in
# the diagnostics download so a reconnect storm is visible after the fact.
CONNECTION_HISTORY_LEN = 30

_LOGGER = logging.getLogger(__name__)


@dataclass
class DescriptorState:
    value: Any
    unit: Optional[str]
    timestamp: Optional[str]


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

    def update_max_energy(self, value: Optional[float]) -> None:
        if value is None:
            return
        self.max_energy_kwh = value
        if self.last_soc_percent is not None and self.energy_kwh is None:
            self.energy_kwh = value * self.last_soc_percent / 100.0
        self._recalculate_rate()

    def update_actual_soc(self, percent: float, timestamp: Optional[datetime]) -> None:
        self.last_soc_percent = percent
        ts = timestamp or datetime.now(timezone.utc)
        self.last_update = ts
        if self.max_energy_kwh:
            self.energy_kwh = self.max_energy_kwh * percent / 100.0
        else:
            self.energy_kwh = None
        self.estimated_percent = percent
        self.last_estimate_time = ts

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
        self.charging_active = status in {"CHARGINGACTIVE", "CHARGING_IN_PROGRESS"}
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

        delta_seconds = (now - self.last_estimate_time).total_seconds()
        if delta_seconds <= 0:
            return self.estimated_percent

        rate = self.current_rate_per_hour()
        if not self.charging_active or rate in (None, 0):
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
        if not self.charging_active:
            return None
        return self.rate_per_hour

    def _recalculate_rate(self) -> None:
        if not self.charging_active:
            self.rate_per_hour = None
            return
        if self.last_power_w in (None, 0) or self.max_energy_kwh in (None, 0):
            return
        self.rate_per_hour = (self.last_power_w / 1000.0) / self.max_energy_kwh * 100.0


@dataclass
class CardataCoordinator:
    hass: HomeAssistant
    entry_id: str
    data: Dict[str, Dict[str, DescriptorState]] = field(default_factory=dict)
    names: Dict[str, str] = field(default_factory=dict)
    device_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_message_at: Optional[datetime] = None
    last_telematic_api_at: Optional[datetime] = None
    connection_status: str = "connecting"
    last_disconnect_reason: Optional[str] = None
    diagnostic_interval: int = DIAGNOSTIC_LOG_INTERVAL
    watchdog_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    # Wall-clock the watchdog started, so "no data in 48h" has a baseline on a
    # stream that has never delivered a single message.
    stream_started_at: Optional[datetime] = field(default=None, init=False)
    # When a message last arrived per VIN (the global ``last_message_at`` can't
    # tell a healthy car from a silent one when several are configured).
    last_message_by_vin: Dict[str, datetime] = field(default_factory=dict, init=False)
    # Per-VIN, per-descriptor arrival tally for the diagnostics download. Live
    # counters only -- restored state doesn't count as an arrival.
    descriptor_counts: Dict[str, Dict[str, int]] = field(
        default_factory=dict, init=False
    )
    # Recent connection transitions (status + rc/reason + time) for diagnostics.
    connection_history: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=CONNECTION_HISTORY_LEN),
        init=False,
        repr=False,
    )
    # First time the stream went "unauthorized" without recovering since, driving
    # the persisting-rc=5 repair. Cleared on the next successful connect.
    _unauthorized_since: Optional[datetime] = field(default=None, init=False)
    # Latched repair state so the watchdog only calls the issue registry on a
    # transition rather than every tick.
    _no_data_issue_active: bool = field(default=False, init=False)
    _unauthorized_issue_active: bool = field(default=False, init=False)
    _soc_tracking: Dict[str, SocTracking] = field(default_factory=dict, init=False)
    _soc_rate: Dict[str, float] = field(default_factory=dict, init=False)
    _soc_estimate: Dict[str, float] = field(default_factory=dict, init=False)
    _testing_soc_tracking: Dict[str, SocTracking] = field(
        default_factory=dict, init=False
    )
    _testing_soc_estimate: Dict[str, float] = field(default_factory=dict, init=False)
    _avg_aux_power_w: Dict[str, float] = field(default_factory=dict, init=False)
    _charging_power_w: Dict[str, float] = field(default_factory=dict, init=False)
    _direct_power_w: Dict[str, float] = field(default_factory=dict, init=False)
    _ac_voltage_v: Dict[str, float] = field(default_factory=dict, init=False)
    _ac_current_a: Dict[str, float] = field(default_factory=dict, init=False)
    _ac_phase_count: Dict[str, int] = field(default_factory=dict, init=False)
    # Energy delivered, integrated from effective charging power over time.
    # ``lifetime`` is monotonic (feeds the HA Energy dashboard); ``session``
    # resets when a new charging session starts.
    _energy_lifetime_wh: Dict[str, float] = field(default_factory=dict, init=False)
    _energy_session_wh: Dict[str, float] = field(default_factory=dict, init=False)
    _energy_session_start: Dict[str, datetime] = field(default_factory=dict, init=False)
    _energy_last_time: Dict[str, datetime] = field(default_factory=dict, init=False)
    # Charging-session recording. ``history`` is injected during setup; when it
    # is None (or pricing isn't configured) recording degrades quietly rather
    # than breaking the stream -- history is a bonus, not a prerequisite.
    history: Optional[Any] = None
    # Descriptor-coverage self-test ("Beyond the roadmap"). Records which selected
    # descriptors have actually streamed; optional, degrades quietly when absent.
    coverage: Optional[Any] = None
    pricing: PricingConfig = field(default_factory=PricingConfig)
    # Trip recording (roadmap Phase 3). ``geocoder`` and ``work_zone_entity`` are
    # injected/updated from options; both are optional and degrade quietly.
    geocoder: Optional[Any] = None
    work_zone_entity: Optional[str] = None
    # Record each trip's GPS route (opt-in, off by default). The only setting that
    # persists raw coordinates; refreshed from options on reload / options change.
    record_trip_track: bool = False
    # Trip-capture diagnostic mode (opt-in, off by default). Emits the rich
    # ``[trip.*]`` capture and the NDJSON file; refreshed from options like above.
    trip_debug: bool = False
    _session_builders: Dict[str, SessionBuilder] = field(
        default_factory=dict, init=False
    )
    _session_costs: Dict[str, CostAccumulator] = field(
        default_factory=dict, init=False
    )
    _trip_builders: Dict[str, TripBuilder] = field(default_factory=dict, init=False)
    # Cancel callbacks for the per-VIN stationary-close debounce timers.
    _trip_close_timers: Dict[str, Any] = field(default_factory=dict, init=False)
    # Cancel callbacks for the per-VIN charging-session close debounce timers; a
    # pending timer means charging just stopped and we're waiting to see whether
    # it resumes (a status flap) before committing the close.
    _charge_close_timers: Dict[str, Any] = field(default_factory=dict, init=False)
    # Per-VIN GPS movement trackers. Trips are detected from the live position
    # stream because the i5 streams no motion/ignition and its ``trip.segment``
    # batches are not trip-end markers -- it emits them repeatedly *mid-drive*
    # (see ``_process_gps_signal`` and ``_process_trip_signals``).
    _gps_trackers: Dict[str, GpsTracker] = field(default_factory=dict, init=False)
    # Wall-clock of the last GPS fix classified as movement, per VIN. Guards the
    # segment-close path: a segment batch must not close a trip the GPS track
    # still shows to be under way.
    _last_gps_move: Dict[str, datetime] = field(default_factory=dict, init=False)
    # Last settled GPS position per VIN (updated on every complete fix, moving or
    # not). It is what a new trip's track is seeded from, so the route starts
    # where the car actually was parked rather than at the first movement fix.
    _last_gps_position: Dict[str, tuple[float, float]] = field(
        default_factory=dict, init=False
    )
    # Timestamps of the last processed latitude / longitude, used to pair BMW's
    # two-message fix (lat and lon arrive separately): a fix is processed only
    # once both have advanced, which also drops the duplicate "same position"
    # step the old per-message handling produced.
    _gps_last_lat_ts: Dict[str, datetime] = field(default_factory=dict, init=False)
    _gps_last_lon_ts: Dict[str, datetime] = field(default_factory=dict, init=False)
    # Driver-door state and a pending door-close start marker (see
    # ``_process_door_signal``). Both optional: absent on cars that don't stream
    # the door, in which case detection falls back to GPS alone.
    _driver_door_open: Dict[str, bool] = field(default_factory=dict, init=False)
    _pending_start: Dict[str, tuple[datetime, Optional[tuple[float, float]]]] = field(
        default_factory=dict, init=False
    )
    # Trip-capture bookkeeping (only populated in ``trip_debug`` mode). The last
    # GPS fix's wall-clock (for the inter-fix gap), the scheduled close-timer fire
    # time (for the countdown shown on each fix line), per-open-trip capture stats
    # for the post-mortem, and a one-shot guard so the file-size warning is logged
    # only once.
    _last_gps_fix_at: Dict[str, datetime] = field(default_factory=dict, init=False)
    _trip_close_due: Dict[str, datetime] = field(default_factory=dict, init=False)
    _trip_capture: Dict[str, dict] = field(default_factory=dict, init=False)
    _trip_capture_warned: bool = field(default=False, init=False)
    # Per-VIN lock serialising trip detection. Each MQTT message is dispatched as
    # its own task (``run_coroutine_threadsafe`` in ``stream``), so two GPS fixes
    # can interleave at the geocode ``await`` inside ``_open_trip`` and both pass
    # the ``not open_trip`` check -- opening the trip twice and dropping the first
    # builder. The lock makes "check-then-open" (and close) atomic per vehicle.
    _trip_locks: Dict[str, asyncio.Lock] = field(default_factory=dict, init=False)

    @property
    def signal_new_sensor(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_new_sensor"

    @property
    def signal_new_binary(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_new_binary"

    @property
    def signal_update(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_update"

    @property
    def signal_diagnostics(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_diagnostics"

    @property
    def signal_soc_estimate(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_soc_estimate"

    @property
    def signal_telematic_api(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_telematic_api"

    @property
    def signal_energy(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_energy"

    @property
    def signal_history(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_history"

    @property
    def signal_trips(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_trips"

    def _get_testing_tracking(self, vin: str) -> SocTracking:
        return self._testing_soc_tracking.setdefault(vin, SocTracking())

    def _adjust_power_for_testing(self, vin: str, power_w: float) -> float:
        aux_power = self._avg_aux_power_w.get(vin)
        if aux_power is None:
            return max(power_w, 0.0)
        return max(power_w - aux_power, 0.0)

    def _update_testing_power(self, vin: str, timestamp: Optional[datetime]) -> None:
        raw_power = self._charging_power_w.get(vin)
        if raw_power is None:
            return
        testing_tracking = self._get_testing_tracking(vin)
        testing_tracking.update_power(
            self._adjust_power_for_testing(vin, raw_power), timestamp
        )

    def _set_direct_power(
        self, vin: str, power_w: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        if power_w is None:
            self._direct_power_w.pop(vin, None)
        else:
            self._direct_power_w[vin] = max(power_w, 0.0)
        self._apply_effective_power(vin, timestamp)

    def _set_ac_voltage(
        self, vin: str, voltage_v: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        if voltage_v is None:
            self._ac_voltage_v.pop(vin, None)
        else:
            self._ac_voltage_v[vin] = max(voltage_v, 0.0)
        self._apply_effective_power(vin, timestamp)

    def _set_ac_current(
        self, vin: str, current_a: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        if current_a is None:
            self._ac_current_a.pop(vin, None)
        else:
            self._ac_current_a[vin] = max(current_a, 0.0)
        self._apply_effective_power(vin, timestamp)

    def _set_ac_phase(
        self, vin: str, phase_value: Optional[Any], timestamp: Optional[datetime]
    ) -> None:
        phase_count: Optional[int] = None
        if phase_value is None:
            phase_count = None
        elif isinstance(phase_value, (int, float)):
            try:
                parsed = int(phase_value)
            except (TypeError, ValueError):
                parsed = None
            phase_count = parsed if parsed and parsed > 0 else None
        elif isinstance(phase_value, str):
            match = re.match(r"(\d+)", phase_value)
            if match:
                try:
                    parsed = int(match.group(1))
                except (TypeError, ValueError):
                    parsed = None
                phase_count = parsed if parsed and parsed > 0 else None
        if phase_count is None:
            self._ac_phase_count.pop(vin, None)
        else:
            self._ac_phase_count[vin] = phase_count
        self._apply_effective_power(vin, timestamp)

    def _derive_ac_power(self, vin: str) -> Optional[float]:
        voltage = self._ac_voltage_v.get(vin)
        current = self._ac_current_a.get(vin)
        phases = self._ac_phase_count.get(vin)
        if voltage is None or current is None or phases is None:
            return None
        return max(voltage * current * phases, 0.0)

    def _compute_effective_power(self, vin: str) -> Optional[float]:
        direct = self._direct_power_w.get(vin)
        if direct is not None:
            return direct
        return self._derive_ac_power(vin)

    def _apply_effective_power(
        self, vin: str, timestamp: Optional[datetime]
    ) -> None:
        tracking = self._soc_tracking.setdefault(vin, SocTracking())
        testing_tracking = self._get_testing_tracking(vin)
        effective_power = self._compute_effective_power(vin)
        if effective_power is None:
            self._charging_power_w.pop(vin, None)
            return
        self._charging_power_w[vin] = effective_power
        tracking.update_power(effective_power, timestamp)
        testing_tracking.update_power(
            self._adjust_power_for_testing(vin, effective_power), timestamp
        )

    async def async_handle_message(self, payload: Dict[str, Any]) -> None:
        vin = payload.get("vin")
        data = payload.get("data") or {}
        if not vin or not isinstance(data, dict):
            return

        vehicle_state = self.data.setdefault(vin, {})
        new_binary: list[str] = []
        new_sensor: list[str] = []
        # Descriptors carrying a value in this batch, for the coverage self-test.
        seen_descriptors: list[str] = []

        self.last_message_at = datetime.now(timezone.utc)
        self.last_message_by_vin[vin] = self.last_message_at

        if debug_enabled():
            _LOGGER.debug("Processing message for VIN %s: %s", vin, list(data.keys()))

        tracking = self._soc_tracking.setdefault(vin, SocTracking())
        testing_tracking = self._get_testing_tracking(vin)
        now = datetime.now(timezone.utc)

        # Trip signals seen in this batch; acted on after the loop so opening or
        # closing a trip (which may await a geocode) never re-enters mid-iteration.
        motion_value: Optional[bool] = None
        ignition_value: Optional[bool] = None
        door_value: Optional[bool] = None
        segment_fresh = False
        gps_seen = False
        gps_ts: Optional[datetime] = None

        for descriptor, descriptor_payload in data.items():
            if not isinstance(descriptor_payload, dict):
                continue
            value = descriptor_payload.get("value")
            unit = normalize_unit(descriptor_payload.get("unit"))
            timestamp = descriptor_payload.get("timestamp")
            parsed_ts = dt_util.parse_datetime(timestamp) if timestamp else None
            if value is None:
                if descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                    tracking.update_target_soc(None, parsed_ts)
                    testing_tracking.update_target_soc(None, parsed_ts)
                elif descriptor == "vehicle.vehicle.avgAuxPower":
                    self._avg_aux_power_w.pop(vin, None)
                    self._update_testing_power(vin, parsed_ts)
                elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
                    self._set_direct_power(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                    self._set_ac_voltage(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                    self._set_ac_current(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                    self._set_ac_phase(vin, None, parsed_ts)
                continue
            is_new = descriptor not in vehicle_state
            vehicle_state[descriptor] = DescriptorState(value=value, unit=unit, timestamp=timestamp)
            seen_descriptors.append(descriptor)
            vin_counts = self.descriptor_counts.setdefault(vin, {})
            vin_counts[descriptor] = vin_counts.get(descriptor, 0) + 1
            if descriptor == "vehicle.vehicleIdentification.basicVehicleData" and isinstance(value, dict):
                self.apply_basic_data(vin, value)
            if is_new:
                if isinstance(value, bool):
                    new_binary.append(descriptor)
                else:
                    new_sensor.append(descriptor)
            if descriptor == "vehicle.drivetrain.batteryManagement.header":
                try:
                    percent = float(value)
                except (TypeError, ValueError):
                    pass
                else:
                    tracking.update_actual_soc(percent, parsed_ts)
                    testing_tracking.update_actual_soc(percent, parsed_ts)
            elif descriptor == "vehicle.drivetrain.batteryManagement.maxEnergy":
                try:
                    max_energy = float(value)
                except (TypeError, ValueError):
                    pass
                else:
                    tracking.update_max_energy(max_energy)
                    testing_tracking.update_max_energy(max_energy)
            elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
                try:
                    power_w = float(value)
                except (TypeError, ValueError):
                    self._set_direct_power(vin, None, parsed_ts)
                else:
                    self._set_direct_power(vin, power_w, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.status":
                if isinstance(value, str):
                    was_charging = tracking.charging_active
                    tracking.update_status(value)
                    testing_tracking.update_status(value)
                    self._fire_charging_event(vin, was_charging, tracking, value)
            elif descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                try:
                    target = float(value)
                except (TypeError, ValueError):
                    tracking.update_target_soc(None, parsed_ts)
                    testing_tracking.update_target_soc(None, parsed_ts)
                else:
                    tracking.update_target_soc(target, parsed_ts)
                    testing_tracking.update_target_soc(target, parsed_ts)
            elif descriptor == "vehicle.vehicle.avgAuxPower":
                aux_w: Optional[float] = None
                try:
                    aux_value = float(value)
                except (TypeError, ValueError):
                    pass
                else:
                    if isinstance(unit, str) and unit.lower() == "w":
                        aux_w = aux_value
                    else:
                        aux_w = aux_value * 1000.0
                if aux_w is not None:
                    aux_w = max(aux_w, 0.0)
                if aux_w is None:
                    self._avg_aux_power_w.pop(vin, None)
                else:
                    self._avg_aux_power_w[vin] = aux_w
                self._update_testing_power(vin, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                try:
                    voltage_v = float(value)
                except (TypeError, ValueError):
                    self._set_ac_voltage(vin, None, parsed_ts)
                else:
                    self._set_ac_voltage(vin, voltage_v, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                try:
                    current_a = float(value)
                except (TypeError, ValueError):
                    self._set_ac_current(vin, None, parsed_ts)
                else:
                    self._set_ac_current(vin, current_a, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                self._set_ac_phase(vin, value, parsed_ts)
            elif descriptor == DESC_MOVING and isinstance(value, bool):
                motion_value = value
            elif descriptor == DESC_IGNITION and isinstance(value, bool):
                ignition_value = value
            elif descriptor == DESC_DRIVER_DOOR and isinstance(value, bool):
                door_value = value
            if descriptor in (DESC_GPS_LAT, DESC_GPS_LON):
                gps_seen = True
                if parsed_ts is not None:
                    gps_ts = parsed_ts
            if descriptor.startswith(DESC_SEG_PREFIX):
                # Only a segment carrying a *recent* timestamp is a completed
                # trip; the stale "last trip end" fields BMW repeats in every
                # snapshot must not be mistaken for one (see ``SEG_FRESH_S``).
                if (
                    parsed_ts is not None
                    and (now - parsed_ts).total_seconds() <= SEG_FRESH_S
                ):
                    segment_fresh = True

            async_dispatcher_send(self.hass, self.signal_update, vin, descriptor)

        for descriptor in new_sensor:
            async_dispatcher_send(self.hass, self.signal_new_sensor, vin, descriptor)
        for descriptor in new_binary:
            async_dispatcher_send(self.hass, self.signal_new_binary, vin, descriptor)

        if self.coverage is not None and seen_descriptors:
            # Bookkeeping must never break the stream.
            try:
                self.coverage.note_seen(vin, seen_descriptors)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Coverage tracking failed for %s", vin)

        self._apply_soc_estimate(vin, now)
        if self._integrate_energy(vin, now):
            async_dispatcher_send(self.hass, self.signal_energy, vin)

        # Serialise per-VIN so concurrent messages can't double-open a trip while
        # one is awaiting a geocode (see ``_trip_locks``). The timer-driven close
        # stays outside the lock -- it pops its builder before any await, so it
        # can't corrupt an open running here, and taking the lock there would
        # deadlock the in-lock segment/stationary closes.
        lock = self._trip_locks.get(vin)
        if lock is None:
            lock = self._trip_locks[vin] = asyncio.Lock()
        async with lock:
            await self._process_trip_signals(
                vin, now, motion_value, ignition_value, segment_fresh
            )
            if door_value is not None:
                await self._process_door_signal(vin, now, door_value)
            if gps_seen:
                await self._process_gps_signal(vin, now, gps_ts)

        # Trip-capture diagnostics: the raw firehose + NDJSON file, outside the
        # trip lock (they only read state) and strictly gated on the opt-in.
        if self.trip_debug:
            self._capture_batch(vin, data, now)
            with suppress(Exception):  # file IO must never break the stream
                await self._capture_to_file(vin, data, now)

        async_dispatcher_send(self.hass, self.signal_diagnostics)

    def _integrate_energy(self, vin: str, now: datetime) -> bool:
        """Accumulate delivered energy from effective charging power.

        Assumes the last-sampled power held constant since the previous tick (a
        left Riemann sum); the stream samples often enough that the error stays
        small. ``_energy_last_time`` is advanced every call — including while
        idle — so a resumed session never integrates over a stale gap.
        """
        last = self._energy_last_time.get(vin)
        self._energy_last_time[vin] = now
        tracking = self._soc_tracking.get(vin)
        power_w = self._charging_power_w.get(vin)
        if last is None or tracking is None or not tracking.charging_active:
            return False
        if not power_w or power_w <= 0:
            return False
        delta_seconds = (now - last).total_seconds()
        if delta_seconds <= 0:
            return False
        wh = power_w * (delta_seconds / 3600.0)
        self._energy_lifetime_wh[vin] = self._energy_lifetime_wh.get(vin, 0.0) + wh
        self._energy_session_wh[vin] = self._energy_session_wh.get(vin, 0.0) + wh
        self._record_energy_delta(vin, now, power_w, wh / 1000.0)
        return True

    def _record_energy_delta(
        self, vin: str, now: datetime, power_w: float, kwh: float
    ) -> None:
        """Feed the session record from the same delta that moved the counters.

        Sharing the integration step is deliberate: the curve, the energy and
        the cost then describe exactly the same samples and cannot disagree.
        """

        builder = self._session_builders.get(vin)
        if builder is not None:
            builder.sample(now, power_w / 1000.0)
            tracking = self._soc_tracking.get(vin)
            if tracking is not None:
                builder.note_soc(tracking.last_soc_percent)

        accumulator = self._session_costs.get(vin)
        if accumulator is not None:
            billable, _source = billable_energy(
                battery_kwh=kwh, loss_percent=self.pricing.loss_percent
            )
            accumulator.add(billable, self._current_price())

    def get_lifetime_energy_kwh(self, vin: str) -> Optional[float]:
        value = self._energy_lifetime_wh.get(vin)
        return None if value is None else round(value / 1000.0, 3)

    def get_session_energy_kwh(self, vin: str) -> Optional[float]:
        value = self._energy_session_wh.get(vin)
        return None if value is None else round(value / 1000.0, 3)

    def get_session_start(self, vin: str) -> Optional[datetime]:
        return self._energy_session_start.get(vin)

    def restore_lifetime_energy(self, vin: str, kwh: float) -> None:
        """Seed the lifetime accumulator from a restored sensor state."""
        if kwh is None:
            return
        self._energy_lifetime_wh.setdefault(vin, kwh * 1000.0)

    def restore_session_energy(
        self, vin: str, kwh: float, start: Optional[datetime] = None
    ) -> None:
        if kwh is not None:
            self._energy_session_wh.setdefault(vin, kwh * 1000.0)
        if start is not None:
            self._energy_session_start.setdefault(vin, start)

    def _fire_charging_event(
        self,
        vin: str,
        was_charging: bool,
        tracking: SocTracking,
        status: str,
    ) -> None:
        """Fire a HA bus event when a charging session begins or ends.

        A stopped session that reached (or exceeded) the configured target SoC
        also fires a dedicated ``complete`` event so automations can distinguish
        "finished as planned" from "unplugged early".
        """
        now_charging = tracking.charging_active
        if was_charging == now_charging:
            return
        soc = (
            tracking.estimated_percent
            if tracking.estimated_percent is not None
            else tracking.last_soc_percent
        )
        payload: Dict[str, Any] = {
            "vin": vin,
            "entry_id": self.entry_id,
            "status": status,
            "soc": None if soc is None else round(soc, 1),
            "target_soc": tracking.target_soc_percent,
        }
        if now_charging:
            # Charging resumed while a close was pending: this was a brief status
            # flap, not a new plug-in. Cancel the pending close and let the same
            # session continue -- no fresh STARTED event, no split record.
            if self._cancel_charge_close_timer(vin):
                if debug_enabled():
                    _LOGGER.debug(
                        "[charge] %s RESUME after status flap; keeping session",
                        vin,
                    )
                return
            # New session: zero the session accumulator and stamp its start so
            # the session-energy sensor reports a fresh last_reset.
            started_at = datetime.now(timezone.utc)
            self._energy_session_wh[vin] = 0.0
            self._energy_session_start[vin] = started_at
            self._open_session_record(vin, tracking, started_at)
            self.hass.bus.async_fire(EVENT_CHARGING_STARTED, payload)
            return
        # Charging stopped. Debounce the close so a momentary NOCHARGING blip
        # that comes straight back doesn't split one plug-in into two records.
        self._arm_charge_close_timer(vin, status)

    def _arm_charge_close_timer(self, vin: str, status: str) -> None:
        """Commit the session close only if charging stays stopped for the grace.

        Re-arming on each stop transition restarts the countdown, so the close
        fires ``CHARGE_CLOSE_DEBOUNCE_S`` after the *last* stop; a resume in
        between cancels it (see :meth:`_fire_charging_event`).
        """

        self._cancel_charge_close_timer(vin)

        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, and _finalize_charge_close
        # touches loop-only helpers (async_dispatcher_send).
        @callback
        def _fire(_now) -> None:
            self._charge_close_timers.pop(vin, None)
            self._finalize_charge_close(vin, status)

        self._charge_close_timers[vin] = async_call_later(
            self.hass, CHARGE_CLOSE_DEBOUNCE_S, _fire
        )

    def _cancel_charge_close_timer(self, vin: str) -> bool:
        """Cancel a pending close; return whether one was actually pending."""

        cancel = self._charge_close_timers.pop(vin, None)
        if cancel is None:
            return False
        cancel()
        return True

    def _finalize_charge_close(self, vin: str, status: str) -> None:
        """Actually close the debounced session and fire the stop events."""

        tracking = self._soc_tracking.get(vin)
        if tracking is None:
            return
        soc = (
            tracking.estimated_percent
            if tracking.estimated_percent is not None
            else tracking.last_soc_percent
        )
        payload: Dict[str, Any] = {
            "vin": vin,
            "entry_id": self.entry_id,
            "status": status,
            "soc": None if soc is None else round(soc, 1),
            "target_soc": tracking.target_soc_percent,
        }
        # Close the record first so its summary can ride along on the event --
        # automations then get the cost without a second lookup.
        session = self._close_session_record(vin, tracking, status)
        if session is not None:
            payload["energy_kwh"] = session.energy_kwh
            payload["cost"] = session.cost
            payload["session_id"] = session.id
        self.hass.bus.async_fire(EVENT_CHARGING_STOPPED, payload)
        target = tracking.target_soc_percent
        if target is not None and soc is not None and soc >= target - 1.0:
            self.hass.bus.async_fire(EVENT_CHARGING_COMPLETE, payload)

    def _open_session_record(
        self, vin: str, tracking: SocTracking, started_at: datetime
    ) -> None:
        if self.history is None:
            return
        location = self._charging_location(vin)
        self._session_builders[vin] = SessionBuilder(
            vin,
            started_at,
            soc_start=tracking.last_soc_percent,
            target_soc=tracking.target_soc_percent,
            location=location,
            location_assumed=location is None,
        )
        self._session_costs[vin] = CostAccumulator(currency=self.pricing.currency)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s OPEN at %s zone=%s soc=%s target=%s "
                "pricing=%s price_now=%s",
                vin,
                started_at.isoformat(),
                (location or {}).get("zone"),
                tracking.last_soc_percent,
                tracking.target_soc_percent,
                self.pricing.mode if self.pricing.enabled else "disabled",
                self._current_price(),
            )

    def _close_session_record(self, vin: str, tracking: SocTracking, status: str):
        builder = self._session_builders.pop(vin, None)
        accumulator = self._session_costs.pop(vin, None)
        if builder is None or self.history is None:
            return None

        energy_kwh = self.get_session_energy_kwh(vin)
        session = builder.close(
            datetime.now(timezone.utc),
            soc_end=tracking.last_soc_percent,
            energy_kwh=energy_kwh,
            cost=resolve_cost(accumulated=accumulator.as_cost() if accumulator else None),
            reason=status,
        )
        session.mileage_km = self._odometer_km(vin)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s CLOSE(%s) energy=%s kWh soc=%s->%s zone=%s "
                "assumed=%s odo=%s cost=%s priced/unpriced=%s/%s",
                vin,
                status,
                session.energy_kwh,
                session.soc_start,
                session.soc_end,
                (session.location or {}).get("zone"),
                session.location_assumed,
                session.mileage_km,
                session.cost,
                getattr(accumulator, "priced_kwh", None),
                getattr(accumulator, "unpriced_kwh", None),
            )
        try:
            self.history.add_session(session)
        except Exception:  # noqa: BLE001 - never let bookkeeping break the stream
            _LOGGER.exception("Could not record charging session for %s", vin)
            return None
        async_dispatcher_send(self.hass, self.signal_history, vin)
        self._log_battery_health(vin)
        return session

    def _log_battery_health(self, vin: str) -> None:
        """Explain the battery-health estimate after each charge (debug only).

        The sensor deliberately shows only "Learning (n/10)" until it is sure, so
        without this there is no way to see *why* -- too few wide-SoC samples, or
        an estimate that disagrees with BMW's own capacity figure.
        """

        if not debug_enabled() or self.history is None:
            return
        health = usable_capacity(
            self.history.sessions(vin),
            nominal_kwh=self.battery_nominal_kwh(vin),
            sanity_kwh=self.battery_capacity_kwh(vin),
        )
        _LOGGER.debug(
            "[health] %s samples=%s usable=%s confident=%s suspicious=%s "
            "nominal=%s bmw_capacity=%s",
            vin,
            health.samples,
            health.usable_kwh,
            health.confident,
            health.suspicious,
            health.nominal_kwh,
            self.battery_capacity_kwh(vin),
        )

    def _odometer_km(self, vin: str) -> Optional[float]:
        """Cumulative odometer in km, for trip distance and distance-based costing.

        Reads whichever odometer descriptor the car actually streams (see
        ``DESC_ODOMETER``) -- notably the i5's ``travelledDistance``, which the
        trip builder prefers over its GPS-track fallback. Coarse (1 km integer
        steps), so a sub-km trip that doesn't tick it still falls through to GPS.
        """

        for descriptor in DESC_ODOMETER:
            state = self.get_state(vin, descriptor)
            if state is None or state.value is None:
                continue
            try:
                return float(state.value)
            except (TypeError, ValueError):
                continue
        return None

    def _battery_kwh(self, vin: str, descriptor: str) -> Optional[float]:
        state = self.get_state(vin, descriptor)
        if state is None or state.value is None:
            return None
        try:
            value = float(state.value)
        except (TypeError, ValueError):
            return None
        # BMW streams a sentinel (0 / "INVALID") before the value is known; a
        # zero capacity would poison both the vs-new ratio and the sanity check.
        return value if value > 0 else None

    def battery_nominal_kwh(self, vin: str) -> Optional[float]:
        """As-new HV battery size in kWh, for the battery-health vs-new figure."""

        return self._battery_kwh(
            vin, "vehicle.drivetrain.batteryManagement.batterySizeMax"
        )

    def battery_capacity_kwh(self, vin: str) -> Optional[float]:
        """BMW's own current full-pack capacity in kWh, used to sanity-check ours.

        The SoC tracker already learns this from the ``maxEnergy`` stream (it
        scales SoC into energy with it), so prefer that; fall back to the raw
        descriptor for a car that streams it without an active SoC track yet.
        """

        tracking = self._soc_tracking.get(vin)
        if tracking is not None and tracking.max_energy_kwh:
            return tracking.max_energy_kwh
        return self._battery_kwh(vin, "vehicle.drivetrain.batteryManagement.maxEnergy")

    def _charging_location(self, vin: str) -> Optional[Dict[str, Any]]:
        """Where the car is plugged in, as an HA zone name when we can tell.

        Only the resolved zone is stored, never raw coordinates: knowing a
        session happened "at home" is what costing needs, and a list of exact
        positions is far more sensitive than that.
        """

        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        zone = self._zone_name(latitude, longitude)
        return {"zone": zone} if zone else {"zone": None}

    def _coordinate(self, vin: str, kind: str) -> Optional[float]:
        for descriptor in (
            f"vehicle.cabin.infotainment.navigation.currentLocation.{kind}",
            f"vehicle.trip.segment.end.vehicleLocation.gpsPosition.{kind}",
        ):
            state = self.get_state(vin, descriptor)
            if state is None or state.value is None:
                continue
            try:
                return float(state.value)
            except (TypeError, ValueError):
                continue
        return None

    def zone_at(self, latitude: float, longitude: float) -> Optional[str]:
        """Public zone lookup, used when importing BMW's charging history so an
        imported session resolves to the same zone a live one would."""

        return self._zone_name(latitude, longitude)

    def _zone_name(self, latitude: float, longitude: float) -> Optional[str]:
        from homeassistant.components import zone as zone_component

        try:
            found = zone_component.async_active_zone(self.hass, latitude, longitude)
        except Exception:  # noqa: BLE001 - zone lookup is best-effort
            return None
        if found is None:
            return None
        return found.name or found.entity_id

    # --- trips (roadmap Phase 3) -------------------------------------------

    def _cap(self, msg: str, *args: Any, substrate: bool = False) -> None:
        """Emit a trip log line, routed by which debug mode is on.

        Trip-capture mode (``trip_debug``) is the loudest: it sends the line to
        the dedicated capture logger at INFO so it is visible on its own, whether
        or not the generic ``debug_log`` is on. Without capture mode, the decision
        lines (``substrate=False``) still appear under ``debug_log`` exactly as
        before; the heavy substrate lines (``substrate=True``) never do -- they
        are only worth their volume during a deliberate capture.
        """

        if self.trip_debug:
            _TRIPLOG.info(msg, *args)
        elif not substrate and debug_enabled():
            _LOGGER.debug(msg, *args)

    def _capture_batch(self, vin: str, data: Dict[str, Any], now: datetime) -> None:
        """Capture a raw MQTT batch: a compact ``[trip.raw]``/``[trip.seg]`` log
        line for eyeballing, plus the whole batch appended to the NDJSON file for
        offline replay. Only called in ``trip_debug`` mode. Never raises."""

        try:
            open_trip = vin in self._trip_builders
            scalars = []
            for key, payload in data.items():
                short = key[len("vehicle.") :] if key.startswith("vehicle.") else key
                value = payload.get("value") if isinstance(payload, dict) else payload
                if isinstance(value, (dict, list)):
                    scalars.append(f"{short}=<{type(value).__name__}>")
                else:
                    scalars.append(f"{short}={value}")
            self._cap(
                "[trip.raw] %s open=%s n=%d %s",
                vin,
                open_trip,
                len(data),
                " ".join(scalars),
                substrate=True,
            )

            # Segment/accumulated batch, captured whole with each field's own
            # timestamp so we can see whether any ever carries a real trip end.
            seg = {
                k: v for k, v in data.items() if k.startswith(DESC_SEG_CAPTURE_PREFIX)
            }
            if seg:
                freshest = None
                parts = []
                for key, payload in seg.items():
                    short = key[len(DESC_SEG_CAPTURE_PREFIX) :]
                    if isinstance(payload, dict):
                        ts = payload.get("timestamp")
                        parts.append(f"{short}={payload.get('value')}@{ts}")
                        parsed = dt_util.parse_datetime(ts) if ts else None
                        if parsed is not None and (freshest is None or parsed > freshest):
                            freshest = parsed
                    else:
                        parts.append(f"{short}={payload}")
                age = (now - freshest).total_seconds() if freshest is not None else None
                self._cap(
                    "[trip.seg] %s n=%d freshest_age=%ss fresh=%s %s",
                    vin,
                    len(seg),
                    None if age is None else int(age),
                    age is not None and age <= SEG_FRESH_S,
                    " ".join(parts),
                    substrate=True,
                )

            # Curated candidate lifecycle/motion signals present in this batch,
            # pulled out of the firehose so the fields we hope might drive a
            # better detector are trivially greppable. Each with its own
            # timestamp; absent (unstreamed) fields simply never appear.
            watch = []
            for key in TRIP_WATCH_DESCRIPTORS:
                payload = data.get(key)
                if not isinstance(payload, dict):
                    continue
                short = key[len("vehicle.") :] if key.startswith("vehicle.") else key
                watch.append(f"{short}={payload.get('value')}@{payload.get('timestamp')}")
            if watch:
                self._cap(
                    "[trip.watch] %s %s", vin, " ".join(watch), substrate=True
                )
        except Exception:  # noqa: BLE001 - capture must never break the stream
            _LOGGER.exception("Trip capture (log) failed for %s", vin)

    async def _capture_to_file(self, vin: str, data: Dict[str, Any], now: datetime) -> None:
        """Append one raw batch to the NDJSON capture file (off the event loop)."""

        record = {
            "at": now.isoformat(),
            "vin": vin,
            "open": vin in self._trip_builders,
            "data": data,
        }
        try:
            line = json.dumps(record, default=str)
        except (TypeError, ValueError):
            return
        path = self.hass.config.path(TRIP_CAPTURE_FILE)
        await self.hass.async_add_executor_job(self._append_capture_line, path, line)

    def _append_capture_line(self, path: str, line: str) -> None:
        try:
            if (
                os.path.exists(path)
                and os.path.getsize(path) > TRIP_CAPTURE_MAX_BYTES
            ):
                if not self._trip_capture_warned:
                    self._trip_capture_warned = True
                    _TRIPLOG.warning(
                        "Trip capture file %s exceeded %d bytes; pausing capture "
                        "writes. Turn trip-capture mode off, or move/delete the file.",
                        path,
                        TRIP_CAPTURE_MAX_BYTES,
                    )
                return
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as err:
            _TRIPLOG.warning("Trip capture file write failed: %s", err)

    async def _process_trip_signals(
        self,
        vin: str,
        now: datetime,
        motion: Optional[bool],
        ignition: Optional[bool],
        segment_fresh: bool,
    ) -> None:
        """React to this batch's motion / ignition / segment signals.

        A secondary path to the GPS detector (:meth:`_process_gps_signal`): cars
        that *do* stream motion/ignition still open and close on them, and a
        genuinely fresh segment batch closes with BMW's own statistics. Kept off
        the per-descriptor path (called once per message) because closing a trip
        may await a reverse geocode. All of it is guarded: trip bookkeeping must
        never break the stream.
        """

        if self.history is None:
            return
        try:
            open_trip = vin in self._trip_builders
            # Which of these BMW actually streams (and in what order) is the one
            # thing no unit test here can answer, so record every observation
            # under the opt-in debug flag. Tag it so a drive can be grepped out
            # of an otherwise very chatty debug log.
            if motion is not None or ignition is not None or segment_fresh:
                self._cap(
                    "[trip] %s signals moving=%s ignition=%s segment=%s open=%s",
                    vin,
                    motion,
                    ignition,
                    segment_fresh,
                    open_trip,
                )

            # A fresh segment batch is BMW's own completed-trip marker on cars
            # that stream one at the *end* of a drive -- but the i5 emits these
            # batches repeatedly mid-drive (identical shape: fresh timestamp, no
            # travelledDistance), so honouring every one chops a continuous drive
            # into sub-minute fragments that then drop out as noise. Only trust a
            # segment close when the GPS track isn't still showing movement; a
            # genuinely parked car (or one that streams no GPS at all) still
            # closes promptly here, otherwise the GPS debounce owns the close.
            if segment_fresh and open_trip and not self._gps_recently_moving(vin, now):
                await self._close_trip(vin, now, reason="segment")
                return

            started = motion is True or ignition is True
            if started and not open_trip:
                await self._open_trip(vin, now)
            if motion is True:
                # Moving again: cancel any pending stationary-close.
                self._cancel_trip_close_timer(vin)
            elif motion is False and open_trip:
                self._arm_trip_close_timer(vin)
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Trip detection failed for %s", vin)

    async def _process_door_signal(
        self, vin: str, now: datetime, door_open: bool
    ) -> None:
        """Refine trip start/end from the driver door, when the car streams it.

        Closing the door (driver got in) arms a start marker at that position, so
        the next movement seeds the trip there. Opening the door after the car has
        stopped means the driver is getting out -- an arrival -- so an open trip
        is closed promptly instead of waiting out the GPS debounce. A pure
        accelerator: cars that never send the door fall back to GPS alone.
        """

        if self.history is None:
            return
        try:
            previous = self._driver_door_open.get(vin)
            self._driver_door_open[vin] = door_open
            if previous is None or previous == door_open:
                return  # first reading or no transition
            open_trip = vin in self._trip_builders

            if door_open and open_trip:
                # Opened while a trip is open: an arrival, but only once the car
                # has actually stopped (guards a flickery read mid-move).
                last_move = self._last_gps_move.get(vin)
                stopped = (
                    last_move is None
                    or (now - last_move).total_seconds() >= DOOR_ARRIVAL_STOP_S
                )
                if stopped:
                    self._cap(
                        "[trip.door] %s driver door opened while stopped -> close",
                        vin,
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="door")
            elif not door_open and not open_trip:
                # Closed with no trip open: the driver just got in. Mark the start
                # here so the next movement seeds the trip from this spot.
                self._pending_start[vin] = (now, self._last_gps_position.get(vin))
                self._cap(
                    "[trip.door] %s driver door closed -> start marked at %s",
                    vin,
                    self._last_gps_position.get(vin),
                    substrate=True,
                )
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Door trip detection failed for %s", vin)

    def _gps_fix_ready(self, vin: str, now: datetime) -> bool:
        """True when a complete lat+lon fix is ready to process.

        BMW streams the two coordinates as separate messages, so a position is
        only settled once *both* timestamps have advanced past the last processed
        fix; until then acting would plot a phantom (fresh component + stale one).
        Falls through (returns True) when there are no timestamps to pair on, or
        when a component has been frozen past ``GPS_PAIR_STALE_S`` -- so a broken
        pairing assumption degrades to the old behaviour rather than stalling.
        """

        lat_state = self.get_state(vin, DESC_GPS_LAT)
        lon_state = self.get_state(vin, DESC_GPS_LON)
        lat_ts = (
            dt_util.parse_datetime(lat_state.timestamp)
            if lat_state and lat_state.timestamp
            else None
        )
        lon_ts = (
            dt_util.parse_datetime(lon_state.timestamp)
            if lon_state and lon_state.timestamp
            else None
        )
        if lat_ts is None or lon_ts is None:
            return True  # nothing to pair on -- process as before
        last_lat = self._gps_last_lat_ts.get(vin)
        last_lon = self._gps_last_lon_ts.get(vin)
        both_advanced = (last_lat is None or lat_ts > last_lat) and (
            last_lon is None or lon_ts > last_lon
        )
        if not both_advanced:
            prev = self._last_gps_fix_at.get(vin)
            if prev is not None and (now - prev).total_seconds() < GPS_PAIR_STALE_S:
                return False  # incomplete pair (or duplicate); wait for the mate
        self._gps_last_lat_ts[vin] = lat_ts
        self._gps_last_lon_ts[vin] = lon_ts
        return True

    async def _process_gps_signal(
        self, vin: str, now: datetime, fix_ts: Optional[datetime] = None
    ) -> None:
        """Open, extend and close trips from the live GPS position stream.

        The primary detector on a vehicle that streams neither motion/ignition
        nor a fresh segment batch: a fix that moves more than the jitter
        threshold opens a trip (if none is open) and adds its hop to the track
        distance; the close is left to the debounce timer, reset on every moving
        fix so a mid-drive GPS dropout still closes the trip, and armed once on a
        stationary fix so a parked car that keeps polling still closes promptly.

        ``fix_ts`` is the fix's own BMW timestamp (when known), used only by the
        trip-capture ``[trip.gps]`` line to expose stream latency and cadence.
        """

        if self.history is None:
            return
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return
        # BMW sends latitude and longitude as two separate messages ~1 s apart.
        # Acting on each pairs a fresh component with a stale one, so the first
        # message plots a phantom right-angle point (and doubles the distance)
        # that the second corrects. Wait until *both* components have advanced
        # since the last processed fix, so the tracker only ever sees a settled
        # position -- unless a component has frozen long enough to risk a stall.
        if not self._gps_fix_ready(vin, now):
            return
        try:
            tracker = self._gps_trackers.get(vin)
            if tracker is None:
                tracker = self._gps_trackers[vin] = GpsTracker()
            step_km = tracker.step(latitude, longitude)
            moving = is_gps_movement(step_km)
            open_trip = vin in self._trip_builders

            # Inter-fix gap and stream latency -- the two numbers that explain a
            # mid-drive split (a gap over the debounce) or a laggy close.
            prev_fix = self._last_gps_fix_at.get(vin)
            gap_s = (now - prev_fix).total_seconds() if prev_fix is not None else None
            self._last_gps_fix_at[vin] = now

            if moving:
                self._last_gps_move[vin] = now
                if not open_trip:
                    # Seed the track from where the car actually started (the
                    # parked position / door-close point), not this already
                    # moved-on fix -- done inside _open_trip before we overwrite
                    # _last_gps_position below.
                    await self._open_trip(vin, now)
                    open_trip = True
                builder = self._trip_builders.get(vin)
                if builder is not None:
                    builder.add_gps_km(step_km)
                    # Record the route point too, stamped with this fix's time
                    # (opt-in; a no-op otherwise).
                    builder.add_track_point(latitude, longitude, now)
                # Rolling window: keep the trip alive while the car is moving,
                # but guarantee a close even if the fixes stop arriving.
                self._reset_trip_close_timer(vin)
            elif open_trip:
                # Stationary fix: start the countdown but don't keep resetting it,
                # or a car that streams its parked position never closes.
                self._arm_trip_close_timer(vin)

            # Remember where the car is now, for the next trip's start seed.
            self._last_gps_position[vin] = (latitude, longitude)
            self._note_capture_fix(vin, now, step_km, moving, gap_s)
            if self.trip_debug:
                self._log_gps_fix(
                    vin, now, latitude, longitude, step_km, moving, gap_s, fix_ts,
                    open_trip,
                )
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("GPS trip detection failed for %s", vin)

    def _note_capture_fix(
        self,
        vin: str,
        now: datetime,
        step_km: float,
        moving: bool,
        gap_s: Optional[float],
    ) -> None:
        """Fold one fix into the open trip's capture stats (for [trip.post])."""

        stats = self._trip_capture.get(vin)
        if stats is None:
            return
        stats["fixes"] += 1
        if moving:
            stats["moves"] += 1
        else:
            stats["holds"] += 1
        if gap_s is not None and gap_s > stats["max_gap"]:
            stats["max_gap"] = gap_s

    def _log_gps_fix(
        self,
        vin: str,
        now: datetime,
        latitude: float,
        longitude: float,
        step_km: float,
        moving: bool,
        gap_s: Optional[float],
        fix_ts: Optional[datetime],
        open_trip: bool,
    ) -> None:
        """The per-fix substrate line: every fix, moving or not."""

        last_move = self._last_gps_move.get(vin)
        since_move = (now - last_move).total_seconds() if last_move is not None else None
        due = self._trip_close_due.get(vin)
        timer = "none" if due is None else f"armed({int((due - now).total_seconds())}s)"
        latency = (now - fix_ts).total_seconds() if fix_ts is not None else None
        builder = self._trip_builders.get(vin)
        # GPS-quality context: distinguishes "the car stopped" from "the fix was
        # lost" when a run of fixes shows no movement.
        fix_state = self._raw_value(vin, DESC_GPS_FIX)
        sats = self._raw_value(vin, DESC_GPS_SATS)
        heading = self._raw_value(vin, DESC_GPS_HEADING)
        self._cap(
            "[trip.gps] %s fix=(%.5f,%.5f) step=%dm gap=%ss lat=%ss moving=%s "
            "sinceMove=%ss odo=%s soc=%s gpsKm=%.2f open=%s timer=%s "
            "fixState=%s sats=%s hdg=%s",
            vin,
            latitude,
            longitude,
            round(step_km * 1000),
            None if gap_s is None else int(gap_s),
            None if latency is None else int(latency),
            moving,
            None if since_move is None else int(since_move),
            self._odometer_km(vin),
            self._current_soc(vin),
            builder.gps_km if builder is not None else 0.0,
            open_trip,
            timer,
            fix_state,
            sats,
            heading,
            substrate=True,
        )

    def _raw_value(self, vin: str, descriptor: str) -> Any:
        """Latest stored value of a descriptor, or None if never streamed."""

        state = self.get_state(vin, descriptor)
        return None if state is None else state.value

    def _gps_recently_moving(self, vin: str, now: datetime) -> bool:
        """True while the GPS track still shows the car under way.

        A car is "still moving" until its last movement fix is older than the
        stationary-close debounce -- the same window the GPS close timer uses --
        so a segment batch can't pre-empt a drive the timer would still keep
        alive. Cars that stream no GPS never set this and are never gated.
        """

        last = self._last_gps_move.get(vin)
        if last is None:
            return False
        return (now - last).total_seconds() < TRIP_CLOSE_DEBOUNCE_S

    def _trip_start_seed(self, vin: str, now: datetime) -> Optional[tuple[float, float]]:
        """Where the trip actually began, for the track's t=0 point and start place.

        Prefers a recent driver-door-close position (the driver got in there),
        then the last known parked position, so the route connects from where the
        car was rather than the already-moved-on fix that triggered detection.
        Falls back to the current fix when neither is available.
        """

        pending = self._pending_start.pop(vin, None)
        if pending is not None:
            marked_at, marked_pos = pending
            if (now - marked_at).total_seconds() <= PENDING_START_MAX_S and marked_pos:
                return marked_pos
        parked = self._last_gps_position.get(vin)
        if parked is not None:
            return parked
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is not None and longitude is not None:
            return (latitude, longitude)
        return None

    async def _open_trip(self, vin: str, now: datetime) -> None:
        seed = self._trip_start_seed(vin, now)
        # Resolve the start place from the seed (the parked/door-close spot), not
        # the ~hundreds-of-metres-later fix that first registered as movement.
        start_place = await self._resolve_place(
            vin, seed[0] if seed else None, seed[1] if seed else None
        )
        builder = self._trip_builders[vin] = TripBuilder(
            vin,
            now,
            start_place=start_place,
            soc_start=self._current_soc(vin),
            mileage_start=self._odometer_km(vin),
            location_assumed=start_place is None
            or start_place.get("label") == "Unknown",
            record_track=self.record_trip_track,
        )
        # Seed the route from where the drive actually started (see
        # _trip_start_seed), stamped t=0. A no-op when route recording is off or
        # no position is available.
        if seed is not None:
            builder.add_track_point(seed[0], seed[1], now)
        # Start the per-trip capture stats (a no-op cost when capture is off, but
        # cheap and it means _note_capture_fix needn't check the mode).
        self._trip_capture[vin] = {
            "fixes": 0,
            "moves": 0,
            "holds": 0,
            "max_gap": 0.0,
            "odo_start": builder.mileage_start,
        }
        self._cap(
            "[trip] %s OPEN at %s place=%s soc=%s odo=%s",
            vin,
            now.isoformat(),
            (start_place or {}).get("label"),
            builder.soc_start,
            builder.mileage_start,
        )

    async def _close_trip(
        self, vin: str, now: datetime, *, reason: str = "stationary"
    ) -> None:
        builder = self._trip_builders.pop(vin, None)
        cap_stats = self._trip_capture.pop(vin, None)
        self._cancel_trip_close_timer(vin)
        self._trip_close_due.pop(vin, None)
        if builder is None or self.history is None:
            return

        end_place = await self._resolve_place(vin)
        soc_end = self._current_soc(vin)
        stats, travelled_km = self._read_trip_segment(vin)
        energy_kwh = self._trip_energy_kwh(vin, builder.soc_start, soc_end)

        trip = builder.close(
            now,
            end_place=end_place,
            soc_end=soc_end,
            mileage_end=self._odometer_km(vin),
            energy_kwh=energy_kwh,
            travelled_km=travelled_km,
            stats=stats,
        )
        dropped = is_noise_trip(trip)
        self._cap(
            "[trip] %s CLOSE(%s) %s -> %s dist=%s (bmw=%s) soc=%s->%s "
            "energy=%s stats=%s",
            vin,
            reason,
            (trip.start_place or {}).get("label"),
            (trip.end_place or {}).get("label"),
            trip.distance_km,
            travelled_km,
            trip.soc_start,
            trip.soc_end,
            trip.energy_kwh,
            stats,
        )
        # One-line post-mortem: the distance signals side by side and the fix
        # cadence, so a bad trip can be diagnosed without scrolling the capture.
        if self.trip_debug:
            odo_start = (cap_stats or {}).get("odo_start")
            odo_end = self._odometer_km(vin)
            odo_delta = (
                round(odo_end - odo_start, 1)
                if odo_start is not None and odo_end is not None
                else None
            )
            self._cap(
                "[trip.post] %s reason=%s dropped=%s fixes=%s moves=%s holds=%s "
                "maxGap=%ss odoΔ=%s gpsKm=%.2f bmwKm=%s dist=%s dur=%ss",
                vin,
                reason,
                dropped,
                (cap_stats or {}).get("fixes"),
                (cap_stats or {}).get("moves"),
                (cap_stats or {}).get("holds"),
                int((cap_stats or {}).get("max_gap", 0.0)),
                odo_delta,
                builder.gps_km,
                travelled_km,
                trip.distance_km,
                trip.duration_s,
                substrate=True,
            )
        if dropped:
            # A parking manoeuvre or a spurious blip, not a drive worth logging.
            self._cap(
                "[trip] %s DROPPED as noise (dist=%s, duration=%ss)",
                vin,
                trip.distance_km,
                trip.duration_s,
            )
            return

        classification = classify_trip(
            trip.start_place,
            trip.end_place,
            home=self._home_zone_name(),
            work=self._work_zone_name(),
        )
        if classification is not None:
            trip.classification = classification
            trip.classification_source = SOURCE_AUTO
        self._cap(
            "[trip] %s RECORDED id=%s class=%s track=%d pts (home=%s work=%s)",
            vin,
            trip.id,
            classification,
            len(trip.track),
            self._home_zone_name(),
            self._work_zone_name(),
        )

        try:
            self.history.add_trip(trip)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not record trip for %s", vin)
            return
        async_dispatcher_send(self.hass, self.signal_trips, vin)

    def _arm_trip_close_timer(self, vin: str, *, quiet: bool = False) -> None:
        if vin in self._trip_close_timers:
            return

        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, where async_create_task is
        # not thread-safe.
        @callback
        def _fire(_now) -> None:
            self._trip_close_timers.pop(vin, None)
            self._trip_close_due.pop(vin, None)
            self._cap(
                "[trip.timer] %s FIRE -> closing (stationary %ds)",
                vin,
                TRIP_CLOSE_DEBOUNCE_S,
                substrate=True,
            )
            self.hass.async_create_task(
                self._close_trip(
                    vin, datetime.now(timezone.utc), reason="stationary"
                )
            )

        self._trip_close_due[vin] = datetime.now(timezone.utc) + timedelta(
            seconds=TRIP_CLOSE_DEBOUNCE_S
        )
        self._trip_close_timers[vin] = async_call_later(
            self.hass, TRIP_CLOSE_DEBOUNCE_S, _fire
        )
        # Only the first arm (car goes stationary) is worth a line; the per-fix
        # reset while moving would otherwise log every fix (the countdown is
        # already shown on each [trip.gps] line).
        if not quiet:
            self._cap(
                "[trip.timer] %s ARM due_in=%ds",
                vin,
                TRIP_CLOSE_DEBOUNCE_S,
                substrate=True,
            )

    def _reset_trip_close_timer(self, vin: str) -> None:
        """Restart the debounce from now -- used on every moving GPS fix.

        Unlike :meth:`_arm_trip_close_timer`, which leaves a running countdown
        alone, this pushes the close out so a moving car never times out; the
        timer then fires ``TRIP_CLOSE_DEBOUNCE_S`` after the *last* movement.
        """

        self._cancel_trip_close_timer(vin)
        self._arm_trip_close_timer(vin, quiet=True)

    def _cancel_trip_close_timer(self, vin: str) -> None:
        cancel = self._trip_close_timers.pop(vin, None)
        self._trip_close_due.pop(vin, None)
        if cancel is not None:
            cancel()

    async def async_flush_trips(self) -> None:
        """Close and persist any in-progress trips (called on unload/reload).

        A trip left open when Home Assistant restarts would otherwise be lost;
        closing it here captures whatever end state we have. Timers are cancelled
        first so a pending debounce can't fire against a stale builder.
        """

        for cancel in list(self._trip_close_timers.values()):
            cancel()
        self._trip_close_timers.clear()
        for vin in list(self._trip_builders):
            with suppress(Exception):
                await self._close_trip(
                    vin, datetime.now(timezone.utc), reason="unload"
                )

    def async_flush_charging(self) -> None:
        """Commit any charge whose debounced close is still pending (on unload).

        A session that stopped within the last ``CHARGE_CLOSE_DEBOUNCE_S`` still
        has a timer counting down; cancelling it without closing would drop the
        session, so finalize it here. An actively-charging session (no pending
        timer) is left as before -- BMW's import recovers it on the next fetch.
        """

        for vin in list(self._charge_close_timers):
            if self._cancel_charge_close_timer(vin):
                with suppress(Exception):
                    self._finalize_charge_close(vin, "unload")

    async def _resolve_place(
        self,
        vin: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """A trip endpoint as a named place -- never coordinates.

        Prefers the resolved HA zone (free, local, private). Only a point with no
        matching zone is offered to the optional geocoder, and only the resulting
        string is kept. ``None`` means no GPS was available at all. Explicit
        coordinates override the current fix (used to place a trip's *start* at
        the parked/door-close point rather than where movement was first seen).
        """

        if latitude is None or longitude is None:
            latitude = self._coordinate(vin, "latitude")
            longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        zone = self._zone_name(latitude, longitude)
        if zone:
            return place(zone=zone)
        address = None
        if self.geocoder is not None:
            address = await self.geocoder.resolve(latitude, longitude)
        return place(address=address) if address else place()

    def _current_soc(self, vin: str) -> Optional[float]:
        tracking = self._soc_tracking.get(vin)
        if tracking is None:
            return None
        if tracking.estimated_percent is not None:
            return round(tracking.estimated_percent, 1)
        if tracking.last_soc_percent is not None:
            return round(tracking.last_soc_percent, 1)
        return None

    def _trip_energy_kwh(
        self, vin: str, soc_start: Optional[float], soc_end: Optional[float]
    ) -> Optional[float]:
        """Energy used on a trip, from the SoC drop and the pack capacity.

        Battery-side by construction (SoC × capacity), which is what "energy the
        drive consumed" should mean. Returns ``None`` when either the SoC delta
        or the capacity is unknown rather than guessing.
        """

        if soc_start is None or soc_end is None:
            return None
        drop = soc_start - soc_end
        if drop <= 0:
            return None
        capacity = self.battery_capacity_kwh(vin)
        if not capacity:
            return None
        return round(drop / 100.0 * capacity, 3)

    def _read_trip_segment(
        self, vin: str
    ) -> tuple[dict[str, Any], Optional[float]]:
        """BMW's per-segment statistics and its own travelled distance.

        The statistics ride the trip record (served via ``get_trips``) rather
        than becoming entities; the distance is a fallback for when the odometer
        didn't tick over the trip window.
        """

        def _num(descriptor: str) -> Optional[float]:
            state = self.get_state(vin, descriptor)
            if state is None or state.value is None:
                return None
            try:
                return float(state.value)
            except (TypeError, ValueError):
                return None

        stats: dict[str, Any] = {}
        for key, descriptor in (
            ("recuperation_kwh", DESC_SEG_RECUP),
            ("accel_stars", DESC_SEG_ACCEL_STARS),
            ("brake_stars", DESC_SEG_BRAKE_STARS),
            ("eco_fraction", DESC_SEG_ECO),
            ("electric_fraction", DESC_SEG_ELECTRIC),
            ("bmw_consumption", DESC_SEG_CONSUMPTION),
        ):
            value = _num(descriptor)
            if value is not None:
                stats[key] = value
        return stats, _num(DESC_SEG_DISTANCE)

    def _home_zone_name(self) -> Optional[str]:
        state = self.hass.states.get("zone.home")
        return state.name if state is not None else "Home"

    def _work_zone_name(self) -> Optional[str]:
        if not self.work_zone_entity:
            return None
        state = self.hass.states.get(self.work_zone_entity)
        return state.name if state is not None else None

    def _current_price(self) -> Optional[float]:
        """The price per kWh in force right now, or ``None`` if unknowable.

        Read live rather than at the end of the session: with a dynamic tariff
        the price moves while the car charges, so each energy delta has to be
        billed at the price that applied when it was delivered.
        """

        config = self.pricing
        if not config.enabled:
            return None
        if config.mode == MODE_FIXED:
            return config.fixed_price
        state = self.hass.states.get(config.price_entity)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def get_state(self, vin: str, descriptor: str) -> Optional[DescriptorState]:
        return self.data.get(vin, {}).get(descriptor)

    def iter_descriptors(self, *, binary: bool) -> Iterable[tuple[str, str]]:
        for vin, descriptors in self.data.items():
            for descriptor, descriptor_state in descriptors.items():
                if isinstance(descriptor_state.value, bool) == binary:
                    yield vin, descriptor

    async def async_handle_connection_event(
        self, status: str, *, reason: Optional[str] = None
    ) -> None:
        now = datetime.now(timezone.utc)
        self.connection_status = status
        if reason:
            self.last_disconnect_reason = reason
        elif status == "connected":
            self.last_disconnect_reason = None
        self.connection_history.append(
            {"at": now.isoformat(), "status": status, "reason": reason}
        )
        # Track an unresolved "unauthorized" (MQTT rc=5) window: it opens on the
        # first unauthorized event and closes only on a successful connect, which
        # is exactly the condition the persisting-rc=5 repair should flag.
        if status == "unauthorized":
            if self._unauthorized_since is None:
                self._unauthorized_since = now
        elif status == "connected":
            self._unauthorized_since = None
        self._log_diagnostics()
        self._evaluate_stream_repairs(now)

    def _evaluate_stream_repairs(self, now: Optional[datetime] = None) -> None:
        """Raise or clear the stream-health repair issues.

        Idempotent and latched: the underlying registry calls are only made on a
        state transition, so this is cheap to run from both the watchdog tick and
        every connection event. Both issues carry a ``learn_more_url`` to the
        matching Wiki anchor -- the fix, not just the symptom.
        """

        now = now or datetime.now(timezone.utc)

        # Persisting rc=5 first: it is the more specific diagnosis, and while it
        # is active the no-data repair would only be a vaguer restatement of the
        # same outage, so we suppress that one.
        unauthorized = (
            self._unauthorized_since is not None
            and (now - self._unauthorized_since).total_seconds()
            >= UNAUTHORIZED_REPAIR_AFTER_S
        )
        self._set_unauthorized_issue(unauthorized)

        reference = self.last_message_at or self.stream_started_at
        no_data = (
            not unauthorized
            and reference is not None
            and (now - reference).total_seconds() >= NO_DATA_REPAIR_AFTER_S
        )
        self._set_no_data_issue(no_data)

    def _set_unauthorized_issue(self, active: bool) -> None:
        issue_id = f"stream_unauthorized_{self.entry_id}"
        if active == self._unauthorized_issue_active:
            return
        self._unauthorized_issue_active = active
        if not active:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="stream_unauthorized",
            translation_placeholders={
                "minutes": str(UNAUTHORIZED_REPAIR_AFTER_S // 60),
            },
            learn_more_url=f"{WIKI_TROUBLESHOOTING}#stream-authorization-failing",
        )

    def _set_no_data_issue(self, active: bool) -> None:
        issue_id = f"stream_no_data_{self.entry_id}"
        if active == self._no_data_issue_active:
            return
        self._no_data_issue_active = active
        if not active:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stream_no_data",
            translation_placeholders={
                "hours": str(NO_DATA_REPAIR_AFTER_S // 3600),
            },
            learn_more_url=f"{WIKI_TROUBLESHOOTING}#no-data-arriving",
        )

    def async_clear_stream_repairs(self) -> None:
        """Drop both stream-health repairs (called on unload)."""

        ir.async_delete_issue(self.hass, DOMAIN, f"stream_unauthorized_{self.entry_id}")
        ir.async_delete_issue(self.hass, DOMAIN, f"stream_no_data_{self.entry_id}")

    def descriptor_diagnostics(self, vin: str) -> list[dict[str, Any]]:
        """Per-descriptor arrival count + last timestamp for the diagnostics dump.

        Values are deliberately omitted -- only counts, units and timestamps --
        so nothing sensitive (GPS, VIN-bearing payloads) rides along; only the
        descriptor *names* appear, which are catalogue identifiers.
        """

        counts = self.descriptor_counts.get(vin, {})
        rows = [
            {
                "descriptor": descriptor,
                "arrivals": counts.get(descriptor, 0),
                "last_timestamp": state.timestamp,
                "unit": state.unit,
            }
            for descriptor, state in self.data.get(vin, {}).items()
        ]
        return sorted(rows, key=lambda row: row["descriptor"])

    async def async_start_watchdog(self) -> None:
        if self.watchdog_task:
            return
        self.stream_started_at = datetime.now(timezone.utc)
        # Tie the heartbeat to the config entry so it is cancelled on unload and
        # its exceptions surface, instead of being an orphaned bare loop task.
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            self.watchdog_task = entry.async_create_background_task(
                self.hass, self._watchdog_loop(), f"{DOMAIN}_watchdog"
            )
        else:  # pragma: no cover - entry always present during setup
            self.watchdog_task = self.hass.loop.create_task(self._watchdog_loop())

    async def async_stop_watchdog(self) -> None:
        if not self.watchdog_task:
            return
        self.watchdog_task.cancel()
        try:
            await self.watchdog_task
        except asyncio.CancelledError:
            pass
        self.watchdog_task = None

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.diagnostic_interval)
                self._log_diagnostics()
                self._evaluate_stream_repairs()
        except asyncio.CancelledError:
            return

    def _log_diagnostics(self) -> None:
        if debug_enabled():
            _LOGGER.debug(
                "Stream heartbeat: status=%s last_reason=%s last_message=%s",
                self.connection_status,
                self.last_disconnect_reason,
                self.last_message_at,
            )
        now = datetime.now(timezone.utc)
        updated_vins: list[str] = []
        for vin in list(self._soc_tracking.keys()):
            if self._apply_soc_estimate(vin, now, notify=False):
                updated_vins.append(vin)
        for vin in updated_vins:
            async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
        for vin in list(self._soc_tracking.keys()):
            if self._integrate_energy(vin, now):
                async_dispatcher_send(self.hass, self.signal_energy, vin)
        async_dispatcher_send(self.hass, self.signal_diagnostics)

    def _apply_soc_estimate(self, vin: str, now: datetime, notify: bool = True) -> bool:
        tracking = self._soc_tracking.get(vin)
        testing_tracking = self._testing_soc_tracking.get(vin)
        if not tracking:
            removed_estimate = self._soc_estimate.pop(vin, None) is not None
            removed_rate = self._soc_rate.pop(vin, None) is not None
            testing_removed = self._testing_soc_estimate.pop(vin, None) is not None
            if vin in self._testing_soc_tracking:
                self._testing_soc_tracking.pop(vin, None)
            self._avg_aux_power_w.pop(vin, None)
            self._charging_power_w.pop(vin, None)
            self._direct_power_w.pop(vin, None)
            self._ac_voltage_v.pop(vin, None)
            self._ac_current_a.pop(vin, None)
            self._ac_phase_count.pop(vin, None)
            changed = removed_estimate or removed_rate or testing_removed
            if notify and changed:
                async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
            return changed
        percent = tracking.estimate(now)
        rate = tracking.current_rate_per_hour()

        rate_changed = False
        if rate in (None, 0):
            if vin in self._soc_rate:
                self._soc_rate.pop(vin, None)
                rate_changed = True
        else:
            rounded_rate = round(rate, 3)
            if self._soc_rate.get(vin) != rounded_rate:
                self._soc_rate[vin] = rounded_rate
                rate_changed = True

        estimate_changed = False
        if percent is None:
            if vin in self._soc_estimate:
                self._soc_estimate.pop(vin, None)
                estimate_changed = True
        else:
            rounded_percent = round(percent, 2)
            if self._soc_estimate.get(vin) != rounded_percent:
                self._soc_estimate[vin] = rounded_percent
                estimate_changed = True
        updated = rate_changed or estimate_changed

        testing_changed = False
        if testing_tracking:
            testing_percent = testing_tracking.estimate(now)
            if testing_percent is None:
                if vin in self._testing_soc_estimate:
                    self._testing_soc_estimate.pop(vin, None)
                    testing_changed = True
            else:
                rounded_testing = round(testing_percent, 2)
                if self._testing_soc_estimate.get(vin) != rounded_testing:
                    self._testing_soc_estimate[vin] = rounded_testing
                    testing_changed = True
        else:
            if vin in self._testing_soc_estimate:
                self._testing_soc_estimate.pop(vin, None)
                testing_changed = True

        final_updated = updated or testing_changed
        if notify and final_updated:
            async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
        return final_updated

    def get_soc_rate(self, vin: str) -> Optional[float]:
        return self._soc_rate.get(vin)

    def get_soc_estimate(self, vin: str) -> Optional[float]:
        return self._soc_estimate.get(vin)

    def get_testing_soc_estimate(self, vin: str) -> Optional[float]:
        return self._testing_soc_estimate.get(vin)

    def restore_descriptor_state(
        self,
        vin: str,
        descriptor: str,
        value: Any,
        unit: Optional[str],
        timestamp: Optional[str],
    ) -> None:
        parsed_ts = dt_util.parse_datetime(timestamp) if timestamp else None
        unit = normalize_unit(unit)
        if value is None:
            if descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                tracking = self._soc_tracking.setdefault(vin, SocTracking())
                testing_tracking = self._get_testing_tracking(vin)
                tracking.update_target_soc(None, parsed_ts)
                testing_tracking.update_target_soc(None, parsed_ts)
            elif descriptor == "vehicle.vehicle.avgAuxPower":
                self._avg_aux_power_w.pop(vin, None)
                self._update_testing_power(vin, parsed_ts)
            elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
                self._set_direct_power(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                self._set_ac_voltage(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                self._set_ac_current(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                self._set_ac_phase(vin, None, parsed_ts)
            return
        vehicle_state = self.data.setdefault(vin, {})
        stored_value: Any = value
        if descriptor in {
            "vehicle.drivetrain.batteryManagement.header",
            "vehicle.drivetrain.batteryManagement.maxEnergy",
            "vehicle.powertrain.electric.battery.charging.power",
            "vehicle.drivetrain.electricEngine.charging.acVoltage",
            "vehicle.drivetrain.electricEngine.charging.acAmpere",
        }:
            try:
                stored_value = float(value)
            except (TypeError, ValueError):
                return
        vehicle_state[descriptor] = DescriptorState(
            value=stored_value,
            unit=unit,
            timestamp=timestamp,
        )
        tracking = self._soc_tracking.setdefault(vin, SocTracking())
        testing_tracking = self._get_testing_tracking(vin)

        updated = False
        if descriptor == "vehicle.drivetrain.batteryManagement.header":
            try:
                percent = float(value)
            except (TypeError, ValueError):
                return
            tracking.update_actual_soc(percent, parsed_ts)
            testing_tracking.update_actual_soc(percent, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.batteryManagement.maxEnergy":
            try:
                max_energy = float(value)
            except (TypeError, ValueError):
                return
            tracking.update_max_energy(max_energy)
            testing_tracking.update_max_energy(max_energy)
            updated = True
        elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
            try:
                power_w = float(value)
            except (TypeError, ValueError):
                self._set_direct_power(vin, None, parsed_ts)
            else:
                self._set_direct_power(vin, power_w, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.status":
            if isinstance(value, str):
                tracking.update_status(value)
                testing_tracking.update_status(value)
                updated = True
        elif descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
            try:
                target = float(value)
            except (TypeError, ValueError):
                tracking.update_target_soc(None, parsed_ts)
                testing_tracking.update_target_soc(None, parsed_ts)
                updated = True
            else:
                tracking.update_target_soc(target, parsed_ts)
                testing_tracking.update_target_soc(target, parsed_ts)
                updated = True
        elif descriptor == "vehicle.vehicle.avgAuxPower":
            aux_w: Optional[float] = None
            try:
                aux_value = float(value)
            except (TypeError, ValueError):
                pass
            else:
                if isinstance(unit, str) and unit.lower() == "w":
                    aux_w = aux_value
                else:
                    aux_w = aux_value * 1000.0
            if aux_w is not None:
                aux_w = max(aux_w, 0.0)
            if aux_w is None:
                self._avg_aux_power_w.pop(vin, None)
            else:
                self._avg_aux_power_w[vin] = aux_w
            self._update_testing_power(vin, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
            try:
                voltage_v = float(value)
            except (TypeError, ValueError):
                self._set_ac_voltage(vin, None, parsed_ts)
            else:
                self._set_ac_voltage(vin, voltage_v, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
            try:
                current_a = float(value)
            except (TypeError, ValueError):
                self._set_ac_current(vin, None, parsed_ts)
            else:
                self._set_ac_current(vin, current_a, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
            self._set_ac_phase(vin, value, parsed_ts)
            updated = True

        if not updated:
            return
        if tracking.estimated_percent is not None:
            self._soc_estimate[vin] = round(tracking.estimated_percent, 2)
        elif tracking.last_soc_percent is not None:
            self._soc_estimate[vin] = round(tracking.last_soc_percent, 2)
        if tracking.rate_per_hour not in (None, 0):
            self._soc_rate[vin] = round(tracking.rate_per_hour, 3)
        else:
            self._soc_rate.pop(vin, None)

        if testing_tracking.estimated_percent is not None:
            self._testing_soc_estimate[vin] = round(
                testing_tracking.estimated_percent, 2
            )
        elif vin in self._testing_soc_estimate:
            self._testing_soc_estimate.pop(vin, None)

    def restore_soc_cache(
        self,
        vin: str,
        *,
        estimate: Optional[float] = None,
        rate: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        tracking = self._soc_tracking.setdefault(vin, SocTracking())
        reference_time = timestamp or datetime.now(timezone.utc)
        if estimate is not None:
            tracking.estimated_percent = estimate
            tracking.last_estimate_time = reference_time
            self._soc_estimate[vin] = round(estimate, 2)
        if rate is not None:
            tracking.rate_per_hour = rate if rate not in (None, 0) else None
            if tracking.rate_per_hour:
                self._soc_rate[vin] = round(tracking.rate_per_hour, 3)
                tracking.charging_active = True
                if tracking.max_energy_kwh not in (None, 0):
                    tracking.last_power_w = (
                        tracking.rate_per_hour / 100.0
                    ) * tracking.max_energy_kwh * 1000.0
                tracking.last_power_time = reference_time
            else:
                self._soc_rate.pop(vin, None)

    def restore_testing_soc_cache(
        self,
        vin: str,
        *,
        estimate: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        tracking = self._get_testing_tracking(vin)
        reference_time = timestamp or datetime.now(timezone.utc)
        if estimate is None:
            return
        tracking.estimated_percent = estimate
        tracking.last_estimate_time = reference_time
        self._testing_soc_estimate[vin] = round(estimate, 2)

    @staticmethod
    def _build_device_metadata(vin: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        model_name = (
            payload.get("modelName")
            or payload.get("modelRange")
            or payload.get("series")
            or vin
        )
        brand = payload.get("brand") or "BMW"
        raw_payload = dict(payload)
        display_attrs: Dict[str, Any] = {
            "vin": raw_payload.get("vin") or vin,
            "model_name": model_name,
            "model_key": raw_payload.get("modelKey"),
            "series": raw_payload.get("series"),
            "series_development": raw_payload.get("seriesDevt"),
            "body_type": raw_payload.get("bodyType"),
            "color": raw_payload.get("colourDescription") or raw_payload.get("colourCodeRaw"),
            "country": raw_payload.get("countryCode"),
            "drive_train": raw_payload.get("driveTrain"),
            "propulsion_type": raw_payload.get("propulsionType"),
            "engine_code": raw_payload.get("engine"),
            "charging_modes": ", ".join(raw_payload.get("chargingModes") or []),
            "navigation_installed": raw_payload.get("hasNavi"),
            "sunroof": raw_payload.get("hasSunRoof"),
            "head_unit": raw_payload.get("headUnit"),
            "sim_status": raw_payload.get("simStatus"),
            "construction_date": raw_payload.get("constructionDate"),
            "special_equipment_codes": raw_payload.get("fullSAList"),
        }
        metadata: Dict[str, Any] = {
            "name": model_name,
            "manufacturer": brand,
            "serial_number": raw_payload.get("vin") or vin,
            "extra_attributes": display_attrs,
            "raw_data": raw_payload,
        }
        model = raw_payload.get("modelName") or raw_payload.get("series") or raw_payload.get("modelRange")
        if model:
            metadata["model"] = model
        if raw_payload.get("puStep"):
            metadata["sw_version"] = raw_payload["puStep"]
        if raw_payload.get("bodyType"):
            metadata["hw_version"] = raw_payload["bodyType"]
        return metadata

    def apply_basic_data(self, vin: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata = self._build_device_metadata(vin, payload)
        if not metadata:
            return None
        self.device_metadata[vin] = metadata
        new_name = metadata.get("name", vin)
        name_changed = self.names.get(vin) != new_name
        self.names[vin] = new_name
        if name_changed:
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.entry_id}_name",
                vin,
                new_name,
            )
        return metadata
