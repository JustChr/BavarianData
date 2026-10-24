"""State coordinator for BMW CarData streaming payloads."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Iterable, Optional

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
    EVENT_CHARGING_INTERRUPTED,
)
from .debug import debug_enabled, mask_vin
from .history.efficiency import TREND_MONTHS, efficiency_profile
from .history.health import usable_capacity
from .history.obfcm import lifetime as obfcm_lifetime
from .history.meter_rate import MeterRate, meter_rate
from .history.pricing import (
    MODE_FIXED,
    CostAccumulator,
    PricingConfig,
    billable_energy,
    resolve_cost,
)
from .evcc import PLUG_DESCRIPTORS, BridgeSnapshot, plug_state
from .history.energy_mix import (
    SOURCE_PV,
    MixAccumulator,
    supply_shares,
)
from .history.held_energy import HeldEnergy
from .history.sessions import (
    SessionBuilder,
    energy_ceiling_kwh,
    meter_energy_bound_kwh,
    house_supplies_this_session,
    meter_counts_this_session,
    open_session_is_resumable,
    soc_is_from_session,
)
from .history.stations import CHARGE_METHOD_DESCRIPTORS, streamed_charge_method
from .history.trips import Trip
from .car_profile import REST, STREAM, CarProfile
from .car_signals import (
    DESC_MOVING,
    DESC_IGNITION,
    DESC_GPS_LAT,
    DESC_GPS_LON,
    DESC_ODOMETER,
    DESC_FUEL_L,
    DESC_FUEL_LEVEL,
    LITRES_PER_GALLON,
    DESC_SEG_PREFIX,
    DESC_DRIVER_DOOR,
    LATE_READING_SETTLE_S,
    GPS_PART_LAT,
    GPS_PART_LON,
    CHARGE_CLOSE_DEBOUNCE_S,
    CHARGE_PLUGGED_HOLD_S,
    OPEN_SESSION_SAVE_INTERVAL_S,
    RESTORED_SESSION_GRACE_S,
    CHARGE_TERMINAL_STATUSES,
    CHARGE_PROGRESS_DESCRIPTORS,
    SEG_FRESH_S,
    DESC_BMW_RANGE,
    DESC_MAX_ENERGY,
    DESC_CHARGE_STATUS,
    CHARGE_END_REASON_DESCRIPTORS,
)
from .car_state import NO_CAR, Vehicle
from .coverage import is_combustion_only, is_plug_in_hybrid
from .soc_tracking import SocTracking
from .steering import steering_side
from .trip_capture import TRIP_CAPTURE_FILE, TripLog
from .vehicle_events import VehicleEvents
from .vehicle_reports import VehicleReports
from .vehicle_trips import VehicleTrips
from .stream_health import (
    NO_DATA_REPAIR_AFTER_S,
    UNAUTHORIZED_REPAIR_AFTER_S,
    WIKI_TROUBLESHOOTING,
    StreamHealth,
)
from .placeholders import is_placeholder
from .structured_values import EMPTY_WHEN_NULL
from .trip_settings import TripSettings
from .tyre import parse_tyre_diagnosis
from .mileage import mileage_report
from .units import miles_to_km, normalize_unit, unit_choices
from .vehicle_support import is_motorcycle, motorcycle_issue_id
from .vehicle_triggers import (
    charging_interrupted,
)

_LOGGER = logging.getLogger(__name__)


def _snapshot_float(value: Any) -> Optional[float]:
    """A number out of a stored snapshot, or ``None`` if it is not one.

    Snapshots are JSON written by an older build of this integration, so every
    field in one is untrusted input: a counter that comes back as ``null`` or a
    string must leave the session unseeded rather than raise on the setup path.
    """

    try:
        return None if value is None else float(value)
    except TypeError, ValueError:
        return None


def _snapshot_time(value: Any) -> Optional[datetime]:
    """A timestamp out of a stored snapshot, or ``None`` if it is unusable."""

    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


@dataclass
class DescriptorState:
    value: Any
    unit: Optional[str]
    timestamp: Optional[str]


def _charge_complete(soc: Optional[float], target: Optional[float]) -> bool:
    """Whether a charge that stopped reached its target (within the 1 % BMW rounds to)."""

    return target is not None and soc is not None and soc >= target - 1.0


@dataclass
class CardataCoordinator:
    hass: HomeAssistant
    entry_id: str
    data: Dict[str, Dict[str, DescriptorState]] = field(default_factory=dict)
    names: Dict[str, str] = field(default_factory=dict)
    device_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # vin -> parsed smart-maintenance tyre diagnosis (see tyre.py). REST-only:
    # BMW cannot stream this, so it is refreshed on the daily poll and persisted
    # by ``tyre_store`` -- a restart must not blank the tyre card until the next
    # fetch comes due.
    tyre_diagnosis: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Called with the VIN the first time a vehicle is seen on the stream, so
    # ``__init__.py`` can fetch the basic data a car bought after setup would
    # otherwise never get: the bootstrap runs once per entry, and everything
    # that names a car (model, software version, the device name itself) comes
    # from that one REST call. Set by the setup path; never fires for a VIN the
    # bootstrap already seeded.
    on_new_vehicle: Optional[Callable[[str], None]] = field(default=None, repr=False)
    # Called with the outage's length in seconds when the stream connects again
    # after having been connected before, so ``__init__.py`` can fetch what the
    # cars reported while nobody was listening (see ``startup_refresh.py``).
    on_reconnect_after_outage: Optional[Callable[[float], None]] = field(default=None, repr=False)
    last_message_at: Optional[datetime] = None
    last_telematic_api_at: Optional[datetime] = None
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
    descriptor_counts: Dict[str, Dict[str, int]] = field(default_factory=dict, init=False)
    # Per-VIN reporting traits (``car_profile.py``) for the diagnostics
    # download; live counters too.
    car_profiles: Dict[str, CarProfile] = field(default_factory=dict, init=False)
    # The stream's status, outages and the repairs they decide (``stream_health``).
    health: StreamHealth = field(default_factory=StreamHealth, init=False)
    # Latched repair state so the watchdog only calls the issue registry on a
    # transition rather than every tick.
    _no_data_issue_active: bool = field(default=False, init=False)
    _unauthorized_issue_active: bool = field(default=False, init=False)
    # Every car's own state (``car_state.py``), by VIN. Written through
    # :meth:`_car`, read through :meth:`_peek`.
    _cars: Dict[str, Vehicle] = field(default_factory=dict, init=False)
    # Charging-session recording. ``history`` is injected during setup; when it
    # is None (or pricing isn't configured) recording degrades quietly rather
    # than breaking the stream -- history is a bonus, not a prerequisite.
    history: Optional[Any] = None
    # Descriptor-coverage self-test ("Beyond the roadmap"). Records which selected
    # descriptors have actually streamed; optional, degrades quietly when absent.
    coverage: Optional[Any] = None
    # Persistence for the tyre diagnosis (see tyre_store.py); injected during
    # setup. Optional: without it the diagnosis is simply not remembered.
    tyre_store: Optional[Any] = None
    pricing: PricingConfig = field(default_factory=PricingConfig)
    # A car with its own wallbox meter or charging losses (see
    # ``vehicle_options.py``); every other car uses ``pricing``. Read through
    # ``pricing_for``. The tariff, currency and energy sources are the
    # house's, so they are the same in both.
    vehicle_pricing: Dict[str, PricingConfig] = field(default_factory=dict)
    # Trip recording (roadmap Phase 3). ``geocoder`` is injected/updated from
    # options; optional, degrades quietly.
    geocoder: Optional[Any] = None
    # How each car's trips are detected, recorded and classified (work zone,
    # default type, commute stop tolerance, detection mode, route recording).
    # ``trip_settings`` is every car without settings of its own; a car in
    # ``vehicle_trip_settings`` uses those instead. Both come from options; see
    # ``trip_settings.py``. Read through ``trip_settings_for``.
    trip_settings: TripSettings = field(default_factory=TripSettings)
    vehicle_trip_settings: Dict[str, TripSettings] = field(default_factory=dict)
    # Each car's mileage allowance as stored under Configure -> Mileage
    # allowance (``vehicle_options.mileage_values``), by VIN; a car without one
    # is absent. Read through ``mileage_report``.
    mileage_allowances: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # The account's Units setting (Configure -> Units; ``units.unit_choices``):
    # display only, everything recorded stays metric.
    display_units: Dict[str, str] = field(default_factory=lambda: unit_choices({}))
    # The trip detector's log and capture mode (``trip_capture``).
    trip_log: TripLog = field(default_factory=TripLog, init=False)
    # The device-trigger events: situations, edges, leaving a zone
    # (``vehicle_events``). Made in ``__post_init__``: it reads through us.
    events: VehicleEvents = field(init=False, repr=False)
    # The vehicle-report event, report trips and phone routes (``vehicle_reports``).
    reports: VehicleReports = field(init=False, repr=False)
    # Trip detection and recording (``vehicle_trips``).
    trips: VehicleTrips = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.events = VehicleEvents(self)
        self.reports = VehicleReports(self)
        self.trips = VehicleTrips(self)

    async def async_flush_trips(self) -> None:
        """Close and persist any in-progress trips (unload, reload, shutdown)."""

        await self.trips.async_flush_trips()

    def mark_trip_start(self, vin: str, at: datetime) -> None:
        """Note that a drive began at ``at`` (``bavariandata.mark_trip_start``)."""

        self.reports.mark_trip_start(vin, at)

    def watch_phone_routes(self) -> None:
        """Listen to every phone and in-car sensor a car's trip settings name."""

        self.reports.watch_phone_routes()

    def stop_watching_phone_routes(self) -> None:
        self.reports.stop_watching_phone_routes()

    @property
    def trip_debug(self) -> bool:
        """Trip-capture mode (opt-in): the ``[trip.*]`` capture and its NDJSON file.

        Account-wide, refreshed from the options.
        """

        return self.trip_log.enabled

    @trip_debug.setter
    def trip_debug(self, enabled: bool) -> None:
        self.trip_log.enabled = bool(enabled)

    @property
    def connection_status(self) -> str:
        return self.health.status

    @property
    def last_disconnect_reason(self) -> Optional[str]:
        return self.health.last_disconnect_reason

    @property
    def connection_history(self) -> Deque[Dict[str, Any]]:
        return self.health.history

    def _car(self, vin: str) -> Vehicle:
        """``vin``'s state, made on first use -- for anything that writes it."""

        car = self._cars.get(vin)
        if car is None:
            car = self._cars[vin] = Vehicle(vin)
        return car

    def _peek(self, vin: str) -> Vehicle:
        """``vin``'s state for reading: a car never heard of reads as unset.

        Never creates one, so a getter asked about any VIN leaves no trace;
        :data:`NO_CAR` refuses writes, so a read path cannot fill one in.
        """

        return self._cars.get(vin, NO_CAR)

    def _vins_where(self, test: Callable[[Vehicle], Any]) -> list[str]:
        """Every car ``test`` holds for, as a list (safe to change cars while walking it)."""

        return [vin for vin, car in self._cars.items() if test(car)]

    def _tracked_cars(self) -> list[tuple[str, SocTracking]]:
        """Every car with a SoC tracker, and the tracker."""

        return [
            (vin, car.charge.tracking)
            for vin, car in self._cars.items()
            if car.charge.tracking is not None
        ]

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

    @property
    def signal_mileage(self) -> str:
        """A car's mileage allowance was changed (Configure -> Mileage allowance)."""
        return f"{DOMAIN}_{self.entry_id}_mileage"

    @property
    def signal_trip_active(self) -> str:
        """Fired when a trip opens or closes, so the "drive under way" flag flips.

        Separate from :attr:`signal_trips`, which means "a finished trip landed in
        the store" and drives the statistics rebuild -- an opening trip has
        nothing to rebuild from.
        """

        return f"{DOMAIN}_{self.entry_id}_trip_active"

    @property
    def signal_tyre(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_tyre"

    def has_open_session(self, vin: str) -> bool:
        """True while a charging session is being recorded on ``vin``."""

        return self._peek(vin).session.builder is not None

    def charging_active(self, vin: str) -> Optional[bool]:
        """Whether the SoC tracker counts ``vin`` as charging (``None``: never seen)."""

        tracking = self._peek(vin).charge.tracking
        return None if tracking is None else tracking.charging_active

    def gps_halves_pending(self, vin: str) -> set[str]:
        """The halves of a GPS fix that arrived without their mate yet."""

        return set(self._peek(vin).gps.pending_parts or ())

    def has_open_trip(self, vin: str) -> bool:
        """True while a drive is under way on ``vin``.

        The cheap half of :meth:`open_trip_progress`, for the entity state that
        answers only "is it driving?" without building the whole live view.
        """

        return self._peek(vin).trip.builder is not None

    def open_trip_vins(self) -> list[str]:
        """Every vehicle with a drive under way right now."""

        return self._vins_where(lambda car: car.trip.builder is not None)

    def open_trip_progress(
        self, vin: str, *, include_track: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Live view of the drive under way on ``vin``, or ``None`` when parked.

        The only window into the open ``TripBuilder`` from outside the coordinator. A
        trip in progress exists purely in memory until it closes, so without this
        nothing -- neither an entity nor ``get_trips`` -- can show that the car is
        out driving right now.

        ``include_track`` adds the route recorded so far, and only exists when the
        user opted into route recording. Off by default because the polyline runs
        to ``MAX_TRACK_POINTS`` and must never land in an entity attribute, where
        every point would be written to the recorder on every update; a service
        response is the right place to ask for it.
        """

        builder = self._peek(vin).trip.builder
        if builder is None:
            return None
        soc_now = self.trips.trip_soc_end(vin, builder.start)
        progress = builder.progress(
            dt_util.utcnow(),
            mileage_now=self.odometer_km(vin),
            soc_now=soc_now,
        )
        energy_kwh = self.trips.trip_energy_kwh(vin, builder.soc_start, soc_now)
        progress["energy_kwh"] = None if energy_kwh is None else round(energy_kwh, 3)
        # Shaped like a finished record, so it carries the same consumption
        # figure under the same rule -- built through a real Trip rather than
        # divided here, or a drive still under way would print the one-percent
        # nonsense a finished one withholds.
        progress["consumption_kwh_per_100km"] = Trip(
            vin=vin,
            start=builder.start,
            distance_km=progress.get("distance_km"),
            soc_start=builder.soc_start,
            soc_end=soc_now,
            energy_kwh=energy_kwh,
            hybrid=self.is_plug_in_hybrid(vin),
        ).consumption_kwh_per_100km
        # Both of these qualify how much the "under way" flag can be trusted: the
        # last movement we actually saw, and whether the close is currently being
        # held through stream silence (see _hold_close_on_silence).
        last_move = self._peek(vin).gps.last_move
        progress["last_movement"] = last_move.isoformat() if last_move else None
        progress["held"] = self._peek(vin).trip.held_since is not None
        if include_track:
            progress["track"] = [list(point) for point in builder.track]
        return progress

    def apply_tyre_diagnosis(self, vin: str, payload: Any) -> Dict[str, Any]:
        """Store a parsed tyre diagnosis and wake the entities that show it.

        Returns the parsed result so the caller can log how much arrived. A
        response with no wheels is still stored: it is how "BMW has no tyre
        service data for this car" becomes visible rather than looking like a
        failed fetch.
        """

        parsed = parse_tyre_diagnosis(payload)
        if self.tyre_store is not None:
            # Persist before dispatching, so ``fetched_at`` is already on the
            # document the woken entities read.
            parsed["fetched_at"] = self.tyre_store.async_record(vin, parsed)
        self.tyre_diagnosis[vin] = parsed
        async_dispatcher_send(self.hass, self.signal_tyre, vin)
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry_id}_new_tyre", vin)
        return parsed

    def restore_tyre_diagnosis(self, stored: Dict[str, Dict[str, Any]]) -> None:
        """Seed the diagnosis from the store at setup.

        Called before the platforms are set up, so the tyre entities find their
        data already in place and come up with yesterday's reading rather than
        "unknown". A live fetch always wins: anything already present (it cannot
        be, this early, but the guard keeps the method safe to call again) stays.
        """

        for vin, diagnosis in (stored or {}).items():
            if vin and isinstance(diagnosis, dict) and vin not in self.tyre_diagnosis:
                self.tyre_diagnosis[vin] = diagnosis

    def _set_direct_power(
        self, vin: str, power_w: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._car(vin).charge.direct_power_w = None if power_w is None else max(power_w, 0.0)
        self._apply_effective_power(vin, timestamp)

    def _set_ac_voltage(
        self, vin: str, voltage_v: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._car(vin).charge.ac_voltage_v = None if voltage_v is None else max(voltage_v, 0.0)
        self._apply_effective_power(vin, timestamp)

    def _set_ac_current(
        self, vin: str, current_a: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._car(vin).charge.ac_current_a = None if current_a is None else max(current_a, 0.0)
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
            except TypeError, ValueError:
                parsed = None
            phase_count = parsed if parsed and parsed > 0 else None
        elif isinstance(phase_value, str):
            match = re.match(r"(\d+)", phase_value)
            if match:
                try:
                    parsed = int(match.group(1))
                except TypeError, ValueError:
                    parsed = None
                phase_count = parsed if parsed and parsed > 0 else None
        self._car(vin).charge.ac_phase_count = phase_count
        self._apply_effective_power(vin, timestamp)

    def _derive_ac_power(self, vin: str) -> Optional[float]:
        charge = self._peek(vin).charge
        voltage, current, phases = charge.ac_voltage_v, charge.ac_current_a, charge.ac_phase_count
        if voltage is None or current is None or phases is None:
            return None
        return max(voltage * current * phases, 0.0)

    def _compute_effective_power(self, vin: str) -> Optional[float]:
        direct = self._peek(vin).charge.direct_power_w
        if direct is not None:
            return direct
        return self._derive_ac_power(vin)

    def _apply_effective_power(self, vin: str, timestamp: Optional[datetime]) -> None:
        charge = self._car(vin).charge
        tracking = charge.tracker()
        effective_power = self._compute_effective_power(vin)
        charge.charging_power_w = effective_power
        if effective_power is not None:
            tracking.update_power(effective_power, timestamp)

    async def async_handle_message(self, payload: Dict[str, Any]) -> None:
        vin = payload.get("vin")
        data = payload.get("data") or {}
        if not vin or not isinstance(data, dict):
            return

        first_sight = vin not in self.data
        vehicle_state = self.data.setdefault(vin, {})
        if first_sight and self.on_new_vehicle is not None:
            # Scheduling only -- the handler must not make this message wait on
            # a REST call, and must never let one fail the stream.
            try:
                self.on_new_vehicle(vin)
            except Exception:  # noqa: BLE001 - a new car is not worth a dropped message
                _LOGGER.exception("Cardata: new-vehicle handler failed for %s", mask_vin(vin))
        new_binary: list[str] = []
        new_sensor: list[str] = []
        # Descriptors carrying a value in this batch, for the coverage self-test.
        seen_descriptors: list[str] = []

        self.last_message_at = datetime.now(timezone.utc)
        self.last_message_by_vin[vin] = self.last_message_at

        if debug_enabled():
            _LOGGER.debug("Processing message for VIN %s: %s", vin, list(data.keys()))

        tracking = self._car(vin).charge.tracker()
        now = datetime.now(timezone.utc)
        # REST callers tag their batches; the stream sends no ``source``.
        source = REST if payload.get("source") == REST else STREAM
        profile = self.car_profiles.get(vin)
        if profile is None:
            profile = self.car_profiles[vin] = CarProfile(now)
        # The charge the car set off with, for a report trip: read before this
        # batch lands, because a burst carries the SoC it *arrived* with, and the
        # previous report's reading predates any charge in between.
        parked_soc: Optional[tuple[float, Optional[datetime]]] = None
        if self.report_trips(vin) and any(descriptor in data for descriptor in DESC_ODOMETER):
            soc_now = self._current_soc(vin)
            if soc_now is not None:
                parked_soc = (soc_now, tracking.last_update)

        # Trip signals seen in this batch; acted on after the loop so opening or
        # closing a trip (which may await a geocode) never re-enters mid-iteration.
        motion_value: Optional[bool] = None
        ignition_value: Optional[bool] = None
        door_value: Optional[bool] = None
        segment_fresh = False
        gps_parts: set[str] = set()
        gps_ts: Optional[datetime] = None

        status_change: Optional[tuple[bool, str]] = None
        for descriptor, descriptor_payload in data.items():
            if not isinstance(descriptor_payload, dict):
                continue
            value = descriptor_payload.get("value")
            # The integration speaks kilometres: a car that sends miles for one
            # descriptor (issue #62) must not have the number shown as km.
            value, unit = miles_to_km(value, normalize_unit(descriptor_payload.get("unit")))
            timestamp = descriptor_payload.get("timestamp")
            parsed_ts = dt_util.parse_datetime(timestamp) if timestamp else None
            if value is None and descriptor in EMPTY_WHEN_NULL:
                # BMW's word for "no Check Control messages" (structured_values).
                value = []
            if is_placeholder(descriptor, value):
                # BMW's stand-in for no reading (placeholders): clear what an
                # entity already shows, but never create one or count it as seen.
                profile.observe_placeholder(descriptor)
                if descriptor in vehicle_state:
                    vehicle_state[descriptor] = DescriptorState(
                        value=None, unit=unit, timestamp=timestamp
                    )
                    async_dispatcher_send(self.hass, self.signal_update, vin, descriptor)
                continue
            if value is None:
                if descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                    tracking.update_target_soc(None, parsed_ts)
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
            profile.observe(
                descriptor, value, timestamp, parsed_ts, vehicle_state.get(descriptor), now, source
            )
            vehicle_state[descriptor] = DescriptorState(value=value, unit=unit, timestamp=timestamp)
            seen_descriptors.append(descriptor)
            vin_counts = self.descriptor_counts.setdefault(vin, {})
            vin_counts[descriptor] = vin_counts.get(descriptor, 0) + 1
            if descriptor == "vehicle.vehicleIdentification.basicVehicleData" and isinstance(
                value, dict
            ):
                self.apply_basic_data(vin, value)
            if is_new:
                if isinstance(value, bool):
                    new_binary.append(descriptor)
                else:
                    new_sensor.append(descriptor)
            if descriptor == "vehicle.drivetrain.batteryManagement.header":
                try:
                    percent = float(value)
                except TypeError, ValueError:
                    pass
                else:
                    tracking.update_actual_soc(percent, parsed_ts)
            elif descriptor == "vehicle.drivetrain.batteryManagement.maxEnergy":
                try:
                    max_energy = float(value)
                except TypeError, ValueError:
                    pass
                else:
                    tracking.update_max_energy(max_energy)
            elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
                try:
                    power_w = float(value)
                except TypeError, ValueError:
                    self._set_direct_power(vin, None, parsed_ts)
                else:
                    self._set_direct_power(vin, power_w, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.status":
                if isinstance(value, str):
                    was_charging = tracking.charging_active
                    tracking.update_status(value)
                    # Acted on once the whole batch is read: the target, the SoC
                    # and the power may come after the status in the same batch,
                    # and the session and its event must see them.
                    status_change = (was_charging, value)
            elif descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                try:
                    target = float(value)
                except TypeError, ValueError:
                    tracking.update_target_soc(None, parsed_ts)
                else:
                    tracking.update_target_soc(target, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                try:
                    voltage_v = float(value)
                except TypeError, ValueError:
                    self._set_ac_voltage(vin, None, parsed_ts)
                else:
                    self._set_ac_voltage(vin, voltage_v, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                try:
                    current_a = float(value)
                except TypeError, ValueError:
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
                gps_parts.add(GPS_PART_LAT if descriptor == DESC_GPS_LAT else GPS_PART_LON)
                if parsed_ts is not None:
                    gps_ts = parsed_ts
            if descriptor.startswith(DESC_SEG_PREFIX):
                # Only a segment carrying a *recent* timestamp is a completed
                # trip; the stale "last trip end" fields BMW repeats in every
                # snapshot must not be mistaken for one (see ``SEG_FRESH_S``).
                if parsed_ts is not None and (now - parsed_ts).total_seconds() <= SEG_FRESH_S:
                    segment_fresh = True

            async_dispatcher_send(self.hass, self.signal_update, vin, descriptor)

        profile.finish_batch(now, source)
        if status_change is not None:
            self._fire_charging_event(vin, status_change[0], tracking, status_change[1])
        if any(descriptor in data for descriptor in PLUG_DESCRIPTORS):
            self._close_held_charge_on_unplug(vin)

        if any(descriptor in data for descriptor in DESC_ODOMETER):
            self.reports.note_odometer(vin, parked_soc=parked_soc)
            self._note_odometer_day(vin, now)
        if DESC_FUEL_L in data or DESC_FUEL_LEVEL in data:
            self._note_fuel(vin, now)

        for descriptor in new_sensor:
            async_dispatcher_send(self.hass, self.signal_new_sensor, vin, descriptor)
        for descriptor in new_binary:
            async_dispatcher_send(self.hass, self.signal_new_binary, vin, descriptor)

        if seen_descriptors:
            self._car(vin).live_descriptors.update(seen_descriptors)

        if self.coverage is not None and seen_descriptors:
            # Bookkeeping must never break the stream.
            try:
                self.coverage.note_seen(vin, seen_descriptors)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Coverage tracking failed for %s", mask_vin(vin))

        self._apply_soc_estimate(vin, now)
        if self._integrate_energy(vin, now):
            async_dispatcher_send(self.hass, self.signal_energy, vin)

        # Serialise per-VIN so concurrent messages can't double-open a trip while
        # one is awaiting a geocode (see ``TripState.lock``). The timer-driven close
        # stays outside the lock -- it pops its builder before any await, so it
        # can't corrupt an open running here, and taking the lock there would
        # deadlock the in-lock segment/stationary closes.
        lock = self._peek(vin).trip.lock
        if lock is None:
            lock = self._car(vin).trip.lock = asyncio.Lock()
        async with lock:
            # A car that reports only once parked has its trips built from the
            # reports instead: its burst's position jump, read live, would be a
            # drive of no duration, ending wherever the timers left it.
            if not self.report_trips(vin):
                await self.trips.process_trip_signals(
                    vin, now, motion_value, ignition_value, segment_fresh
                )
                if door_value is not None:
                    await self.trips.process_door_signal(vin, now, door_value)
                if "vehicle.drivetrain.batteryManagement.header" in data or any(
                    descriptor in data for descriptor in DESC_ODOMETER
                ):
                    self.trips.note_late_reading(vin, now)
                if gps_parts:
                    await self.trips.process_gps_signal(vin, now, gps_ts, gps_parts)
            # After the trip logic: a trip opening ends "parked unlocked".
            self.events.evaluate_situations(vin, now)
        self.events.note_edges(vin, data, seen_descriptors, now, source)

        # Trip-capture diagnostics: the raw firehose + NDJSON file, outside the
        # trip lock (they only read state) and strictly gated on the opt-in.
        if self.trip_debug:
            open_trip = self._peek(vin).trip.builder is not None
            self.trip_log.log_batch(vin, data, now, open_trip=open_trip, fresh_s=SEG_FRESH_S)
            line = TripLog.record_line(vin, data, now, open_trip=open_trip)
            if line is not None:
                with suppress(Exception):  # file IO must never break the stream
                    await self.hass.async_add_executor_job(
                        self.trip_log.append_line,
                        self.hass.config.path(TRIP_CAPTURE_FILE),
                        line,
                    )

        async_dispatcher_send(self.hass, self.signal_diagnostics)

    def _integrate_energy(self, vin: str, now: datetime) -> bool:
        """Accumulate delivered energy from effective charging power.

        Assumes the last-sampled power held constant since the previous tick (a
        left Riemann sum). ``energy_last_time`` is advanced every call —
        including while idle — so a resumed session never integrates over a
        stale gap.

        BMW does *not* sample charging power evenly: it arrives in bursts with
        gaps of an hour or more between them, while this runs on every batch
        *and* on the watchdog tick. Held across such a gap, one unrepresentative
        sample dominates the total — a real session integrated a 3.54 kW reading
        for 162 minutes and recorded 11.10 kWh where the battery took 5.46. So
        the running total is capped by what the pack can actually have absorbed
        (see :meth:`_session_energy_ceiling_wh`); the integration still supplies
        the timing and the curve, it just can't run away from the battery.
        """
        charge = self._car(vin).charge
        last, charge.energy_last_time = charge.energy_last_time, now
        tracking = charge.tracking
        power_w = charge.charging_power_w
        if last is None or tracking is None or not tracking.charging_active:
            return False
        # A session resumed across a restart is owed whatever the pack gained
        # while we were away; it can only be measured once a SoC reading from
        # this session lands, so it is attempted here rather than at the resume.
        self._apply_gap_credit(vin)
        if not power_w or power_w <= 0:
            return False
        delta_seconds = (now - last).total_seconds()
        if delta_seconds <= 0:
            return False
        wh = power_w * (delta_seconds / 3600.0)

        # Accumulate raw, expose bounded. Capping each *increment* instead would
        # lose energy for good every time SoC lagged the charge -- and SoC always
        # lags, arriving a whole percent at a time. Bounding the running total
        # lets it catch up the moment the next reading lands, so an honest
        # session ends on its full figure while a runaway one stays pinned to
        # what the battery can hold.
        raw_after = (charge.energy_session_raw_wh or 0.0) + wh
        before = charge.energy_session_wh or 0.0
        charge.energy_session_raw_wh = raw_after
        # Never below what was already released: a session that loses its meter
        # (another car plugs in, the meter resets) falls back to a SoC ceiling
        # that may sit under the meter's count.
        after = max(before, self._bounded_session_wh(vin, raw_after))

        charge.energy_session_wh = after
        # The whole increment is booked, at this instant's mix and price; the
        # ceiling only decides how much of it is released into the session.
        self._record_energy_delta(vin, now, power_w, wh / 1000.0)
        gained = after - before
        if gained <= 0:
            return False
        charge.energy_lifetime_wh = (charge.energy_lifetime_wh or 0.0) + gained
        self._release_held_energy(vin, gained / 1000.0)
        return True

    def _settle_session_energy(self, vin: str) -> None:
        """Release what the session's latest SoC makes room for, one last time.

        The integration only releases energy while the car is charging, but a
        car that sleeps through its charge sends its new SoC in the same batch
        as the status that ends it -- the ceiling rises after the last step that
        could have used it. A MINI Cooper SE filed every charge at the 2 %
        margin that way, 0.6 kWh for a 15 kWh night (#180). So the close looks
        once more. Only against a ceiling: one that cannot be computed (no SoC
        from this session) never releases a raw total it never checked.
        """

        charge = self._peek(vin).charge
        raw = charge.energy_session_raw_wh
        if raw is None or self._session_energy_ceiling_wh(vin) is None:
            return
        before = charge.energy_session_wh or 0.0
        after = self._bounded_session_wh(vin, raw)
        gained = after - before
        if self._session_meter_bound_kwh(vin) is not None:
            # Held to the meter, what is still held is the car's power reading
            # above the wallbox's (the i5's runs ~4 % high) or power held across
            # a gap the meter billed as nothing. Its mix and cost were billed
            # from the meter, so all of it is this charge's.
            held = self._peek(vin).session.held
            if held is not None:
                held.release(
                    held.battery_kwh, self._peek(vin).session.mix, self._peek(vin).session.cost
                )
        if gained <= 0:
            return
        charge.energy_session_wh = after
        charge.energy_lifetime_wh = (charge.energy_lifetime_wh or 0.0) + gained
        self._release_held_energy(vin, gained / 1000.0)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s SETTLE +%s kWh at close (soc %s -> %s)",
                vin,
                round(gained / 1000.0, 3),
                charge.energy_session_soc,
                self._session_soc_percent(vin),
            )
        async_dispatcher_send(self.hass, self.signal_energy, vin)

    def _release_held_energy(self, vin: str, kwh: float) -> None:
        held = self._peek(vin).session.held
        if held is not None:
            held.release(kwh, self._peek(vin).session.mix, self._peek(vin).session.cost)

    def _bounded_session_wh(self, vin: str, raw_wh: float) -> float:
        """The raw running total, held to what the pack can have absorbed."""

        ceiling = self._session_energy_ceiling_wh(vin)
        return raw_wh if ceiling is None else min(raw_wh, ceiling)

    def _session_soc_percent(self, vin: str) -> Optional[float]:
        """The reported SoC, but only if it was read during this session.

        A car that doesn't stream ``batteryManagement.header`` holds the same
        bootstrap reading forever, which would otherwise be recorded as both ends
        of every charge and bound every one of them to the ceiling's bare margin.
        See :func:`soc_is_from_session` for what that costs.
        """

        tracking = self._peek(vin).charge.tracking
        if tracking is None or tracking.last_soc_percent is None:
            return None
        if not soc_is_from_session(
            tracking.last_update, self._peek(vin).charge.energy_session_start
        ):
            return None
        return tracking.last_soc_percent

    def _session_energy_ceiling_wh(self, vin: str) -> Optional[float]:
        """The live inputs for :func:`energy_ceiling_kwh`, as Wh -- or the meter's.

        The ceiling rises as SoC does, so a charge that keeps going keeps room
        to accrue, and a stream that falls silent -- taking SoC with it -- grants
        none. Reads the *reported* SoC, never the extrapolated one: the estimate
        is advanced from the very charging power this bounds, so using it would
        let the figure authorise its own growth. And never a reading from before
        the session (see :meth:`_session_soc_percent`), which would bound the
        charge by a number that predates it.
        """

        soc_start = self._peek(vin).charge.energy_session_soc
        soc_now = self._session_soc_percent(vin)
        capacity = self.battery_capacity_kwh(vin)
        metered_kwh = self._session_meter_bound_kwh(vin)
        if metered_kwh is not None:
            return metered_kwh * 1000.0
        ceiling_kwh = energy_ceiling_kwh(soc_start, soc_now, capacity)
        return None if ceiling_kwh is None else ceiling_kwh * 1000.0

    def _session_meter_bound_kwh(self, vin: str) -> Optional[float]:
        """The bound wallbox meter's count, where it bounds this session.

        See :func:`meter_energy_bound_kwh` for when it does.
        """

        builder = self._peek(vin).session.builder
        return meter_energy_bound_kwh(
            None if builder is None else builder.metered_so_far_kwh(),
            self._peek(vin).charge.energy_session_soc,
            self._session_soc_percent(vin),
            self.battery_capacity_kwh(vin),
        )

    def _sample_power_curve(self, vin: str, now: datetime, power_w: float) -> None:
        """Feed the session record's curve and its running SoC.

        Split out from the energy step because the two answer different
        questions: the curve records what the car *reported*, which stays true
        even in a tick where the ceiling let no energy through.
        """

        builder = self._peek(vin).session.builder
        if builder is None:
            return
        builder.sample(now, power_w / 1000.0)
        builder.note_soc(self._session_soc_percent(vin))
        if builder.charge_method is None:
            builder.charge_method = streamed_charge_method(
                self._live_values(vin, CHARGE_METHOD_DESCRIPTORS)
            )
        # Throttled inside; this is simply the one place that runs for every
        # sample of every open session, which is exactly the cadence a snapshot
        # of an open session wants.
        self._snapshot_open_session(vin)

    def _record_energy_delta(self, vin: str, now: datetime, power_w: float, kwh: float) -> None:
        """Feed the session record from the same delta that moved the counters.

        Sharing the integration step is deliberate: the curve, the energy and
        the cost then describe exactly the same samples and cannot disagree.

        The bound wallbox meter rides the same step, and for the same reason.
        Its advance over this interval is a *measured* grid delta for exactly
        the window the battery-side increment covers, so it can be billed
        directly -- which is what makes the setting's promise ("that exact grid
        figure is used instead") true of the cost and not only of the total.
        Billing the session's grid figure at close instead would price the whole
        charge at whatever the tariff happened to be when the plug came out.

        Booked into the held pool, not the session: the SoC ceiling decides
        afterwards how much of it the session gets (:meth:`_release_held_energy`).
        """

        self._sample_power_curve(vin, now, power_w)
        self._book_increment(vin, kwh)

    def _book_increment(self, vin: str, kwh: float) -> None:
        """Bill one battery-side increment, at the meter where there is one.

        Also called with nothing integrated at the close, so the meter's last
        advance -- after the final integration step, up to a whole update
        interval on a meter that reports once a minute -- is billed too.
        """

        # One read of the wallbox meter, used twice: to bill this increment and
        # to move the session's running total. ``_grid_meter_step`` advances the
        # billing cursor, so it is called on its own line rather than inline --
        # a side effect buried in an argument list is how one gets called twice.
        meter_now = self._session_grid_meter_kwh(vin, self._peek(vin).session.builder)
        metered_kwh = self._grid_meter_step(vin, meter_now)
        billable, _source = billable_energy(
            battery_kwh=kwh,
            grid_kwh=metered_kwh,
            loss_percent=self.pricing_for(vin).loss_percent,
        )
        builder = self._peek(vin).session.builder
        held = self._peek(vin).session.held
        # Away from home the house's meters and tariff describe the house, not
        # this charge: no mix, no cost (see ``house_supplies_this_session``).
        if builder is None or house_supplies_this_session(builder.location, self._home_zone_name()):
            if held is not None:
                # One sample of the house's meters serves both the mix and the
                # cost, so the two can never describe different instants.
                split = held.hold(kwh, billable, self._supply_shares())
                self._bill_split(held.cost, billable, split)
        elif held is not None:
            held.hold(kwh, None, None)
        if builder is not None:
            builder.note_grid_meter(meter_now)

    def _grid_meter_step(self, vin: str, meter_now: Optional[float]) -> Optional[float]:
        """How far the wallbox meter has advanced since the last billed step.

        ``None`` when there is nothing measured to bill -- no meter bound, the
        first step of a session (the baseline has to be established before a
        delta exists), or a meter that went backwards, which is a reset and not
        a negative charge. In each case billing falls back to the battery-side
        figure, exactly as it did before a meter was ever bound.

        A meter that has not moved is a measured zero, not a missing reading. A
        wallbox that lost power while the car's last reported power was held
        across the gap delivered nothing, and a meter that updates once a
        minute bills the whole minute on its next step -- billing the
        battery-side figure in between counted those kilowatt-hours twice.
        """

        if meter_now is None:
            return None
        previous = self._peek(vin).session.grid_meter_billed
        self._car(vin).session.grid_meter_billed = meter_now
        if previous is None:
            return None
        delta = meter_now - previous
        if delta < 0:
            return None
        return delta

    def get_lifetime_energy_kwh(self, vin: str) -> Optional[float]:
        value = self._peek(vin).charge.energy_lifetime_wh
        return None if value is None else round(value / 1000.0, 3)

    def get_session_energy_kwh(self, vin: str) -> Optional[float]:
        value = self._peek(vin).charge.energy_session_wh
        return None if value is None else round(value / 1000.0, 3)

    def get_session_start(self, vin: str) -> Optional[datetime]:
        return self._peek(vin).charge.energy_session_start

    def restore_lifetime_energy(self, vin: str, kwh: float) -> None:
        """Seed the lifetime accumulator from a restored sensor state."""
        if kwh is None:
            return
        charge = self._car(vin).charge
        if charge.energy_lifetime_wh is None:
            charge.energy_lifetime_wh = kwh * 1000.0

    def restore_session_energy(
        self, vin: str, kwh: float, start: Optional[datetime] = None
    ) -> None:
        charge = self._car(vin).charge
        if kwh is not None:
            if charge.energy_session_wh is None:
                charge.energy_session_wh = kwh * 1000.0
            # Seed the raw total to match: a restart mid-charge that left the raw
            # figure at zero would read as "the ceiling is holding everything
            # back" and stall the session's energy until the next plug-in. The
            # restored value is already bounded, so starting them equal is right.
            if charge.energy_session_raw_wh is None:
                charge.energy_session_raw_wh = kwh * 1000.0
        if start is not None and charge.energy_session_start is None:
            charge.energy_session_start = start

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
        if self._peek(vin).session.restored_at is not None:
            # First word from the stream since a restart left a charge open.
            # Either it is still running -- the same record continues -- or it
            # ended while we were away, in which case it is closed at its last
            # sample rather than now. Deliberately not routed through the
            # flap debounce: that window exists to rejoin a session we were
            # watching, and here the gap has already happened.
            if now_charging:
                self._resume_restored_session(vin)
            elif str(status).strip().upper() in CHARGE_TERMINAL_STATUSES:
                self._close_restored_session(vin, status)
                self._clear_charge_progress(vin)
            # Anything else -- initialization, a pause, a status we don't know --
            # says nothing about whether this charge is over, so the session is
            # left restored and the grace timer keeps the deadline.
            return
        if was_charging == now_charging:
            if (
                not now_charging
                and str(status).strip().upper() in CHARGE_TERMINAL_STATUSES
                and self._peek(vin).session.close_timer is None
                and self._peek(vin).session.builder is None
            ):
                # The car says no charge is running and none is open here: any
                # progress figure still standing is one whose close we never saw
                # -- the charge ended while Home Assistant was down, or before
                # an update that clears them -- and came back with the restore.
                self._clear_charge_progress(vin)
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
                self._car(vin).session.stopped = None
                if debug_enabled():
                    _LOGGER.debug(
                        "[charge] %s RESUME after status flap; keeping session",
                        vin,
                    )
                return
            # New session: zero the session accumulator and stamp its start so
            # the session-energy sensor reports a fresh last_reset.
            started_at = datetime.now(timezone.utc)
            charge = self._car(vin).charge
            charge.energy_session_wh = 0.0
            charge.energy_session_raw_wh = 0.0
            charge.energy_session_start = started_at
            # The SoC this session started from, kept here rather than read off
            # the session record so the energy ceiling still applies when history
            # is switched off (the record is optional; the counters are not).
            charge.energy_session_soc = tracking.last_soc_percent
            self._open_session_record(vin, tracking, started_at)
            self._fire(EVENT_CHARGING_STARTED, payload)
            return
        # Charging stopped. Debounce the close so a momentary NOCHARGING blip
        # that comes straight back doesn't split one plug-in into two records.
        self._arm_charge_close_timer(vin, status)

    def _arm_charge_close_timer(self, vin: str, status: str) -> None:
        """Commit the session close only if charging stays stopped for the grace.

        Re-arming on each stop transition restarts the countdown, so the close
        fires ``CHARGE_CLOSE_DEBOUNCE_S`` after the *last* stop; a resume in
        between cancels it (see :meth:`_fire_charging_event`). If the cable is
        still in by then, the close waits on until ``CHARGE_PLUGGED_HOLD_S``
        after the stop.
        """

        self._cancel_charge_close_timer(vin)
        self._car(vin).session.stopped = (datetime.now(timezone.utc), status)
        self._schedule_charge_close(vin, CHARGE_CLOSE_DEBOUNCE_S)

    def _schedule_charge_close(self, vin: str, delay_s: float) -> None:
        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, and _finalize_charge_close
        # touches loop-only helpers (async_dispatcher_send).
        @callback
        def _fire(_now) -> None:
            self._car(vin).session.close_timer = None
            stopped_at, _status = self._peek(vin).session.stopped
            if self._plug_state_live(vin) is True:
                waited = (datetime.now(timezone.utc) - stopped_at).total_seconds()
                if waited < CHARGE_PLUGGED_HOLD_S:
                    self._schedule_charge_close(vin, CHARGE_PLUGGED_HOLD_S - waited)
                    return
            self._finalize_charge_close(vin)

        self._car(vin).session.close_timer = async_call_later(self.hass, delay_s, _fire)

    def _close_held_charge_on_unplug(self, vin: str) -> None:
        """Close a charge held for the plug as soon as the cable comes out.

        Only once the flap debounce is over: before that the pending close
        fires on its own, and an unplug inside it is the usual end of a charge.
        """

        if self._peek(vin).session.close_timer is None or self._plug_state_live(vin) is not False:
            return
        stopped_at, _status = self._peek(vin).session.stopped
        if (datetime.now(timezone.utc) - stopped_at).total_seconds() < CHARGE_CLOSE_DEBOUNCE_S:
            return
        self._cancel_charge_close_timer(vin)
        self._finalize_charge_close(vin)

    def _plug_state_live(self, vin: str) -> Optional[bool]:
        return plug_state(self._live_values(vin, PLUG_DESCRIPTORS))

    def _cancel_charge_close_timer(self, vin: str) -> bool:
        """Cancel a pending close; return whether one was actually pending."""

        cancel = self._peek(vin).session.take("close_timer")
        if cancel is None:
            return False
        cancel()
        return True

    def _finalize_charge_close(self, vin: str, status: Optional[str] = None) -> None:
        """Actually close the debounced session and fire the stop events.

        The record ends when the charge stopped, not when the wait for a resume
        ran out; ``status`` overrides the car's own word for the end.
        """

        stopped_at, stopped_status = self._peek(vin).session.take("stopped") or (None, None)
        status = status or stopped_status
        tracking = self._peek(vin).charge.tracking
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
        session = self._close_session_record(vin, status, at=stopped_at)
        self._clear_charge_progress(vin)
        if session is not None:
            payload["energy_kwh"] = session.energy_kwh
            payload["cost"] = session.cost
            payload["session_id"] = session.id
        payload["complete"] = _charge_complete(soc, tracking.target_soc_percent)
        self._fire(EVENT_CHARGING_STOPPED, payload)
        target = tracking.target_soc_percent
        if payload["complete"]:
            self._fire(EVENT_CHARGING_COMPLETE, payload)
        elif charging_interrupted(
            soc, target, plug_state(self._live_values(vin, PLUG_DESCRIPTORS))
        ):
            # Short of the target with the cable still in, five minutes on (the
            # plugged-in hold) -- not a pause, and not the driver unplugging.
            self._fire(
                EVENT_CHARGING_INTERRUPTED, {**payload, "reason": self._charge_end_reason(vin)}
            )

    def _charge_end_reason(self, vin: str) -> Optional[str]:
        """BMW's own word for why the charge ended, when the car sent one live."""

        for value in self._live_values(vin, CHARGE_END_REASON_DESCRIPTORS).values():
            if isinstance(value, str) and value.strip().upper() not in ("", "INVALID", "UNKNOWN"):
                return value
        return None

    def _open_session_record(self, vin: str, tracking: SocTracking, started_at: datetime) -> None:
        if self.history is None:
            return
        location = self._charging_location(vin)
        self._car(vin).session.builder = SessionBuilder(
            vin,
            started_at,
            soc_start=tracking.last_soc_percent,
            target_soc=tracking.target_soc_percent,
            location=location,
            location_assumed=location is None,
            soc_before=tracking.soc_before_charge,
        )
        self._car(vin).session.cost = CostAccumulator(currency=self.pricing.currency)
        self._car(vin).session.mix = MixAccumulator()
        self._car(vin).session.held = HeldEnergy(currency=self.pricing.currency)
        # Where the wallbox meter stood when the charge began. Taken here, at
        # the transition, rather than at the first energy sample -- which can be
        # minutes later, by which time the meter has already counted kilowatt-
        # hours this session would then not be credited with. The billing cursor
        # starts in the same place, so the very first increment is billed at the
        # meter rather than being skipped for want of a baseline.
        meter_at_open = self._session_grid_meter_kwh(vin, self._peek(vin).session.builder)
        self._peek(vin).session.builder.note_grid_meter(meter_at_open)
        if meter_at_open is None:
            self._car(vin).session.grid_meter_billed = None
        else:
            self._car(vin).session.grid_meter_billed = meter_at_open
        # Snapshot straight away: a restart in the first five minutes of a charge
        # is exactly as likely as one in any other five, and an unsnapshotted
        # session is the thing this whole mechanism exists to prevent.
        self._snapshot_open_session(vin, force=True)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s OPEN at %s zone=%s soc=%s target=%s pricing=%s price_now=%s",
                vin,
                started_at.isoformat(),
                (location or {}).get("zone"),
                tracking.last_soc_percent,
                tracking.target_soc_percent,
                self.pricing.mode if self.pricing.enabled else "disabled",
                self._current_price(),
            )

    def _snapshot_open_session(self, vin: str, *, force: bool = False) -> None:
        """Write the in-progress charge to the store so a restart can resume it.

        Throttled to ``OPEN_SESSION_SAVE_INTERVAL_S``: every write rewrites the
        whole history document, and a charge samples for hours. ``force`` skips
        the throttle for the moments that matter -- the session opening, and the
        way out on unload or shutdown.
        """

        builder = self._peek(vin).session.builder
        if builder is None or self.history is None:
            return
        if self._peek(vin).session.restored_at is not None:
            # Restored but not yet confirmed by the stream: nothing has been
            # sampled into it since, so there is nothing new to write -- and
            # rewriting it would stamp a fresh ``saved_at`` on an old charge,
            # which is exactly the staleness test that decides whether it may be
            # resumed at all.
            return
        now = datetime.now(timezone.utc)
        if not force:
            last_saved = self._peek(vin).session.saved_at
            if (
                last_saved is not None
                and (now - last_saved).total_seconds() < OPEN_SESSION_SAVE_INTERVAL_S
            ):
                return

        snapshot = builder.to_dict()
        accumulator = self._peek(vin).session.cost
        snapshot["cost"] = None if accumulator is None else accumulator.to_dict()
        mix = self._peek(vin).session.mix
        snapshot["mix"] = None if mix is None else mix.to_dict()
        held = self._peek(vin).session.held
        snapshot["held"] = None if held is None else held.to_dict()
        # The counters live on the coordinator, not on the builder (they exist
        # even with history switched off), so they have to ride along or a
        # resumed session would integrate from zero.
        charge = self._peek(vin).charge
        snapshot["energy_wh"] = charge.energy_session_wh or 0.0
        snapshot["energy_raw_wh"] = charge.energy_session_raw_wh or 0.0
        snapshot["soc_baseline"] = charge.energy_session_soc
        snapshot["saved_at"] = now.isoformat()
        try:
            self.history.async_set_open_session(vin, snapshot)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not snapshot the in-progress charge for %s", mask_vin(vin))
            return
        self._car(vin).session.saved_at = now

    def _clear_open_session(self, vin: str) -> None:
        """Forget the in-progress charge for one VIN, in store and in memory."""

        self._car(vin).session.saved_at = None
        self._car(vin).session.restored_at = None
        self._car(vin).session.gap_credit_soc = None
        self._car(vin).session.grid_meter_billed = None
        self._cancel_restored_open_timer(vin)
        if self.history is None:
            return
        with suppress(Exception):
            self.history.async_clear_open_session(vin)

    @callback
    def async_restore_open_sessions(self) -> None:
        """Pick up charges that were still running when we last stopped.

        Runs once at setup, after the store has loaded and before the stream
        connects. A session young enough to still be the same charge is held
        open until the stream says otherwise (see :meth:`_fire_charging_event`);
        an older one is filed immediately as what it already is.
        """

        if self.history is None:
            return
        now = datetime.now(timezone.utc)
        for vin, snapshot in self.history.open_sessions().items():
            builder = SessionBuilder.from_dict(snapshot)
            if builder is None:
                self._clear_open_session(vin)
                continue
            self._car(vin).session.builder = builder
            self._car(vin).session.cost = CostAccumulator.from_dict(
                snapshot.get("cost"), currency=self.pricing.currency
            )
            self._car(vin).session.mix = MixAccumulator.from_dict(snapshot.get("mix"))
            self._car(vin).session.held = HeldEnergy.from_dict(
                snapshot.get("held"), currency=self.pricing.currency
            )
            energy_wh = _snapshot_float(snapshot.get("energy_wh")) or 0.0
            charge = self._car(vin).charge
            charge.energy_session_wh = energy_wh
            charge.energy_session_raw_wh = (
                _snapshot_float(snapshot.get("energy_raw_wh")) or energy_wh
            )
            charge.energy_session_start = builder.start
            charge.energy_session_soc = _snapshot_float(snapshot.get("soc_baseline"))
            # Resume the billing cursor where the snapshot left it, so the
            # kilowatt-hours the wallbox counted while we were away are not
            # billed at whatever the tariff happens to be now. They still reach
            # the record's ``grid_kwh`` -- the meter's own span covers the gap --
            # which is the same division of labour the battery-side gap credit
            # already makes: honest total, no invented price.
            if builder.grid_meter_last is None:
                self._car(vin).session.grid_meter_billed = None
            else:
                self._car(vin).session.grid_meter_billed = builder.grid_meter_last
            saved_at = _snapshot_time(snapshot.get("saved_at")) or builder.last_sample_at
            self._car(vin).session.saved_at = saved_at or now

            if not open_session_is_resumable(saved_at, now):
                if debug_enabled():
                    _LOGGER.debug(
                        "[charge] %s RESTORED stale (saved %s); closing at %s",
                        vin,
                        None if saved_at is None else saved_at.isoformat(),
                        None
                        if builder.last_sample_at is None
                        else builder.last_sample_at.isoformat(),
                    )
                self._close_session_record(
                    vin, "restart", at=builder.last_sample_at or builder.start
                )
                continue

            self._car(vin).session.restored_at = saved_at or now
            self._arm_restored_open_timer(vin)
            if debug_enabled():
                _LOGGER.debug(
                    "[charge] %s RESTORED open session from %s energy=%s kWh "
                    "soc=%s->%s; awaiting the stream",
                    vin,
                    builder.start.isoformat(),
                    round(energy_wh / 1000.0, 3),
                    builder.soc_start,
                    builder.soc_end,
                )

    def unconfirmed_restored_charges(self) -> list[str]:
        """VINs whose charge was restored across a restart and not yet confirmed."""

        return self._vins_where(lambda car: car.session.restored_at is not None)

    def _arm_restored_open_timer(self, vin: str) -> None:
        """Close a restored session the stream never confirmed either way."""

        self._cancel_restored_open_timer(vin)

        # @callback so HA runs it on the event loop: the close touches
        # async_dispatcher_send, which is loop-only.
        @callback
        def _fire(_now) -> None:
            self._car(vin).session.restored_timer = None
            if self._charge_is_evidently_running(vin):
                # BMW has not re-sent the status, but it is still reporting
                # charging power stamped after the snapshot -- which only a
                # running charge produces. Resume rather than split one plug-in
                # into two records.
                self._resume_restored_session(vin)
                tracking = self._peek(vin).charge.tracking
                if tracking is not None:
                    tracking.charging_active = True
                return
            self._close_restored_session(vin, "restart")

        self._car(vin).session.restored_timer = async_call_later(
            self.hass, RESTORED_SESSION_GRACE_S, _fire
        )

    def _charge_is_evidently_running(self, vin: str) -> bool:
        """Whether the stream still shows this restored charge under way.

        Charging power arriving with a timestamp *after* the snapshot can only
        come from a charge that outlived the restart: an open session is
        snapshotted continuously while it samples, so anything newer than the
        last snapshot was reported while we were away or since we came back.
        Deliberately not a plain "is the last power reading non-zero" test --
        that value lingers at its final figure long after the plug comes out.
        """

        tracking = self._peek(vin).charge.tracking
        saved_at = self._peek(vin).session.restored_at
        if tracking is None or saved_at is None:
            return False
        last_power_time = tracking.last_power_time
        if last_power_time is None or last_power_time <= saved_at:
            return False
        return bool(tracking.last_power_w and tracking.last_power_w > 0)

    def _cancel_restored_open_timer(self, vin: str) -> bool:
        cancel = self._peek(vin).session.take("restored_timer")
        if cancel is None:
            return False
        cancel()
        return True

    def _resume_restored_session(self, vin: str) -> None:
        """Carry on with a restored session the stream says is still charging."""

        self._car(vin).session.restored_at = None
        self._cancel_restored_open_timer(vin)
        builder = self._peek(vin).session.builder
        if builder is not None and builder.soc_end is not None:
            # Claim the charge that ran while nobody was watching, once a SoC
            # reading from *this* session arrives to measure it (see
            # :meth:`_apply_gap_credit`). The pack's SoC rose; that energy is as
            # real as the integrated kind, and the same ceiling bounds both.
            self._car(vin).session.gap_credit_soc = builder.soc_end
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s RESUME restored session (energy=%s kWh so far)",
                vin,
                self.get_session_energy_kwh(vin),
            )

    def _close_restored_session(self, vin: str, status: str) -> None:
        """File a restored session the charge did not survive.

        Ends at the last sample we actually watched, never at the moment we
        found out: the car stopped charging somewhere in the gap, and stretching
        the record to now would inflate its duration and its average power.
        """

        builder = self._peek(vin).session.builder
        end_at = None if builder is None else (builder.last_sample_at or builder.start)
        session = self._close_session_record(vin, status, at=end_at)
        if session is None:
            return
        payload = {
            "vin": vin,
            "entry_id": self.entry_id,
            "status": status,
            "soc": session.soc_end,
            "target_soc": session.target_soc,
            "energy_kwh": session.energy_kwh,
            "cost": session.cost,
            "session_id": session.id,
            "complete": _charge_complete(session.soc_end, session.target_soc),
        }
        self._fire(EVENT_CHARGING_STOPPED, payload)
        if payload["complete"]:
            # It reached its target while nobody watched: still the news an
            # automation waits for, by the same rule as a charge seen ending.
            self._fire(EVENT_CHARGING_COMPLETE, payload)

    def _apply_gap_credit(self, vin: str) -> None:
        """Credit the SoC a resumed session gained while we were not watching.

        Applied once, and only from a reading taken during the session itself --
        the same test the energy ceiling uses. Added to the *raw* total so the
        ceiling still has the last word: this can give back energy the restart
        cost us, never invent energy the pack never took.
        """

        baseline = self._peek(vin).session.gap_credit_soc
        if baseline is None:
            return
        soc_now = self._session_soc_percent(vin)
        if soc_now is None:
            return
        self._car(vin).session.gap_credit_soc = None
        capacity = self.battery_capacity_kwh(vin)
        gained = soc_now - baseline
        if not capacity or capacity <= 0 or gained <= 0:
            return
        credit_wh = gained / 100.0 * capacity * 1000.0
        charge = self._car(vin).charge
        charge.energy_session_raw_wh = (charge.energy_session_raw_wh or 0.0) + credit_wh
        charge.energy_session_wh = self._bounded_session_wh(vin, charge.energy_session_raw_wh)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s GAP credit %s kWh (soc %s -> %s across the restart)",
                vin,
                round(credit_wh / 1000.0, 3),
                baseline,
                soc_now,
            )

    def _close_session_record(self, vin: str, status: str, *, at: Optional[datetime] = None):
        # One last look at the wallbox meter, billed and noted before the settle it
        # may bound. The final energy deltas and the close can be minutes apart on
        # a charge that tapers, and those are kilowatt-hours the meter did count.
        if self._peek(vin).session.builder is not None:
            self._book_increment(vin, 0.0)
        self._settle_session_energy(vin)
        builder = self._peek(vin).session.take("builder")
        accumulator = self._peek(vin).session.take("cost")
        mix = self._peek(vin).session.take("mix")
        # Whatever is still held, the pack's SoC never accounted for.
        self._car(vin).session.held = None
        # Whatever happens below, this charge is no longer in progress: the
        # snapshot has to go even when there is no history to file the record in,
        # or the next start would resurrect it.
        self._clear_open_session(vin)
        if builder is None or self.history is None:
            return None

        energy_kwh = self.get_session_energy_kwh(vin)
        session = builder.close(
            at or datetime.now(timezone.utc),
            soc_end=self._session_soc_percent(vin),
            energy_kwh=energy_kwh,
            cost=resolve_cost(accumulated=accumulator.as_cost() if accumulator else None),
            reason=status,
        )
        session.mileage_km = self.odometer_km(vin)
        session.energy_mix = None if mix is None else mix.as_record()
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
            _LOGGER.exception("Could not record charging session for %s", mask_vin(vin))
            return None
        async_dispatcher_send(self.hass, self.signal_history, vin)
        self._log_battery_health(vin)
        return session

    def _clear_charge_progress(self, vin: str) -> None:
        """Zero BMW's progress figures for a charge that is over.

        Only the ones the car has sent at all, and only when not already zero,
        so a car that never streams them gains no entity and nothing re-renders
        for nothing (see :data:`CHARGE_PROGRESS_DESCRIPTORS`).
        """

        vehicle_state = self.data.get(vin)
        if not vehicle_state:
            return
        now = datetime.now(timezone.utc)
        for descriptor in CHARGE_PROGRESS_DESCRIPTORS:
            state = vehicle_state.get(descriptor)
            if state is None or state.value in (0, 0.0):
                continue
            vehicle_state[descriptor] = DescriptorState(
                value=0, unit=state.unit, timestamp=now.isoformat()
            )
            if descriptor == "vehicle.powertrain.electric.battery.charging.power":
                self._set_direct_power(vin, 0.0, now)
            async_dispatcher_send(self.hass, self.signal_update, vin, descriptor)

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

    def odometer_km(self, vin: str) -> Optional[float]:
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
            except TypeError, ValueError:
                continue
        return None

    def _fuel_litres(self, vin: str) -> Optional[float]:
        """The fuel in the tank, in litres, or ``None``."""

        state = self.get_state(vin, DESC_FUEL_L)
        litres = self._numeric(vin, DESC_FUEL_L)
        if litres is not None and state is not None and state.unit == "gal":
            litres *= LITRES_PER_GALLON
        return litres

    def _note_fuel(self, vin: str, now: datetime) -> None:
        """Feed the tank reading to the fuel log (``history/fuel.py``).

        Only for a car with an engine: an iX3 streams a tank level and litres
        too (#53's i4 a level), and on a battery car a "refuel" in them is noise.
        Stamped with the car's own time, so a REST catch-up that replays an older
        reading is recognised as one.
        """

        if self.history is None:
            return
        if not (self.is_combustion_only(vin) or self.is_plug_in_hybrid(vin)):
            return
        stamps = []
        for descriptor in (DESC_FUEL_L, DESC_FUEL_LEVEL):
            state = self.get_state(vin, descriptor)
            parsed = dt_util.parse_datetime(state.timestamp) if state and state.timestamp else None
            if parsed is not None:
                stamps.append(parsed)
        self.history.async_note_fuel(
            vin,
            max(stamps) if stamps else now,
            litres=self._fuel_litres(vin),
            percent=self._numeric(vin, DESC_FUEL_LEVEL),
            km=self.odometer_km(vin),
        )

    def fuel_report(self, vin: str) -> Dict[str, Any]:
        """The Fuel view: tank now, refuels, consumption, BMW's lifetime figures."""

        now = datetime.now(timezone.utc)
        report = self.history.fuel_report(vin, now) if self.history is not None else {}
        report = dict(report)
        report["tank"] = {
            "litres": self._fuel_litres(vin),
            "percent": self._numeric(vin, DESC_FUEL_LEVEL),
            "range_km": self._numeric(vin, "vehicle.drivetrain.lastRemainingRange"),
        }
        report["bmw_lifetime"] = self.obfcm_lifetime(vin)
        return report

    def obfcm_lifetime(self, vin: str) -> Optional[Dict[str, Any]]:
        """BMW's lifetime consumption counters (``history/obfcm.py``)."""

        return obfcm_lifetime(
            lambda descriptor: self._numeric(vin, descriptor),
            hybrid=self.is_plug_in_hybrid(vin),
        )

    def _note_odometer_day(self, vin: str, now: datetime) -> None:
        """Log today's odometer reading -- the car's pace (``history/odometer.py``)."""

        odometer_km = self.odometer_km(vin)
        if self.history is not None and odometer_km is not None:
            self.history.async_note_odometer(vin, dt_util.as_local(now).date(), odometer_km)

    def mileage_report(self, vin: str) -> Dict[str, Any]:
        """How far the car is driven, and where it stands against its allowance."""

        log = self.history.odometer_log(vin) if self.history is not None else {}
        today = dt_util.as_local(datetime.now(timezone.utc)).date()
        return mileage_report(log, today, self.mileage_allowances.get(vin))

    def km_per_day(self, vin: str) -> Optional[float]:
        """The car's average km a day over the last 90 days, or ``None``."""

        if self.history is None:
            return None
        return self.history.km_per_day(vin, dt_util.as_local(datetime.now(timezone.utc)).date())

    def _odometer_timestamp(self, vin: str) -> Optional[str]:
        """The car's own timestamp on the odometer reading ``_odometer_km`` uses."""

        for descriptor in DESC_ODOMETER:
            state = self.get_state(vin, descriptor)
            if state is not None and state.value is not None:
                return state.timestamp
        return None

    def last_vehicle_report(self, vin: str) -> Optional[datetime]:
        """When the car last reported its odometer, by the car's clock.

        The odometer rather than the newest timestamp of anything: a parked car's
        service-demand and Check Control fields arrive on BMW's own schedule, some
        with dates months old, so "anything" would say the car reported at 9 pm
        when its last drive ended at 9 am (discussion #56).
        """

        timestamp = self._odometer_timestamp(vin)
        return dt_util.parse_datetime(timestamp) if timestamp else None

    def pricing_for(self, vin: str) -> PricingConfig:
        """The pricing in force for ``vin``: its own meter and losses, if any."""

        return self.vehicle_pricing.get(vin, self.pricing)

    def trip_settings_for(self, vin: str) -> TripSettings:
        """The trip settings in force for ``vin``."""

        return self.vehicle_trip_settings.get(vin, self.trip_settings)

    def report_trips(self, vin: str) -> bool:
        """True when ``vin``'s trips are built from vehicle reports, not the live stream."""

        return self.trip_settings_for(vin).report_trips

    def numeric(self, vin: str, descriptor: str) -> Optional[float]:
        """A descriptor's last value as a number, or ``None`` (for ``assist_api``)."""

        return self._numeric(vin, descriptor)

    def _numeric(self, vin: str, descriptor: str) -> Optional[float]:
        state = self.get_state(vin, descriptor)
        if state is None or isinstance(state.value, bool):
            return None
        try:
            return float(state.value)
        except TypeError, ValueError:
            return None

    def _battery_kwh(self, vin: str, descriptor: str) -> Optional[float]:
        state = self.get_state(vin, descriptor)
        if state is None or state.value is None:
            return None
        try:
            value = float(state.value)
        except TypeError, ValueError:
            return None
        # BMW streams a sentinel (0 / "INVALID") before the value is known; a
        # zero capacity would poison both the vs-new ratio and the sanity check.
        return value if value > 0 else None

    def battery_nominal_kwh(self, vin: str) -> Optional[float]:
        """As-new HV battery size in kWh, for the battery-health vs-new figure."""

        return self._battery_kwh(vin, "vehicle.drivetrain.batteryManagement.batterySizeMax")

    def battery_capacity_kwh(self, vin: str) -> Optional[float]:
        """BMW's own current full-pack capacity in kWh, used to sanity-check ours.

        The SoC tracker already learns this from the ``maxEnergy`` stream (it
        scales SoC into energy with it), so prefer that; fall back to the raw
        descriptor for a car that streams it without an active SoC track yet.
        """

        tracking = self._peek(vin).charge.tracking
        if tracking is not None and tracking.max_energy_kwh:
            return tracking.max_energy_kwh
        return self._battery_kwh(vin, DESC_MAX_ENERGY)

    def current_soc(self, vin: str) -> Optional[float]:
        """The best state of charge we have, live estimate first.

        Public because anything that scales the pack into something else --
        remaining real range, most obviously -- needs exactly the chain the
        internal one walks: the tracker's extrapolated figure while charging,
        the last reading otherwise.
        """

        return self._current_soc(vin)

    def bmw_range_km(self, vin: str) -> Optional[float]:
        """BMW's own remaining *electric* range, as shown in the cluster.

        Deliberately only ``kombiRemainingElectricRange``: it is the number on
        the car's display, which is what a comparison against our measured one
        means to a user. ``lastRemainingRange`` looks like a drop-in fallback
        and is identical on a battery car, but on a plug-in hybrid it includes
        the fuel range -- comparing that against an electric range would make
        BMW's estimate look wildly optimistic for a reason that has nothing to
        do with either car's efficiency.
        """

        state = self.get_state(vin, DESC_BMW_RANGE)
        if state is None or state.value is None:
            return None
        try:
            value = float(state.value)
        except TypeError, ValueError:
            return None
        return value if value > 0 else None

    def _plug_state(self, vin: str) -> Optional[bool]:
        """Whether a cable is in the car, or ``None`` if the car doesn't say.

        Only gathers the car's current values: which descriptors count, in what
        order, and how their words are read all live in ``evcc.plug_state``,
        where they are unit-tested. Entity-side code in this file is not.
        """

        values: Dict[str, Any] = {}
        for descriptor in PLUG_DESCRIPTORS:
            state = self.get_state(vin, descriptor)
            if state is not None:
                values[descriptor] = state.value
        return plug_state(values)

    def _charging_state(self, vin: str) -> Optional[bool]:
        """Whether the car is charging, or ``None`` if it has never said.

        The distinction matters here in a way it does not elsewhere: everywhere
        else in this integration "not charging" is a safe default, but a charge
        controller told ``status: A`` (not connected) may stop charging the car
        it is plugged into. So a car that has never streamed a charging status
        publishes no status at all rather than a confident negative.
        """

        if self.get_state(vin, DESC_CHARGE_STATUS) is None:
            return None
        tracking = self._peek(vin).charge.tracking
        return None if tracking is None else tracking.charging_active

    def bridge_snapshot(self, vin: str) -> BridgeSnapshot:
        """This car as a charge controller sees it (see ``bridge.py``).

        The SoC is the same figure the integration's own sensor and card show --
        extrapolated while charging, the last reading otherwise -- so evcc can
        never disagree with the dashboard about the percentage.

        Charging power is published only when the car is known to be charging.
        BMW's power reading *lingers at its final value* long after the plug
        comes out (the charging ledger has the same problem and solves it with a
        ceiling), so a parked car would otherwise advertise 11 kW forever.

        ``updated`` is deliberately the last *measured* SoC, not "now": the
        extrapolated figure is always current by construction, so quoting its
        own freshness would tell a user nothing about whether to trust it.
        """

        tracking = self._peek(vin).charge.tracking
        charging = self._charging_state(vin)
        # Where the car is, as the charging ledger judges it: the open session's
        # place, else the current fix. See ``BridgeSnapshot.away``.
        builder = self._peek(vin).session.builder
        location = builder.location if builder is not None else self._charging_location(vin)
        power_kw: Optional[float] = None
        if charging is True:
            power_w = self._peek(vin).charge.charging_power_w
            power_kw = None if power_w is None else max(power_w, 0.0) / 1000.0
        elif charging is False:
            power_kw = 0.0
        return BridgeSnapshot(
            vin=vin,
            soc=self.current_soc(vin),
            charging=charging,
            plugged=self._plug_state(vin),
            range_km=self.bmw_range_km(vin),
            odometer_km=self.odometer_km(vin),
            limit_soc=None if tracking is None else tracking.target_soc_percent,
            charge_power_kw=power_kw,
            updated=None if tracking is None else tracking.last_update,
            away=not house_supplies_this_session(location, self._home_zone_name()),
        )

    def efficiency(self, vin: str, *, months: int = TREND_MONTHS) -> Dict[str, Any]:
        """Measured consumption, the range it implies, and the seasonal trend.

        One code path for all three consumers -- the real-range entity, the
        ``get_efficiency`` service and the card's efficiency view -- so none of
        them can quote a figure the others would disagree with.

        The capacity fed to the range arithmetic is our own learned one *only
        once battery health is confident in it*; until then BMW's ``maxEnergy``
        stands in. Both are battery-side usable energy, which is what makes
        dividing a battery-side consumption figure by it legitimate.
        """

        sessions = [] if self.history is None else self.history.sessions(vin)
        bmw_capacity = self.battery_capacity_kwh(vin)
        health = usable_capacity(
            sessions,
            nominal_kwh=self.battery_nominal_kwh(vin),
            sanity_kwh=bmw_capacity,
        )
        return efficiency_profile(
            sessions,
            battery_capacity_kwh=bmw_capacity,
            usable_capacity_kwh=health.usable_kwh if health.confident else None,
            soc_percent=self.current_soc(vin),
            bmw_range_km=self.bmw_range_km(vin),
            now=dt_util.utcnow(),
            localize=dt_util.as_local,
            months=months,
            hybrid=self.is_plug_in_hybrid(vin),
        )

    def _charging_location(self, vin: str) -> Optional[Dict[str, Any]]:
        """Where the car is plugged in, as an HA zone name when we can tell.

        Knowing a session happened "at home" is what costing needs, so the zone
        is all that is kept -- unless the user turned on *Remember charging
        locations*, which adds the point itself for the charging places map. A
        list of exact positions is far more sensitive than a zone name, so that
        stays the user's choice.
        """

        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        zone = self._zone_name(latitude, longitude)
        location: Dict[str, Any] = {"zone": zone} if zone else {"zone": None}
        if self.history is not None and self.history.remember_locations:
            location["lat"] = round(latitude, 5)
            location["lon"] = round(longitude, 5)
        return location

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
            except TypeError, ValueError:
                continue
        return None

    def zone_at(self, latitude: float, longitude: float) -> Optional[str]:
        """Public zone lookup, used when importing BMW's charging history so an
        imported session resolves to the same zone a live one would."""

        return self._zone_name(latitude, longitude)

    def _zone_name(self, latitude: float, longitude: float) -> Optional[str]:
        found = self._zone_at(latitude, longitude)
        return None if found is None else found[0]

    def _zone_at(self, latitude: float, longitude: float) -> Optional[tuple[str, str]]:
        """The zone a point lies in, as ``(name, entity_id)``, or ``None``."""

        from homeassistant.components import zone as zone_component

        try:
            found = zone_component.async_active_zone(self.hass, latitude, longitude)
        except Exception:  # noqa: BLE001 - zone lookup is best-effort
            return None
        if found is None:
            return None
        return (found.name or found.entity_id, found.entity_id)

    def _current_zone(self, vin: str) -> Optional[tuple[str, str]]:
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        return self._zone_at(latitude, longitude)

    # --- device triggers (vehicle_triggers.py) ------------------------------

    def _live_values(self, vin: str, descriptors: Iterable[str]) -> Dict[str, Any]:
        """Current values of those ``descriptors`` the stream sent since startup."""

        live = self._peek(vin).live_descriptors
        values: Dict[str, Any] = {}
        for descriptor in descriptors:
            if descriptor not in live:
                continue
            state = self.get_state(vin, descriptor)
            if state is not None:
                values[descriptor] = state.value
        return values

    def _value(self, vin: str, descriptor: str) -> Any:
        state = self.get_state(vin, descriptor)
        return None if state is None else state.value

    def _raw_value(self, vin: str, descriptor: str) -> Any:
        """Latest stored value of a descriptor, or None if never streamed."""

        state = self.get_state(vin, descriptor)
        return None if state is None else state.value

    def async_flush_charging(self) -> None:
        """Preserve every in-flight charge on the way out (unload or shutdown).

        Two different states need saving. A session that stopped within the last
        ``CHARGE_CLOSE_DEBOUNCE_S`` still has a timer counting down; cancelling
        it without closing would drop the session, so it is finalized here. A
        session that is still *charging* is snapshotted to the store instead, to
        be picked up by :meth:`async_restore_open_sessions` on the way back in.

        That second half used to be a deliberate omission -- the session was
        dropped, on the assumption BMW's charging-history import would recover
        it. It does not: the import is a manual, quota-costing service. Measured
        on a live instance, two restarts that happened to land mid-charge cost
        about 22 kWh in a single week, and every total built on the ledger
        (energy, cost, statistics, the driving summary's energy balance) read low
        by exactly that much.
        """

        for vin in self._vins_where(lambda car: car.session.close_timer is not None):
            if self._cancel_charge_close_timer(vin):
                with suppress(Exception):
                    self._finalize_charge_close(vin, "unload")
        for vin in self._vins_where(lambda car: car.session.builder is not None):
            self._snapshot_open_session(vin, force=True)
        # Nothing is waiting on these any more, and a timer left armed across a
        # reload would fire against the outgoing coordinator.
        for vin in self._vins_where(lambda car: car.session.restored_timer is not None):
            self._cancel_restored_open_timer(vin)

    def _current_soc(self, vin: str) -> Optional[float]:
        tracking = self._peek(vin).charge.tracking
        if tracking is None:
            return None
        if tracking.estimated_percent is not None:
            return round(tracking.estimated_percent, 1)
        if tracking.last_soc_percent is not None:
            return round(tracking.last_soc_percent, 1)
        return None

    def _home_zone_name(self) -> Optional[str]:
        state = self.hass.states.get("zone.home")
        return state.name if state is not None else "Home"

    def home_zone_name(self) -> Optional[str]:
        """Public form, for the BMW history import's home-only tariff."""

        return self._home_zone_name()

    def _work_zone_name(self, vin: str) -> Optional[str]:
        entity_id = self.trip_settings_for(vin).work_zone_entity
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
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
        except TypeError, ValueError:
            return None

    def _power_state_w(self, entity_id: Optional[str]) -> Optional[float]:
        """A power entity's value in watts, or ``None`` if it can't be read.

        Converts from kilowatts where the entity says so. A sensor with no unit
        at all is taken as watts (the Home Assistant default for
        ``device_class: power``), which is stated in the setting's help text --
        silently mixing kW into a watt sum would skew every attribution by a
        factor of a thousand and still look plausible.
        """

        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            value = float(state.state)
        except TypeError, ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "kw":
            return value * 1000.0
        if unit in ("mw",):
            return value * 1_000_000.0
        return value

    def _session_grid_meter_kwh(
        self, vin: str, builder: Optional[SessionBuilder]
    ) -> Optional[float]:
        """``vin``'s wallbox meter as a charge may see it -- ``None`` away from home.

        Every read of the meter on behalf of a session goes through here, so the
        location rule in :func:`meter_counts_this_session` cannot be skipped by a
        new call site (a test pins that). With no session record open there is
        no position to judge, and the reading is returned as it always was.
        """

        if builder is not None and not meter_counts_this_session(
            builder.location, self._home_zone_name()
        ):
            return None
        if self._meter_shared(vin):
            # Its advance is both cars' now. The billing cursor goes too, or
            # the first step after the other car stops would bill its energy.
            if builder is not None:
                builder.note_meter_shared()
            self._car(vin).session.grid_meter_billed = None
            return None
        return self._grid_meter_kwh(vin)

    def cars_charging_on(self, meter: str, *, exclude: Optional[str] = None) -> list[str]:
        """This account's cars charging at home on ``meter`` right now.

        Asked by every account's coordinator, not just this one: a BMW and a
        MINI on two accounts share one wallbox as easily as two cars on one.
        A car charging elsewhere doesn't count, even with the same meter
        bound (a second car takes the account's meter over by default).
        """

        found: list[str] = []
        for vin, tracking in self._tracked_cars():
            # A charge restored across a restart counts while its session waits
            # for the stream's word, not after it is filed unconfirmed: the flag
            # outlives that until the car speaks, which a car done charging may
            # not do for hours, keeping the other car off the meter all along.
            restored = (
                tracking.restored_charging and self._peek(vin).session.restored_at is not None
            )
            if vin == exclude or not (tracking.charging_active or restored):
                continue
            if self._plug_state(vin) is False or self.pricing_for(vin).grid_energy_entity != meter:
                continue
            builder = self._peek(vin).session.builder
            location = builder.location if builder is not None else self._charging_location(vin)
            if meter_counts_this_session(location, self._home_zone_name()):
                found.append(vin)
        return found

    def _meter_shared(self, vin: str) -> bool:
        """Whether another car, of any account, charges on ``vin``'s meter now."""

        meter = self.pricing_for(vin).grid_energy_entity
        if not meter:
            return False
        if self.cars_charging_on(meter, exclude=vin):
            return True
        return any(other.cars_charging_on(meter) for other in self._other_coordinators())

    def _other_coordinators(self) -> list[Any]:
        entries = getattr(self.hass, "config_entries", None)
        if entries is None:
            return []
        found = []
        for entry in entries.async_entries(DOMAIN):
            other = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
            if other is not None and other is not self:
                found.append(other)
        return found

    def _grid_meter_kwh(self, vin: str) -> Optional[float]:
        """The cumulative total of ``vin``'s wallbox energy meter, in kWh.

        The wallbox is the only thing in this integration that can *measure*
        what a charge drew from the grid; everything else derives it. Read as a
        running total and differenced over the session (see
        ``sessions.measured_grid_kwh``) rather than trusting a per-session
        counter, because a cumulative ``total_increasing`` energy sensor is what
        every wallbox integration exposes and the only shape that survives a
        Home Assistant restart mid-charge with its history intact.

        Units are honoured -- Wh and MWh sensors are both common -- and a sensor
        with no unit is taken as kWh, which the setting's own help text states
        and is what an energy sensor without a unit almost always is.
        """

        entity_id = self.pricing_for(vin).grid_energy_entity
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            value = float(state.state)
        except TypeError, ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value

    def _supply_shares(self) -> Optional[dict[str, float]]:
        """The site's supply mix right now, or ``None`` if it can't be known.

        Read live for the same reason the price is: a charge that starts in
        sunshine and finishes after dark is half and half, and a single reading
        at either end would describe only that end.
        """

        config = self.pricing
        if not config.mix_enabled:
            return None
        battery_w = self._power_state_w(config.battery_power_entity)
        if battery_w is not None and config.battery_power_invert:
            battery_w = -battery_w
        sun = self.hass.states.get("sun.sun")
        return supply_shares(
            pv_w=self._power_state_w(config.pv_power_entity),
            grid_w=self._power_state_w(config.grid_power_entity),
            battery_w=battery_w,
            sun_down=sun is not None and sun.state == "below_horizon",
        )

    def _bill_split(
        self,
        accumulator: CostAccumulator,
        billable_kwh: Optional[float],
        split: dict[str, float],
    ) -> None:
        """Bill one energy increment, each source at what that source costs.

        Own solar is billed at ``price_solar`` when the user has set one --
        usually the feed-in tariff, the money given up by not exporting. House
        battery and grid are both billed at the import price in force right now:
        the battery's contents could have come from the roof at noon or from a
        cheap-hour grid charge at three in the morning, and this integration
        cannot tell. Pricing it as solar would quietly claim a saving that may
        never have existed, so the conservative figure stands.
        """

        price_now = self._current_price()
        if not split:
            accumulator.add(billable_kwh, price_now)
            return
        solar_price = self.pricing.solar_price
        for source, amount in split.items():
            price = solar_price if source == SOURCE_PV and solar_price is not None else price_now
            accumulator.add(amount, price)

    def get_state(self, vin: str, descriptor: str) -> Optional[DescriptorState]:
        return self.data.get(vin, {}).get(descriptor)

    def iter_descriptors(self, *, binary: bool) -> Iterable[tuple[str, str]]:
        for vin, descriptors in self.data.items():
            for descriptor, descriptor_state in descriptors.items():
                if isinstance(descriptor_state.value, bool) == binary:
                    yield vin, descriptor

    def seen_descriptors(self, vin: str) -> set[str]:
        """Every descriptor this car has ever sent: live state plus the record.

        The coverage store remembers arrivals across restarts, so the drivetrain
        is known at setup, before the first message of the day.
        """

        seen = set(self.data.get(vin, {}))
        if self.coverage is not None:
            seen |= self.coverage.seen_descriptors(vin)
        return seen

    def drive_train(self, vin: str) -> Optional[str]:
        """BMW's basic-data ``driveTrain`` for a car (``BEV``/``PHEV``/``CONV``)."""

        extra = (self.device_metadata.get(vin) or {}).get("extra_attributes") or {}
        return extra.get("drive_train")

    def is_combustion_only(self, vin: str) -> bool:
        """An engine and no high-voltage battery, by basic data or by evidence."""

        return is_combustion_only(self.seen_descriptors(vin), self.drive_train(vin))

    def is_plug_in_hybrid(self, vin: str) -> bool:
        return is_plug_in_hybrid(self.seen_descriptors(vin), self.drive_train(vin))

    async def async_handle_connection_event(
        self, status: str, *, reason: Optional[str] = None
    ) -> None:
        now = datetime.now(timezone.utc)
        outage_s = self.health.note(status, reason, now)
        if outage_s is not None and self.on_reconnect_after_outage is not None:
            self.on_reconnect_after_outage(outage_s)
        self._log_diagnostics()
        self._evaluate_stream_repairs(now)

    def _evaluate_stream_repairs(self, now: Optional[datetime] = None) -> None:
        """Raise or clear the stream-health repair issues (``stream_health``).

        Idempotent and latched: the underlying registry calls are only made on a
        state transition, so this is cheap to run from both the watchdog tick and
        every connection event. Both issues carry a ``learn_more_url`` to the
        matching Wiki anchor -- the fix, not just the symptom.
        """

        unauthorized, no_data = self.health.repairs_due(
            now or datetime.now(timezone.utc),
            last_message_at=self.last_message_at,
            started_at=self.stream_started_at,
        )
        self._set_unauthorized_issue(unauthorized)
        self._set_no_data_issue(no_data)

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Fire one of the integration's events, with the car's name on it.

        ``vehicle`` is what a notification says ("i5 eDrive40: charging
        complete"), so an automation for two cars needs no lookup of its own;
        ``None`` while BMW hasn't named the car (never the VIN).
        """

        vin = payload.get("vin")
        name = self.names.get(vin) if vin else None
        self.hass.bus.async_fire(
            event_type, {**payload, "vehicle": name if name and name != vin else None}
        )

    def _account_name(self) -> str:
        """The account as its repairs name it: the entry's title, its cars."""

        entries = getattr(self.hass, "config_entries", None)
        entry = entries.async_get_entry(self.entry_id) if entries is not None else None
        return getattr(entry, "title", None) or "BavarianData"

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
                "account": self._account_name(),
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
                "account": self._account_name(),
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
                self.watchdog_tick()
        except asyncio.CancelledError:
            return

    def watchdog_tick(self) -> None:
        """One heartbeat: advance the estimates, settle what timed out, check the stream."""

        self._log_diagnostics()
        self._evaluate_stream_repairs()

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
        for vin, _tracking in self._tracked_cars():
            if self._apply_soc_estimate(vin, now, notify=False):
                updated_vins.append(vin)
        for vin in updated_vins:
            async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
        for vin, _tracking in self._tracked_cars():
            if self._integrate_energy(vin, now):
                async_dispatcher_send(self.hass, self.signal_energy, vin)
        # A late reading with no fix after it: the car went to sleep standing.
        for vin, at in [
            (vin, car.trip.late_reading_at)
            for vin, car in self._cars.items()
            if car.trip.late_reading_at is not None
        ]:
            if (now - at).total_seconds() >= LATE_READING_SETTLE_S:
                self.trips.settle_late_reading(vin, moved=False)
        async_dispatcher_send(self.hass, self.signal_diagnostics)

    def _apply_soc_estimate(self, vin: str, now: datetime, notify: bool = True) -> bool:
        charge = self._peek(vin).charge
        tracking = charge.tracking
        if not tracking:
            changed = any(
                value is not None
                for value in (charge.soc_estimate, charge.soc_rate, charge.charge_eta)
            )
            if vin in self._cars:
                for name in (
                    "soc_estimate",
                    "soc_rate",
                    "charge_eta",
                    "charging_power_w",
                    "direct_power_w",
                    "ac_voltage_v",
                    "ac_current_a",
                    "ac_phase_count",
                    "soc_source",
                ):
                    setattr(charge, name, None)
            if notify and changed:
                async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
            return changed
        learned = self._meter_rate_if_driving(vin)
        tracking.follow_meter(
            None
            if learned is None
            else self._session_grid_meter_kwh(vin, self._peek(vin).session.builder),
            learned,
            now,
        )
        source = "meter" if tracking.meter_model is not None else "stream"
        source_changed = charge.soc_source != source
        charge.soc_source = source
        percent = tracking.estimate(now)
        rate = tracking.current_rate_per_hour()

        rate_changed = False
        if rate in (None, 0):
            if charge.soc_rate is not None:
                charge.soc_rate = None
                rate_changed = True
        else:
            rounded_rate = round(rate, 3)
            if charge.soc_rate != rounded_rate:
                charge.soc_rate = rounded_rate
                rate_changed = True

        estimate_changed = False
        if percent is None:
            if charge.soc_estimate is not None:
                charge.soc_estimate = None
                estimate_changed = True
        else:
            rounded_percent = round(percent, 2)
            if charge.soc_estimate != rounded_percent:
                charge.soc_estimate = rounded_percent
                estimate_changed = True
        eta = self._charge_eta_minutes(vin, tracking)
        eta_changed = charge.charge_eta != eta
        charge.charge_eta = eta
        updated = rate_changed or estimate_changed or source_changed or eta_changed
        if notify and updated:
            async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
        return updated

    def get_soc_rate(self, vin: str) -> Optional[float]:
        return self._peek(vin).charge.soc_rate

    def get_soc_estimate(self, vin: str) -> Optional[float]:
        return self._peek(vin).charge.soc_estimate

    def get_charge_eta(self, vin: str) -> Optional[int]:
        return self._peek(vin).charge.charge_eta

    def _charge_eta_minutes(self, vin: str, tracking: SocTracking) -> Optional[int]:
        """Minutes to the charge target, for the ``charge_eta`` sensor.

        Nothing without a bound wallbox meter: the figure is the meter's, and
        without one the sensor would only ever say 0 or nothing. With one, a
        car that isn't charging needs no time (0), and a charge the meter isn't
        driving -- away from home, the first minutes before its rate settles,
        a paused solar charge -- is ``None``, which leaves the card on BMW's
        own figure (:meth:`SocTracking.minutes_to_target`).
        """

        if not self.pricing_for(vin).grid_energy_entity:
            return None
        if not (tracking.charging_active or tracking.restored_charging):
            return 0
        minutes = tracking.minutes_to_target()
        return None if minutes is None else round(minutes)

    def learned_meter_rate(self, vin: str) -> Optional[MeterRate]:
        """What wallbox kWh do to this car's SoC, from its home charges in the ledger."""

        if self.history is None or not self.pricing_for(vin).grid_energy_entity:
            return None
        return meter_rate(self.history.sessions(vin), self._home_zone_name())

    def _meter_rate_if_driving(self, vin: str) -> Optional[MeterRate]:
        """The learned rate, if the wallbox meter may drive this car's estimate now.

        Every condition is one the meter itself cannot check, because nothing
        it reports says which car it charged: this car must be charging, not
        reporting its cable out, at home by the ledger's own rule
        (:func:`meter_counts_this_session`), and the only car charging on its
        meter -- with two on one meter, its advance is both of theirs and
        neither may claim it. Cars with a meter each don't get in each other's way. The cable check is for a charging status BMW forgot to
        end: unplugged, the car cannot be what the wallbox is charging. Anything
        else, and the stream drives exactly as it did before a meter was bound.
        """

        tracking = self._peek(vin).charge.tracking
        if tracking is None or not (tracking.charging_active or tracking.restored_charging):
            return None
        if self._plug_state(vin) is False or self._meter_shared(vin):
            return None
        builder = self._peek(vin).session.builder
        location = builder.location if builder is not None else self._charging_location(vin)
        if not meter_counts_this_session(location, self._home_zone_name()):
            return None
        return self.learned_meter_rate(vin)

    def soc_estimate_attributes(self, vin: str) -> Dict[str, Any]:
        """What drives the estimate, for the entity's attributes.

        Empty without a bound wallbox meter: the stream is then the only source
        there is, and saying so on every install would be noise.
        ``meter_reading_kwh`` is also what the entity hands back on restore
        (see :meth:`restore_soc_cache`).
        """

        if not self.pricing_for(vin).grid_energy_entity:
            return {}
        tracking = self._peek(vin).charge.tracking
        learned = self.learned_meter_rate(vin)
        driving = tracking is not None and tracking.meter_model is not None
        attrs: Dict[str, Any] = {
            "estimate_source": "meter" if driving else "stream",
            "meter_sessions": 0 if learned is None else learned.samples,
            "meter_percent_per_kwh": None if learned is None else learned.percent_per_kwh,
            "meter_overhead_kw": None if learned is None else learned.overhead_kw,
        }
        if driving and tracking.meter_kwh is not None:
            attrs["meter_reading_kwh"] = round(tracking.meter_kwh, 3)
        return attrs

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
        if is_placeholder(descriptor, value):
            value = None
        if value is None:
            if descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
                tracking = self._car(vin).charge.tracker()
                tracking.update_target_soc(None, parsed_ts)
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
            except TypeError, ValueError:
                return
        vehicle_state[descriptor] = DescriptorState(
            value=stored_value,
            unit=unit,
            timestamp=timestamp,
        )
        self.events.seed_edge(vin, descriptor, stored_value)
        if descriptor in (DESC_GPS_LAT, DESC_GPS_LON):
            self.trips.seed_gps_from_restore(vin)
        if descriptor in DESC_ODOMETER:
            # The odometer comes back through the entity restore, not the
            # stream: log it on the day the car reported it, or the mileage
            # figures stop short of the car's reading until it next drives.
            self._note_odometer_day(vin, parsed_ts or datetime.now(timezone.utc))
        tracking = self._car(vin).charge.tracker()

        updated = False
        if descriptor == "vehicle.drivetrain.batteryManagement.header":
            try:
                percent = float(value)
            except TypeError, ValueError:
                return
            tracking.update_actual_soc(percent, parsed_ts, restored=True)
            updated = True
        elif descriptor == "vehicle.drivetrain.batteryManagement.maxEnergy":
            try:
                max_energy = float(value)
            except TypeError, ValueError:
                return
            tracking.update_max_energy(max_energy)
            updated = True
        elif descriptor == "vehicle.powertrain.electric.battery.charging.power":
            try:
                power_w = float(value)
            except TypeError, ValueError:
                self._set_direct_power(vin, None, parsed_ts)
            else:
                self._set_direct_power(vin, power_w, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.status":
            if isinstance(value, str):
                # A restored status may be hours old, so it only keeps the SoC
                # estimate climbing, and only while a restored charge agrees the
                # car was charging. Without this the estimate froze at its
                # restored value until BMW next woke up -- two hours, on the i5.
                builder = self._peek(vin).session.builder
                since = (
                    builder.start
                    if builder is not None and self._peek(vin).session.restored_at is not None
                    else None
                )
                tracking.restore_status(value, since)
                updated = True
        elif descriptor == "vehicle.powertrain.electric.battery.stateOfCharge.target":
            try:
                target = float(value)
            except TypeError, ValueError:
                tracking.update_target_soc(None, parsed_ts)
                updated = True
            else:
                tracking.update_target_soc(target, parsed_ts)
                updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
            try:
                voltage_v = float(value)
            except TypeError, ValueError:
                self._set_ac_voltage(vin, None, parsed_ts)
            else:
                self._set_ac_voltage(vin, voltage_v, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
            try:
                current_a = float(value)
            except TypeError, ValueError:
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
            self._car(vin).charge.soc_estimate = round(tracking.estimated_percent, 2)
        elif tracking.last_soc_percent is not None:
            self._car(vin).charge.soc_estimate = round(tracking.last_soc_percent, 2)
        if tracking.rate_per_hour not in (None, 0):
            self._car(vin).charge.soc_rate = round(tracking.rate_per_hour, 3)
        else:
            self._car(vin).charge.soc_rate = None

    def restore_soc_cache(
        self,
        vin: str,
        *,
        estimate: Optional[float] = None,
        rate: Optional[float] = None,
        timestamp: Optional[datetime] = None,
        meter_kwh: Optional[float] = None,
    ) -> None:
        tracking = self._car(vin).charge.tracker()
        reference_time = timestamp or datetime.now(timezone.utc)
        # Entities restore in no fixed order, so a restored estimate competes
        # with the restored SoC reading on age, not on who came first.
        if estimate is not None and (timestamp is not None or tracking.estimated_percent is None):
            # The meter reading only means something next to the moment it
            # was taken; without a timestamp it cannot be placed.
            if tracking.adopt_estimate(
                estimate, reference_time, meter_kwh=meter_kwh if timestamp is not None else None
            ):
                self._car(vin).charge.soc_estimate = round(estimate, 2)
        if rate is not None:
            tracking.rate_per_hour = rate if rate not in (None, 0) else None
            if tracking.rate_per_hour:
                self._car(vin).charge.soc_rate = round(tracking.rate_per_hour, 3)
                # Only believe the restored rate means "charging" when a restored
                # session agrees. Setting it unconditionally made a stale rate
                # sensor claim a charge was running: the next genuine
                # CHARGINGACTIVE then looked like no transition at all
                # (``was_charging == now_charging``) and opened no session.
                if self._peek(vin).session.restored_at is not None:
                    tracking.charging_active = True
                if tracking.max_energy_kwh not in (None, 0):
                    tracking.last_power_w = (
                        (tracking.rate_per_hour / 100.0) * tracking.max_energy_kwh * 1000.0
                    )
                tracking.last_power_time = reference_time
            else:
                self._car(vin).charge.soc_rate = None

    @staticmethod
    def _build_device_metadata(vin: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        model_name = (
            payload.get("modelName") or payload.get("modelRange") or payload.get("series") or vin
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
            # "left" / "right": where the driver sits. BMW keys side doors and
            # windows by seat, so the card needs this to draw them on the
            # right side of the car (see steering.py).
            "steering_side": steering_side(raw_payload.get("steering")),
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
            "name": str(model_name),
            "manufacturer": str(brand),
            "serial_number": str(raw_payload.get("vin") or vin),
            "extra_attributes": display_attrs,
            "raw_data": raw_payload,
        }
        model = (
            raw_payload.get("modelName")
            or raw_payload.get("series")
            or raw_payload.get("modelRange")
        )
        # The device registry takes text only (anything else is reported from HA
        # 2026.10 and breaks in 2026.12), and BMW's fields are not typed.
        if model:
            metadata["model"] = str(model)
        if raw_payload.get("puStep"):
            metadata["sw_version"] = str(raw_payload["puStep"])
        if raw_payload.get("bodyType"):
            metadata["hw_version"] = str(raw_payload["bodyType"])
        return metadata

    def apply_basic_data(self, vin: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata = self._build_device_metadata(vin, payload)
        if not metadata:
            return None
        self.device_metadata[vin] = metadata
        if is_motorcycle(payload.get("brand")):
            # CarData lists BMW Motorrad bikes, but BMW streams nothing for them,
            # so setup "works" and then stays empty. Say so where it's seen.
            # The issue id carries a digest, never the VIN (diagnostics attach
            # issue ids unredacted), and the placeholder only a model name.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                motorcycle_issue_id(self.entry_id, vin),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="motorcycle_unsupported",
                translation_placeholders={
                    "vehicle": payload.get("modelName") or payload.get("series") or "BMW Motorrad",
                },
                learn_more_url=(
                    f"{WIKI_TROUBLESHOOTING}#does-bavariandata-work-with-a-bmw-motorcycle"
                ),
            )
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
