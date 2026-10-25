"""State coordinator for BMW CarData streaming payloads."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Iterable, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DIAGNOSTIC_LOG_INTERVAL,
)
from .debug import debug_enabled, mask_vin
from .history.efficiency import TREND_MONTHS, efficiency_profile
from .history.health import usable_capacity
from .history.obfcm import lifetime as obfcm_lifetime
from .history.meter_rate import MeterRate, meter_rate
from .history.pricing import (
    PricingConfig,
)
from .evcc import PLUG_DESCRIPTORS, BridgeSnapshot, plug_state
from .history.sessions import (
    house_supplies_this_session,
)
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
    SEG_FRESH_S,
    DESC_BMW_RANGE,
    DESC_MAX_ENERGY,
    DESC_CHARGE_STATUS,
)
from .car_state import NO_CAR, DescriptorState, Vehicle
from .coverage import is_combustion_only, is_plug_in_hybrid
from .soc_tracking import SocTracking
from .steering import steering_side
from .trip_capture import TRIP_CAPTURE_FILE, TripLog
from .vehicle_charging import VehicleCharging
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

_LOGGER = logging.getLogger(__name__)


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
    # BMW refuses the account's login until the user signs in again; the cars'
    # entities are unavailable meanwhile (``reauth.ReauthGate.refused``).
    login_refused: bool = field(default=False, init=False)
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
    # The SoC estimate, energy counters and charging sessions (``vehicle_charging``).
    charging: VehicleCharging = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.events = VehicleEvents(self)
        self.reports = VehicleReports(self)
        self.trips = VehicleTrips(self)
        self.charging = VehicleCharging(self)

    def async_flush_charging(self) -> None:
        """Preserve every in-flight charge on the way out (unload or shutdown)."""

        self.charging.async_flush_charging()

    @callback
    def async_restore_open_sessions(self) -> None:
        """Pick up charges that were still running when we last stopped."""

        self.charging.async_restore_open_sessions()

    def cars_charging_on(self, meter: str, *, exclude: Optional[str] = None) -> list[str]:
        """This account's cars charging at home on ``meter`` right now.

        Asked by every account's coordinator, not just this one: a BMW and a
        MINI on two accounts share one wallbox as easily as two cars on one.
        """

        return self.charging.cars_charging_on(meter, exclude=exclude)

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
    def signal_availability(self) -> str:
        return f"{DOMAIN}_{self.entry_id}_availability"

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
                    self.charging.set_direct_power(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                    self.charging.set_ac_voltage(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                    self.charging.set_ac_current(vin, None, parsed_ts)
                elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                    self.charging.set_ac_phase(vin, None, parsed_ts)
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
                    self.charging.set_direct_power(vin, None, parsed_ts)
                else:
                    self.charging.set_direct_power(vin, power_w, parsed_ts)
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
                    self.charging.set_ac_voltage(vin, None, parsed_ts)
                else:
                    self.charging.set_ac_voltage(vin, voltage_v, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                try:
                    current_a = float(value)
                except TypeError, ValueError:
                    self.charging.set_ac_current(vin, None, parsed_ts)
                else:
                    self.charging.set_ac_current(vin, current_a, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                self.charging.set_ac_phase(vin, value, parsed_ts)
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
            self.charging.fire_charging_event(vin, status_change[0], tracking, status_change[1])
        if any(descriptor in data for descriptor in PLUG_DESCRIPTORS):
            self.charging.close_held_charge_on_unplug(vin)

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

        self.charging.apply_soc_estimate(vin, tracking, now)
        if self.charging.integrate_energy(vin, now):
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

    @callback
    def unconfirmed_restored_charges(self) -> list[str]:
        """VINs whose charge was restored across a restart and not yet confirmed."""

        return self._vins_where(lambda car: car.session.restored_at is not None)

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
        for vin, tracking in self._tracked_cars():
            if self.charging.apply_soc_estimate(vin, tracking, now, notify=False):
                updated_vins.append(vin)
        for vin in updated_vins:
            async_dispatcher_send(self.hass, self.signal_soc_estimate, vin)
        for vin, _tracking in self._tracked_cars():
            if self.charging.integrate_energy(vin, now):
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

    def get_soc_rate(self, vin: str) -> Optional[float]:
        return self._peek(vin).charge.soc_rate

    def get_soc_estimate(self, vin: str) -> Optional[float]:
        return self._peek(vin).charge.soc_estimate

    def get_charge_eta(self, vin: str) -> Optional[int]:
        return self._peek(vin).charge.charge_eta

    def learned_meter_rate(self, vin: str) -> Optional[MeterRate]:
        """What wallbox kWh do to this car's SoC, from its home charges in the ledger."""

        if self.history is None or not self.pricing_for(vin).grid_energy_entity:
            return None
        return meter_rate(self.history.sessions(vin), self._home_zone_name())

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
                self.charging.set_direct_power(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acVoltage":
                self.charging.set_ac_voltage(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
                self.charging.set_ac_current(vin, None, parsed_ts)
            elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
                self.charging.set_ac_phase(vin, None, parsed_ts)
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
                self.charging.set_direct_power(vin, None, parsed_ts)
            else:
                self.charging.set_direct_power(vin, power_w, parsed_ts)
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
                self.charging.set_ac_voltage(vin, None, parsed_ts)
            else:
                self.charging.set_ac_voltage(vin, voltage_v, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.acAmpere":
            try:
                current_a = float(value)
            except TypeError, ValueError:
                self.charging.set_ac_current(vin, None, parsed_ts)
            else:
                self.charging.set_ac_current(vin, current_a, parsed_ts)
            updated = True
        elif descriptor == "vehicle.drivetrain.electricEngine.charging.phaseNumber":
            self.charging.set_ac_phase(vin, value, parsed_ts)
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
