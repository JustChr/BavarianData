"""State coordinator for BMW CarData streaming payloads."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, Iterable, Mapping, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    DIAGNOSTIC_LOG_INTERVAL,
    EVENT_ALARM,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
    EVENT_CHARGING_COMPLETE,
    EVENT_CHARGING_INTERRUPTED,
    EVENT_CHECK_CONTROL,
    EVENT_PRECONDITIONING,
    EVENT_SITUATION,
    EVENT_TRIP_ENDED,
    EVENT_VEHICLE_REPORT,
    EVENT_ZONE_ARRIVED,
    EVENT_ZONE_LEFT,
)
from .debug import debug_enabled, mask_vin
from .history.classify import (
    COMMUTE_CHAIN_MAX_LEGS,
    classify_trip,
    commute_chain,
)
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
from .history.trips import CLASS_COMMUTE, SOURCE_AUTO, Trip, place
from .history.trip_builder import (
    GpsTracker,
    TripBuilder,
    is_gps_movement,
    is_noise_trip,
    silence_implies_stop,
)
from .car_profile import REST, STREAM, CarProfile
from .car_state import NO_CAR, ClosedTrip, Vehicle
from .coverage import is_combustion_only, is_plug_in_hybrid
from .phone_route import PhoneRoute
from .soc_tracking import SocTracking
from .steering import steering_side
from .trip_capture import TRIP_CAPTURE_FILE, TripLog
from .stream_health import (
    NO_DATA_REPAIR_AFTER_S,
    UNAUTHORIZED_REPAIR_AFTER_S,
    WIKI_TROUBLESHOOTING,
    StreamHealth,
)
from .placeholders import is_placeholder
from .structured_values import EMPTY_WHEN_NULL, structured_state
from .trip_settings import TripSettings
from .tyre import parse_tyre_diagnosis
from .mileage import mileage_report
from .units import miles_to_km, normalize_unit, unit_choices
from .vehicle_report import (
    ARM,
    CANDIDATE,
    REPORT_QUIET_S,
    SEED,
    ReportDecision,
    add_start_mark,
    fuel_stats,
    report_decision,
    report_payload,
    report_time,
    take_start_mark,
)
from .vehicle_support import is_motorcycle, motorcycle_issue_id
from .vehicle_triggers import (
    ALARM_DESCRIPTOR,
    CHECK_CONTROL_DESCRIPTOR,
    LOCK_DESCRIPTORS,
    PRECONDITIONING_DESCRIPTOR,
    PRECONDITIONING_ERROR_DESCRIPTOR,
    SITUATION_PARKED_UNLOCKED,
    SITUATION_PARKED_WINDOW_OPEN,
    SITUATION_PLUGGED_NOT_CHARGING,
    WINDOW_DESCRIPTORS,
    EdgeTracker,
    SituationTracker,
    alarm_on,
    charging_interrupted,
    fresh,
    lock_state,
    message_changes,
    open_windows,
    parked_unlocked,
    parked_window_open,
    plugged_not_charging,
    preconditioning_active,
    window_open,
)

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
# The fuel left, in litres: what a report trip's fuel figures are read from.
DESC_FUEL_L = "vehicle.drivetrain.fuelSystem.remainingFuel"
DESC_FUEL_LEVEL = "vehicle.drivetrain.fuelSystem.level"
# A US gallon, for a tank BMW reports in gallons; the fuel log keeps litres.
LITRES_PER_GALLON = 3.785411784
DESC_SEG_PREFIX = "vehicle.trip.segment.end."
DESC_SEG_DISTANCE = "vehicle.trip.segment.end.travelledDistance"
DESC_SEG_RECUP = "vehicle.trip.segment.accumulated.drivetrain.electricEngine.recuperationTotal"
DESC_SEG_CONSUMPTION = (
    "vehicle.trip.segment.accumulated.drivetrain.electricEngine.energyConsumptionComfort"
)
DESC_SEG_ACCEL_STARS = "vehicle.trip.segment.accumulated.acceleration.starsAverage"
DESC_SEG_BRAKE_STARS = "vehicle.trip.segment.accumulated.chassis.brake.starsAverage"
DESC_SEG_ECO = (
    "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveEcoPro"
)
DESC_SEG_ELECTRIC = (
    "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveElectric"
)
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

# Once the car has been at rest this long with no new segment batch, treat the
# trip as ended. Long enough to ride out a traffic light, short enough that a
# parked car's trip closes promptly.
TRIP_CLOSE_DEBOUNCE_S = 300

# The debounce above assumes the fixes that stopped moving are evidence the *car*
# stopped. When the position stream simply goes quiet mid-drive -- the i5 can gap
# for minutes, and a tunnel or a bad cell patch longer still -- there is no such
# evidence, and closing on the timer alone splits one drive into two. So a close
# with no stop evidence behind it is *held*: the trip stays open until a fix comes
# back and settles it (a fix showing the car where it left off closes the trip; a
# fix showing it has moved on continues the same trip). This caps how long that
# wait may last, for a stream that never comes back. Generous, because the close
# is backdated to the last movement either way (see ``_trip_end_time``), so a late
# close costs latency, not accuracy.
TRIP_SILENT_HOLD_MAX_S = 1800

# A driver-door-close start marker is only honoured for opening a trip within
# this long -- so an unrelated door-close hours before a drive can't seed it.
PENDING_START_MAX_S = 300
# A driver-door-open ends an open trip only once the car has been stationary at
# least this long, so a door read that flickers open mid-move can't close a live
# drive. Short, because a driver opens their door only after actually stopping.
DOOR_ARRIVAL_STOP_S = 20
# How long a door-open waits for the rest of its snapshot when no fix has shown
# the car standing yet: an i4 sends the door ahead of its final fix (issue #183).
DOOR_SETTLE_S = 10
# An odometer or SoC reading that lands this soon after a trip closed, with the
# car not moving since, is that trip's: the i5 once sent its last km 52 s after
# the door ended the drive. Settled by the next fix (standing: the trip's; moving:
# the next drive's) or, with no fix at all, after LATE_READING_SETTLE_S.
LATE_READING_MAX_S = 300
LATE_READING_SETTLE_S = 60
# BMW streams latitude and longitude as two separate messages ~1 s apart, so a
# fix is only complete once *both* have arrived. Detection waits for that (see
# ``_gps_fix_ready``); this guard stops a frozen single component from stalling
# GPS processing forever if the pairing assumption ever fails.
GPS_PAIR_STALE_S = 120
# The two halves of a fix, tracked by arrival rather than by timestamp: waiting
# for both messages is the only pairing rule that cannot silently drift out of
# step (see ``_gps_fix_ready``).
GPS_PART_LAT = "lat"
GPS_PART_LON = "lon"
GPS_PAIR_PARTS = frozenset({GPS_PART_LAT, GPS_PART_LON})

# BMW's ``charging.status`` can briefly drop out of CHARGINGACTIVE mid-charge (a
# momentary NOCHARGING/PAUSED blip on the stream) and come straight back. Closing
# the session on the transition would split one plug-in into two records -- one
# then enriched by BMW's import, the other orphaned and double-counted -- so the
# close is debounced: if charging resumes within this window the same session
# continues instead. Long enough to ride out a flap, short enough that a genuine
# unplug still records promptly.
CHARGE_CLOSE_DEBOUNCE_S = 120

# How long a stopped charge waits for a resume while the cable is still in. Some
# cars stop for longer than the flap debounce mid-charge and carry on by
# themselves: a MINI Cooper SE on a portable charger paused for 2:39 and 2:03 and
# was filed as three charges (#180). With the plug still connected the session
# stays open this long after the stop; an unplug closes it at once, and a car
# that doesn't stream its plug keeps the plain debounce. The record still ends
# at the stop, so only the stop events wait.
CHARGE_PLUGGED_HOLD_S = 300

# How often an in-progress charge is written to the store. Every write rewrites
# the whole history document, so this is a trade between disk churn and how much
# of a charge a *crash* can cost -- a clean restart or reload loses nothing,
# because both snapshot on the way out. Five minutes of a 11 kW charge is under
# a kilowatt-hour, and the SoC-derived ceiling stops even that from inflating a
# resumed total beyond what the pack can hold.
OPEN_SESSION_SAVE_INTERVAL_S = 300

# How long a restored in-progress charge waits for the stream to say whether it
# is still running. BMW republishes ``charging.status`` on its own schedule --
# measured at seven minutes after one restart, and not at all after another
# where the charge had ended meanwhile -- so the wait has to be generous. When
# it expires the session is closed as what it already is, ending at its last
# sample rather than at the timeout.
RESTORED_SESSION_GRACE_S = 900

# Charging statuses that mean this plug-in is over, as opposed to merely not
# delivering right now. BMW's enum is nocharging / initialization /
# chargingactive / chargingpaused / chargingended / chargingerror, and the
# middle two are *transient*: a charge that is handshaking or paused is still
# the same charge. Only used to decide the fate of a session restored across a
# restart -- a live session has the flap debounce for this, which a restored one
# cannot use because its gap has already happened. Anything unrecognised counts
# as transient and waits for the grace timer instead.
CHARGE_TERMINAL_STATUSES = frozenset({"NOCHARGING", "CHARGINGENDED", "CHARGINGERROR"})

# BMW's in-charge progress figures, which it simply stops sending when a charge
# ends instead of sending a zero: on the i5 the time to full stayed at 32 min and
# the power at 10.56 kW for good after a charge reached its target (2026-10-10).
# Zeroed when our own session closes on the car's word -- after the flap
# debounce, so a blip that comes straight back never zeroes a value BMW may not
# send again; and never on a restart's timeout, which can't know the charge ended.
CHARGE_PROGRESS_DESCRIPTORS = (
    "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged",
    "vehicle.drivetrain.electricEngine.charging.timeRemaining",
    "vehicle.powertrain.electric.battery.charging.power",
)

# A ``trip.segment.end.*`` field is only a completed-trip signal if its own
# timestamp is recent: BMW ships the "last trip end" fields (e.g. ``hvSoc``) in
# every telematic snapshot with the *previous* drive's timestamp, so an old one
# would otherwise close a live GPS trip the moment the car parks and polls.
SEG_FRESH_S = 900

# --- What the efficiency profile reads live --------------------------------
# The measured consumption comes out of the charging ledger, but two of its
# inputs arrive as ordinary stream messages: BMW's own remaining range (the
# comparison the real-range entity and the card's efficiency view are built
# around) and the pack capacity that consumption is divided into. Neither moves
# the SoC estimate nor lands a history record, so anything that recomputes the
# profile only on those two signals must also watch these -- otherwise it keeps
# whatever it worked out when it was created, which right after a restart is
# before either descriptor has arrived.
DESC_BMW_RANGE = "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange"
DESC_MAX_ENERGY = "vehicle.drivetrain.batteryManagement.maxEnergy"
EFFICIENCY_LIVE_DESCRIPTORS = (DESC_BMW_RANGE, DESC_MAX_ENERGY)

# --- What a charge controller is told (see bridge.py / evcc.py) ------------
# The charging status is already handled by name in the message paths above;
# named here because the bridge needs to know whether it has *ever* arrived,
# which is a different question from what it currently says.
DESC_CHARGE_STATUS = "vehicle.drivetrain.electricEngine.charging.status"
# Why a charge ended, most specific first (carried on the interrupted event).
CHARGE_END_REASON_DESCRIPTORS = (
    "vehicle.drivetrain.electricEngine.charging.reasonChargingEnd",
    "vehicle.drivetrain.electricEngine.charging.hvpmFinishReason",
)
# Whether a cable is in the car comes from a chain of descriptors that differs by
# model -- see ``evcc.PLUG_DESCRIPTORS``, where it is unit-tested.


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
    # The one state listener feeding the cars' phone routes (``phone_route.py``,
    # :meth:`watch_phone_routes`).
    _phone_unsub: Optional[Callable[[], None]] = field(default=None, init=False)
    # The trip detector's log and capture mode (``trip_capture``).
    trip_log: TripLog = field(default_factory=TripLog, init=False)
    # The device triggers (``vehicle_triggers``): situations, judged on what the
    # stream delivered since this instance started (``Vehicle.live_descriptors``).
    _situations: SituationTracker = field(default_factory=SituationTracker, init=False)
    # The alarm, preconditioning and Check Control as last known: what an edge
    # event compares against (``vehicle_triggers.EdgeTracker``).
    _edges: EdgeTracker = field(default_factory=EdgeTracker, init=False)

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
        soc_now = self._trip_soc_end(vin, builder.start)
        progress = builder.progress(
            dt_util.utcnow(),
            mileage_now=self.odometer_km(vin),
            soc_now=soc_now,
        )
        energy_kwh = self._trip_energy_kwh(vin, builder.soc_start, soc_now)
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
            self._note_odometer_report(vin, parked_soc=parked_soc)
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
                await self._process_trip_signals(
                    vin, now, motion_value, ignition_value, segment_fresh
                )
                if door_value is not None:
                    await self._process_door_signal(vin, now, door_value)
                if "vehicle.drivetrain.batteryManagement.header" in data or any(
                    descriptor in data for descriptor in DESC_ODOMETER
                ):
                    self._note_late_reading(vin, now)
                if gps_parts:
                    await self._process_gps_signal(vin, now, gps_ts, gps_parts)
            # After the trip logic: a trip opening ends "parked unlocked".
            self._evaluate_situations(vin, now)
        self._note_edges(vin, data, seen_descriptors, now, source)

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

    def _note_odometer_report(
        self, vin: str, *, parked_soc: Optional[tuple[float, Optional[datetime]]] = None
    ) -> None:
        """Arm the vehicle-report debounce when the odometer reached a new reading.

        Re-armed only by a *new* value: the heartbeat repeats of an unchanged
        reading must not hold the event back, and must never fire it either
        (``vehicle_report.py``).
        """

        if self.history is None:
            return
        odometer_km = self.odometer_km(vin)
        last = self.history.last_report(vin)
        decision = self._report_decision(vin, odometer_km)
        if decision.kind == SEED:
            self.history.async_set_last_report(
                vin, self._report_record(vin, odometer_km, starts=(last or {}).get("starts"))
            )
            if self.report_trips(vin):
                # Where the car stands now is where its first trip will start.
                self.hass.async_create_task(self._remember_report_place(vin, odometer_km))
            return
        if decision.kind == CANDIDATE:
            # Kept beside the record, never in it: see ``vehicle_report.py``.
            self.history.async_set_last_report(
                vin, {**(last or {}), "candidate": self._report_record(vin, odometer_km)}
            )
            return
        if decision.kind == ARM and parked_soc is not None and "soc_parked" not in (last or {}):
            # The first new reading of a drive: what the car stood with before
            # it. Kept in the record, so a restart's replay of the burst can't
            # overwrite it with the SoC the car arrived with.
            soc, soc_at = parked_soc
            self.history.async_set_last_report(
                vin,
                {
                    **(last or {}),
                    "soc_parked": soc,
                    "soc_parked_at": soc_at.isoformat() if soc_at else None,
                },
            )
        if decision.kind != ARM or self._peek(vin).report.pending == odometer_km:
            return
        self._cancel_report_timer(vin)
        self._car(vin).report.pending = odometer_km

        @callback
        def _fire(_now) -> None:
            self._peek(vin).report.take("timer")
            armed = self._peek(vin).report.take("pending")
            # A newer reading would have re-armed the timer; anything else is a
            # value that has gone away again, and is not announced.
            if armed is None or self.odometer_km(vin) != armed:
                return
            # Decided again, against the record as it is now: what the report
            # is measured from may have changed while it held.
            decision = self._report_decision(vin, armed)
            if decision.kind == ARM and decision.baseline is not None:
                self._fire_vehicle_report(vin, armed, decision.baseline)

        self._car(vin).report.timer = async_call_later(self.hass, REPORT_QUIET_S, _fire)

    def _report_decision(self, vin: str, odometer_km: Optional[float]) -> ReportDecision:
        return report_decision(
            odometer_km,
            self.history.last_report(vin) if self.history is not None else None,
            timestamp=self._odometer_timestamp(vin),
            now=datetime.now(timezone.utc),
        )

    def _report_record(
        self, vin: str, odometer_km: float, *, starts: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """A reading as the record keeps it: by the car's clock and by ours.

        With the fuel and charge that came with it, which the next report trip
        starts from, and any start marks still waiting for their trip. The
        place is added once resolved (:meth:`_remember_report_place`).
        """

        record: dict[str, Any] = {
            "odometer_km": odometer_km,
            "timestamp": self._odometer_timestamp(vin),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "fuel_l": self._numeric(vin, DESC_FUEL_L),
            "soc": self._current_soc(vin),
        }
        if starts:
            record["starts"] = list(starts)
        return record

    def pricing_for(self, vin: str) -> PricingConfig:
        """The pricing in force for ``vin``: its own meter and losses, if any."""

        return self.vehicle_pricing.get(vin, self.pricing)

    def trip_settings_for(self, vin: str) -> TripSettings:
        """The trip settings in force for ``vin``."""

        return self.vehicle_trip_settings.get(vin, self.trip_settings)

    def report_trips(self, vin: str) -> bool:
        """True when ``vin``'s trips are built from vehicle reports, not the live stream."""

        return self.trip_settings_for(vin).report_trips

    @callback
    def mark_trip_start(self, vin: str, at: datetime) -> None:
        """Note that a drive began at ``at`` (``bavariandata.mark_trip_start``).

        Kept with the report record, so a restart before the car reports loses
        nothing; the report that ends the drive spends it (``vehicle_report.py``).
        """

        if self.history is None:
            return
        self.history.async_set_last_report(vin, add_start_mark(self.history.last_report(vin), at))
        self.trip_log.cap("[trip.report] %s start marked at %s", vin, at.isoformat())

    def watch_phone_routes(self) -> None:
        """Listen to every phone and in-car sensor a car's trip settings name.

        Called whenever the trip settings are (re)read. Every car starts over,
        since its in-car sensor -- what decides which positions are held -- may
        be a different one now.
        """

        self.stop_watching_phone_routes()
        for car in self._cars.values():
            car.report.phone_route = None
        entities = sorted(
            {
                entity_id
                for settings in (self.trip_settings, *self.vehicle_trip_settings.values())
                if settings.phone_route
                for entity_id in (settings.phone_tracker, settings.phone_in_car)
                if entity_id
            }
        )
        if entities:
            self._phone_unsub = async_track_state_change_event(
                self.hass, entities, self._phone_state_changed
            )

    def stop_watching_phone_routes(self) -> None:
        if self._phone_unsub is not None:
            self._phone_unsub()
            self._phone_unsub = None

    def _phone_route(self, vin: str, settings: TripSettings, in_car_state: Any) -> PhoneRoute:
        route = self._peek(vin).report.phone_route
        if route is None:
            route = PhoneRoute(gated=bool(settings.phone_in_car))
            if settings.phone_in_car:
                if in_car_state is None:
                    in_car_state = self.hass.states.get(settings.phone_in_car)
                route.in_car = in_car_state is not None and in_car_state.state == "on"
            self._car(vin).report.phone_route = route
        return route

    @callback
    def _phone_state_changed(self, event: Any) -> None:
        """A phone moved, or got into or out of a car."""

        try:
            entity_id = event.data.get("entity_id")
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            now = datetime.now(timezone.utc)
            for vin in sorted({*self.data, *self.names, *self.vehicle_trip_settings}):
                settings = self.trip_settings_for(vin)
                if not settings.phone_route:
                    continue
                if entity_id == settings.phone_in_car:
                    # Created from the state *before* this change, so a car
                    # first heard of now still sees the sensor turn on.
                    route = self._phone_route(vin, settings, event.data.get("old_state"))
                    if route.set_in_car(new_state.state == "on"):
                        self.mark_trip_start(vin, now)
                        # The phone may not move again for a while: where it
                        # is now is where the drive starts.
                        tracker = self.hass.states.get(settings.phone_tracker)
                        if tracker is not None:
                            self._add_phone_position(vin, route, tracker, now)
                elif entity_id == settings.phone_tracker:
                    self._add_phone_position(
                        vin, self._phone_route(vin, settings, None), new_state, now
                    )
        except Exception:  # noqa: BLE001 - a phone must not break the stream
            _LOGGER.exception("Could not follow the phone's position")

    def _add_phone_position(self, vin: str, route: PhoneRoute, state: Any, now: datetime) -> None:
        attributes = getattr(state, "attributes", None) or {}
        try:
            latitude = float(attributes["latitude"])
            longitude = float(attributes["longitude"])
        except KeyError, TypeError, ValueError:
            return
        accuracy = attributes.get("gps_accuracy")
        try:
            accuracy = float(accuracy) if accuracy is not None else None
        except TypeError, ValueError:
            accuracy = None
        if route.add(now, latitude, longitude, accuracy):
            self.trip_log.cap(
                "[trip.phone] %s %.5f,%.5f acc=%s", vin, latitude, longitude, accuracy
            )

    async def _remember_report_place(self, vin: str, odometer_km: float) -> None:
        """Add where the car stands to the record of the reading it just gave.

        The next report trip starts there. Written only while the record is still
        that reading's: a newer one may have landed during the geocode.
        """

        try:
            resolved = await self._resolve_place(vin)
            record = self.history.last_report(vin) if self.history is not None else None
            if resolved is None or record is None or record.get("odometer_km") != odometer_km:
                return
            self.history.async_set_last_report(vin, {**record, "place": resolved})
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not place the vehicle report for %s", mask_vin(vin))

    def _cancel_report_timer(self, vin: str) -> None:
        cancel = self._peek(vin).report.take("timer")
        if cancel is not None:
            cancel()
        self._peek(vin).report.take("pending")

    def _fire_vehicle_report(
        self, vin: str, odometer_km: float, baseline: Mapping[str, Any]
    ) -> None:
        if self.history is None:
            return
        timestamp = self._odometer_timestamp(vin)
        record = self.history.last_report(vin) or {}
        starts = record.get("starts")
        parked = {key: record.get(key) for key in ("soc_parked", "soc_parked_at")}
        start: Optional[datetime] = None
        end_at = dt_util.parse_datetime(timestamp) if timestamp else None
        if end_at is None:
            end_at = datetime.now(timezone.utc)
        if self.report_trips(vin):
            start, starts = take_start_mark(
                starts, previous_at=report_time(baseline), end_at=end_at
            )
        payload = report_payload(
            vin,
            odometer_km=odometer_km,
            timestamp=timestamp,
            last=baseline,
            values={
                "fuel_l": self._numeric(vin, DESC_FUEL_L),
                "fuel_percent": self._numeric(vin, "vehicle.drivetrain.fuelSystem.level"),
                "range_km": self._numeric(vin, "vehicle.drivetrain.lastRemainingRange"),
                "soc_percent": self._numeric(vin, "vehicle.drivetrain.batteryManagement.header"),
                "latitude": self._numeric(vin, DESC_GPS_LAT),
                "longitude": self._numeric(vin, DESC_GPS_LON),
                "altitude_m": self._numeric(
                    vin, "vehicle.cabin.infotainment.navigation.currentLocation.altitude"
                ),
                "heading": self._numeric(vin, DESC_GPS_HEADING),
            },
        )
        payload["entry_id"] = self.entry_id
        # Recorded before firing: an automation that raises must not leave the
        # reading unrecorded, to be announced a second time. A fresh record
        # also drops any candidate: this reading has settled the question.
        self.history.async_set_last_report(
            vin, self._report_record(vin, odometer_km, starts=starts)
        )
        self._fire(EVENT_VEHICLE_REPORT, payload)
        if self.report_trips(vin):
            self.hass.async_create_task(
                self._record_report_trip(vin, odometer_km, {**baseline, **parked}, start, end_at)
            )

    async def _record_report_trip(
        self,
        vin: str,
        odometer_km: float,
        baseline: Mapping[str, Any],
        start: Optional[datetime],
        end_at: datetime,
    ) -> None:
        """One trip from a vehicle report: from the previous report to this one.

        Where it went and how far are the car's own: the place it stood at the
        previous report, the place it stands now, the odometer between them.
        When it set off is a start mark's, or unknown -- then the trip is stored
        at its arrival with no duration (``Trip.duration_unknown``).
        """

        try:
            end_place = await self._resolve_place(vin)
            record = self.history.last_report(vin) if self.history is not None else None
            if end_place is not None and record and record.get("odometer_km") == odometer_km:
                self.history.async_set_last_report(vin, {**record, "place": end_place})
            start_place = baseline.get("place")
            if not isinstance(start_place, Mapping):
                start_place = None
            # The SoC just before the burst; the previous report's own reading
            # only when there is none (a car charged in between left with more).
            soc_start = baseline.get("soc_parked")
            if isinstance(soc_start, bool) or not isinstance(soc_start, (int, float)):
                soc_start = baseline.get("soc")
            if isinstance(soc_start, bool) or not isinstance(soc_start, (int, float)):
                soc_start = None
            mileage_start = baseline.get("odometer_km")
            if isinstance(mileage_start, bool) or not isinstance(mileage_start, (int, float)):
                mileage_start = None
            builder = TripBuilder(
                vin,
                start or end_at,
                start_place=dict(start_place) if start_place else None,
                soc_start=soc_start,
                mileage_start=mileage_start,
                location_assumed=start_place is None or start_place.get("label") == "Unknown",
            )
            soc_end = self._current_soc(vin)
            if self.is_plug_in_hybrid(vin):
                # As on a live trip (``_trip_soc_end``): a hybrid that sent no
                # SoC with its burst still shows the one it set off with, and
                # "38 -> 38 %" would claim the battery never moved.
                tracking = self._peek(vin).charge.tracking
                before = dt_util.parse_datetime(baseline.get("soc_parked_at") or "")
                last = tracking.last_update if tracking is not None else None
                if last is None or (before is not None and last <= before):
                    soc_end = None
            route = self._phone_report_route(vin, start, end_at)
            if route:
                builder.record_track = True
                for at, latitude, longitude in route:
                    builder.add_track_point(latitude, longitude, at)
            trip = builder.close(
                end_at,
                end_place=end_place,
                soc_end=soc_end,
                mileage_end=odometer_km,
                energy_kwh=self._trip_energy_kwh(vin, soc_start, soc_end),
                stats=fuel_stats(baseline.get("fuel_l"), self._numeric(vin, DESC_FUEL_L)),
                hybrid=self.is_plug_in_hybrid(vin),
            )
            trip.duration_unknown = start is None
            if route:
                trip.track_source = "phone"
            self.trip_log.cap(
                "[trip.report] %s %s -> %s dist=%s start=%s phone_points=%s",
                vin,
                (trip.start_place or {}).get("label"),
                (trip.end_place or {}).get("label"),
                trip.distance_km,
                "marked" if start is not None else "unknown",
                len(route),
            )
            self._store_trip(vin, trip, start_zone=None, left_fired=False, announce=True)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not record the report trip for %s", mask_vin(vin))

    def _phone_report_route(
        self, vin: str, start: Optional[datetime], end_at: datetime
    ) -> list[tuple[datetime, float, float]]:
        """The phone's route for a report trip, ending where the car parked.

        Empty when the car takes no route from a phone, or the phone's does not
        fit the trip (``phone_route.py``).
        """

        route = self._peek(vin).report.phone_route
        if route is None or not self.trip_settings_for(vin).phone_route:
            return []
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        end_fix = (latitude, longitude) if latitude is not None and longitude is not None else None
        points = route.take(start, end_at, end_fix)
        if not points or end_fix is None:
            return []
        # The car's own fix closes the line: it is where the trip ends.
        return [*points, (end_at, *end_fix)]

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

    def _evaluate_situations(self, vin: str, now: datetime) -> None:
        """Start or end the trigger situations, firing ``EVENT_SITUATION`` on a change.

        Judged on live values only (see ``Vehicle.live_descriptors``), so a restart can
        start nothing until the car speaks again.
        """

        try:
            tracking = self._peek(vin).charge.tracking
            charging: Optional[bool] = None
            if tracking is not None and DESC_CHARGE_STATUS in self._peek(vin).live_descriptors:
                charging = tracking.charging_active
            soc = self._current_soc(vin)
            target = None if tracking is None else tracking.target_soc_percent
            windows = self._live_values(vin, WINDOW_DESCRIPTORS)
            states = {
                SITUATION_PARKED_UNLOCKED: parked_unlocked(
                    lock_state(self._live_values(vin, LOCK_DESCRIPTORS)),
                    self._peek(vin).trip.builder is not None,
                ),
                SITUATION_PLUGGED_NOT_CHARGING: plugged_not_charging(
                    plug_state(self._live_values(vin, PLUG_DESCRIPTORS)), charging, soc, target
                ),
                SITUATION_PARKED_WINDOW_OPEN: parked_window_open(
                    window_open(windows),
                    self._peek(vin).trip.builder is not None,
                ),
            }
            for situation, active in states.items():
                began = self._situations.since(vin, situation)
                change = self._situations.update(vin, situation, active, now)
                if change is None:
                    continue
                zone = self._current_zone(vin)
                payload: Dict[str, Any] = {
                    "vin": vin,
                    "entry_id": self.entry_id,
                    "situation": situation,
                    "active": change,
                    "since": (now if change else began or now).isoformat(),
                    "zone": None if zone is None else zone[0],
                    "zone_entity_id": None if zone is None else zone[1],
                }
                if situation == SITUATION_PLUGGED_NOT_CHARGING:
                    payload["soc"] = None if soc is None else round(soc, 1)
                    payload["target_soc"] = target
                elif situation == SITUATION_PARKED_WINDOW_OPEN:
                    payload["windows"] = open_windows(windows) if change else []
                if debug_enabled():
                    _LOGGER.debug(
                        "[trigger] %s %s %s", vin, situation, "BEGIN" if change else "END"
                    )
                self._fire(EVENT_SITUATION, payload)
        except Exception:  # noqa: BLE001 - a trigger must never break the stream
            _LOGGER.exception("Trigger evaluation failed for %s", mask_vin(vin))

    def _note_edges(
        self,
        vin: str,
        data: Mapping[str, Any],
        seen: Iterable[str],
        now: datetime,
        source: str,
    ) -> None:
        """Fire the alarm, preconditioning and Check Control events on a change.

        The alarm and preconditioning move only on the stream, and only fire on
        a value stamped fresh: a REST answer may be BMW's cache, and fills a gap
        at most (``EdgeTracker``). Check Control is REST-only, so its lists are
        compared whatever brought them; the first list only sets the start.
        """

        try:
            seen = set(seen)
            for descriptor in (ALARM_DESCRIPTOR, PRECONDITIONING_DESCRIPTOR):
                if descriptor not in seen:
                    continue
                reading = self._edge_reading(descriptor, self._value(vin, descriptor))
                if reading is None:
                    continue
                if source == REST:
                    self._edges.seed(vin, descriptor, reading)
                    continue
                before = self._edges.move(vin, descriptor, reading)
                stamp = (data.get(descriptor) or {}).get("timestamp")
                if before is reading or not fresh(dt_util.parse_datetime(stamp or ""), now):
                    continue
                if not reading and before is None:
                    # An end with no start we knew of is nothing to announce.
                    continue
                if descriptor == ALARM_DESCRIPTOR:
                    self._fire_edge(EVENT_ALARM, vin, reading, {})
                else:
                    error = self._value(vin, PRECONDITIONING_ERROR_DESCRIPTOR)
                    activity = self._value(vin, PRECONDITIONING_DESCRIPTOR)
                    self._fire_edge(
                        EVENT_PRECONDITIONING,
                        vin,
                        reading,
                        {
                            "activity": activity.lower() if isinstance(activity, str) else None,
                            "error": error.lower() if isinstance(error, str) else None,
                            "soc": self._current_soc(vin),
                        },
                    )
            if CHECK_CONTROL_DESCRIPTOR in seen:
                _count, items = structured_state(self._value(vin, CHECK_CONTROL_DESCRIPTOR))
                if isinstance(items, list):
                    before = self._edges.move(vin, CHECK_CONTROL_DESCRIPTOR, items)
                    new, gone = message_changes(before, items) or ([], [])
                    for active, message in [(True, m) for m in new] + [(False, m) for m in gone]:
                        self._fire_edge(
                            EVENT_CHECK_CONTROL,
                            vin,
                            active,
                            {
                                "id": message.get("id"),
                                "title": message.get("title"),
                                "text": message.get("text"),
                                "message_type": message.get("messageType"),
                                "message": message,
                            },
                        )
        except Exception:  # noqa: BLE001 - a trigger must never break the stream
            _LOGGER.exception("Edge trigger evaluation failed for %s", mask_vin(vin))

    @staticmethod
    def _edge_reading(descriptor: str, value: Any) -> Optional[bool]:
        if descriptor == ALARM_DESCRIPTOR:
            return alarm_on(value)
        return preconditioning_active(value)

    def _value(self, vin: str, descriptor: str) -> Any:
        state = self.get_state(vin, descriptor)
        return None if state is None else state.value

    def _seed_edge(self, vin: str, descriptor: str, value: Any) -> None:
        """Give an edge its "before" from a restored value (``restore_descriptor_state``)."""

        if descriptor in (ALARM_DESCRIPTOR, PRECONDITIONING_DESCRIPTOR):
            self._edges.seed(vin, descriptor, self._edge_reading(descriptor, value))
        elif descriptor == CHECK_CONTROL_DESCRIPTOR:
            _count, items = structured_state(value)
            if isinstance(items, list):
                self._edges.seed(vin, descriptor, items)

    def _fire_edge(self, event_type: str, vin: str, active: bool, extra: Dict[str, Any]) -> None:
        zone = self._current_zone(vin)
        if debug_enabled():
            _LOGGER.debug("[trigger] %s %s %s", vin, event_type, "BEGIN" if active else "END")
        self._fire(
            event_type,
            {
                "vin": vin,
                "entry_id": self.entry_id,
                "active": active,
                **extra,
                "zone": None if zone is None else zone[0],
                "zone_entity_id": None if zone is None else zone[1],
            },
        )

    def _fire_zone_left(self, vin: str, zone: tuple[str, str]) -> None:
        self._car(vin).trip.zone_left_fired = True
        builder = self._peek(vin).trip.builder
        self._fire(
            EVENT_ZONE_LEFT,
            {
                "vin": vin,
                "entry_id": self.entry_id,
                "zone": zone[0],
                "zone_entity_id": zone[1],
                "trip_start": None if builder is None else builder.start.isoformat(),
                "soc": self._current_soc(vin),
            },
        )

    def _check_zone_left(self, vin: str, latitude: float, longitude: float) -> None:
        """Fire "left" once per trip, on the first fix outside the start zone."""

        start = self._peek(vin).trip.start_zone
        if start is None or self._peek(vin).trip.zone_left_fired:
            return
        here = self._zone_at(latitude, longitude)
        if here is not None and here[1] == start[1]:
            return
        self._fire_zone_left(vin, start)

    # --- trips (roadmap Phase 3) -------------------------------------------

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
            open_trip = self._peek(vin).trip.builder is not None
            # Which of these BMW actually streams (and in what order) is the one
            # thing no unit test here can answer, so record every observation
            # under the opt-in debug flag. Tag it so a drive can be grepped out
            # of an otherwise very chatty debug log.
            if motion is not None or ignition is not None or segment_fresh:
                self.trip_log.cap(
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
                # No fix was processed in this batch, so the last one on record is
                # still the last time the car was seen parked.
                await self._open_trip(vin, now, parked_until=self._peek(vin).gps.last_fix_at)
            if motion is True:
                # Moving again: cancel any pending stationary-close.
                self._drop_door_arrival_on_move(vin, now)
                self._peek(vin).trip.take("held_since")
                self._cancel_trip_close_timer(vin)
            elif motion is False:
                # An explicit "not moving" is stop evidence in its own right, so a
                # car that streams it never has its close held (see
                # ``_hold_close_on_silence``).
                self._car(vin).trip.last_stop_evidence = now
                if open_trip:
                    self._arm_trip_close_timer(vin)
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Trip detection failed for %s", mask_vin(vin))

    async def _process_door_signal(self, vin: str, now: datetime, door_open: bool) -> None:
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
            previous = self._peek(vin).trip.driver_door_open
            self._car(vin).trip.driver_door_open = door_open
            if previous is None or previous == door_open:
                return  # first reading or no transition
            open_trip = self._peek(vin).trip.builder is not None

            if door_open and open_trip:
                # Opened while a trip is open: an arrival, but only once the car
                # has actually stopped (guards a flickery read mid-move).
                last_move = self._peek(vin).gps.last_move
                stopped = self._stopped_for_door(vin, now)
                # Seen standing: a fix since the last movement showed it, or the
                # door already opened on arrival and the car never moved on.
                evidence = self._peek(vin).trip.last_stop_evidence
                seen_standing = (
                    last_move is None
                    or (evidence is not None and evidence >= last_move)
                    or self._peek(vin).trip.door_arrival is not None
                )
                if stopped and seen_standing:
                    self.trip_log.cap(
                        "[trip.door] %s driver door opened while stopped -> close",
                        vin,
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="door")
                elif stopped:
                    # Quiet, but not seen standing: on a car that streams in
                    # snapshots the final fix comes right behind the door, the last
                    # few minutes of the drive in it (issue #183). Let the snapshot
                    # land, then close; the door stays the arrival.
                    trip = self._car(vin).trip
                    if trip.door_arrival is None:
                        trip.door_arrival = now
                    self._cancel_trip_close_timer(vin)
                    self._arm_trip_close_timer(vin, delay_s=DOOR_SETTLE_S)
                    self.trip_log.cap(
                        "[trip.door] %s driver door opened -> close in %ds",
                        vin,
                        DOOR_SETTLE_S,
                        substrate=True,
                    )
                else:
                    # Too soon to close on: it arrives right behind the final fix.
                    # Remember it -- if the car moves on it is dropped, otherwise
                    # it dates the arrival and says the car stopped.
                    trip = self._car(vin).trip
                    if trip.door_arrival is None:
                        trip.door_arrival = now
            elif not door_open and not open_trip:
                # Closed with no trip open: the driver just got in. Mark the start
                # here so the next movement seeds the trip from this spot.
                self._car(vin).trip.pending_start = (now, self._peek(vin).gps.last_position)
                self.trip_log.cap(
                    "[trip.door] %s driver door closed -> start marked at %s",
                    vin,
                    self._peek(vin).gps.last_position,
                    substrate=True,
                )
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Door trip detection failed for %s", mask_vin(vin))

    def _seed_gps_from_restore(self, vin: str) -> None:
        """Start GPS tracking from where the car stood when we last stopped.

        Without it, the first fix after a restart is only a starting point: a
        car that set off meanwhile has its trip opened one fix late, from that
        already-moved-on fix -- so a drive out of the Home zone no longer starts
        at Home, and commute classification loses it. Only a fresh tracker is
        seeded; a live fix always wins over a restored one.
        """

        if self._peek(vin).gps.tracker is not None:
            return
        try:
            latitude = float(self.get_state(vin, DESC_GPS_LAT).value)
            longitude = float(self.get_state(vin, DESC_GPS_LON).value)
        except AttributeError, TypeError, ValueError:
            return  # the other half has not been restored yet
        tracker = self._car(vin).gps.tracker = GpsTracker()
        tracker.last_lat, tracker.last_lon = latitude, longitude
        if self._car(vin).gps.last_position is None:
            self._car(vin).gps.last_position = (latitude, longitude)

    def _gps_fix_ready(self, vin: str, now: datetime, parts: set[str]) -> bool:
        """True when a complete lat+lon fix is ready to process.

        BMW streams the two coordinates as separate messages, so a position is
        only settled once *both* have arrived; acting on the first would plot a
        phantom right-angle point (fresh component + the previous fix's mate) and
        double the step distance. ``parts`` is which halves this batch carried, so
        pairing is decided by *arrival* -- the one rule that cannot drift out of
        step. Comparing each component's own timestamp against its own previous
        value cannot: once a single unpaired message slips through (the first one
        after connect does, with nothing to compare against), the stale half is
        always "newer than last time" too, and every later fix pairs one message
        behind for good -- which is exactly the L-shaped track BMW's own data
        never contained.

        BMW redelivers each message, so the same fix completes its pair more than
        once; the fix's own timestamp (identical on both halves) drops the repeat.
        A half that never gets its mate within ``GPS_PAIR_STALE_S`` is processed
        anyway, so a broken pairing assumption degrades rather than stalling
        detection outright.
        """

        gps = self._car(vin).gps
        if gps.pending_parts is None:
            gps.pending_parts = set()
        pending = gps.pending_parts
        pending |= parts
        if not GPS_PAIR_PARTS <= pending:
            since = self._peek(vin).gps.pending_since
            if since is None:
                self._car(vin).gps.pending_since = now
                return False
            if (now - since).total_seconds() < GPS_PAIR_STALE_S:
                return False  # half a fix; wait for the mate
        self._peek(vin).gps.take("pending_parts")
        self._peek(vin).gps.take("pending_since")

        fix_ts = self._gps_fix_timestamp(vin)
        if fix_ts is not None:
            last = self._peek(vin).gps.last_fix_ts
            if last is not None and fix_ts <= last:
                return False  # BMW re-sent a fix already processed
            self._car(vin).gps.last_fix_ts = fix_ts
        return True

    def _gps_fix_timestamp(self, vin: str) -> Optional[datetime]:
        """BMW's own time for the current fix, or None when unstamped.

        Both halves of a fix carry the same timestamp, so the newer of the two is
        that fix's time whichever message completed the pair.
        """

        stamps = []
        for descriptor in (DESC_GPS_LAT, DESC_GPS_LON):
            state = self.get_state(vin, descriptor)
            if state is None or not state.timestamp:
                continue
            parsed = dt_util.parse_datetime(state.timestamp)
            if parsed is not None:
                stamps.append(parsed)
        return max(stamps) if stamps else None

    async def _process_gps_signal(
        self,
        vin: str,
        now: datetime,
        fix_ts: Optional[datetime] = None,
        parts: Optional[set[str]] = None,
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
        ``parts`` is which halves of the fix this batch carried (see
        ``_gps_fix_ready``); a caller that can't say assumes a complete pair.
        """

        if self.history is None:
            return
        # BMW sends latitude and longitude as two separate messages ~1 s apart.
        # Acting on each pairs a fresh component with a stale one, so the first
        # message plots a phantom right-angle point (and doubles the distance)
        # that the second never gets to correct. Wait until *both* halves of the
        # fix have arrived, so the tracker only ever sees a settled position --
        # unless a half has frozen long enough to risk a stall. The half is
        # registered *before* asking whether both coordinates are known: on a
        # fresh install the very first latitude has no longitude yet, and
        # dropping it unregistered left every later fix paired one behind.
        if not self._gps_fix_ready(vin, now, set(parts or GPS_PAIR_PARTS)):
            return
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return
        try:
            tracker = self._peek(vin).gps.tracker
            if tracker is None:
                tracker = self._car(vin).gps.tracker = GpsTracker()
            step_km = tracker.step(latitude, longitude)
            moving = is_gps_movement(step_km)
            open_trip = self._peek(vin).trip.builder is not None

            # Inter-fix gap and stream latency -- the two numbers that explain a
            # mid-drive split (a gap over the debounce) or a laggy close.
            prev_fix = self._peek(vin).gps.last_fix_at
            gap_s = (now - prev_fix).total_seconds() if prev_fix is not None else None
            self._car(vin).gps.last_fix_at = now

            if moving:
                if (
                    open_trip
                    and self._peek(vin).trip.held_since is not None
                    and silence_implies_stop(gap_s, step_km, min_gap_s=TRIP_CLOSE_DEBOUNCE_S)
                ):
                    # The fixes are back, but barely anywhere: the car stood
                    # through the silence (a garage that swallows the fix), so the
                    # held close was right after all. Close it *before* stamping
                    # this movement, or the backdated end would land on this
                    # departure instead of the real arrival -- then let the fix
                    # below open the new trip.
                    self._peek(vin).trip.take("held_since")
                    self._cancel_trip_close_timer(vin)
                    self.trip_log.cap(
                        "[trip.timer] %s held close CONFIRMED by distance (%dm over %ds silence)",
                        vin,
                        round(step_km * 1000),
                        int(gap_s or 0),
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="silent")
                    open_trip = False
                self._drop_door_arrival_on_move(vin, now)
                self._car(vin).gps.last_move = now
                if not open_trip:
                    # Seed the track from where the car actually started (the
                    # parked position / door-close point), not this already
                    # moved-on fix -- done inside _open_trip before we overwrite
                    # the last GPS position below. ``prev_fix`` is the last fix that
                    # still showed the car parked, which bounds how far the start
                    # may be backdated (see _trip_start_seed).
                    await self._open_trip(vin, now, parked_until=prev_fix)
                    open_trip = True
                builder = self._peek(vin).trip.builder
                if builder is not None:
                    builder.add_gps_km(step_km)
                    # Record the route point too, stamped with this fix's time
                    # (opt-in; a no-op otherwise).
                    builder.add_track_point(latitude, longitude, now)
                    self._check_zone_left(vin, latitude, longitude)
                # The fixes are back and the car has moved on: the drive never
                # ended, so drop any hold and keep going as one trip.
                self._peek(vin).trip.take("held_since")
                # Rolling window: keep the trip alive while the car is moving,
                # but guarantee a close even if the fixes stop arriving.
                self._reset_trip_close_timer(vin)
            else:
                # A fix that arrived showing no movement is the one thing that
                # positively says the *car* stopped rather than the stream.
                self._car(vin).trip.last_stop_evidence = now
                if open_trip and self._peek(vin).trip.held_since is not None:
                    # A held close, now confirmed: the stream went quiet while the
                    # car was already parked. Close on the spot -- the end is
                    # backdated to the last movement, so nothing is lost.
                    self._peek(vin).trip.take("held_since")
                    self._cancel_trip_close_timer(vin)
                    self.trip_log.cap(
                        "[trip.timer] %s held close CONFIRMED by a stationary fix",
                        vin,
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="silent")
                    open_trip = False
                elif (
                    open_trip
                    and self._peek(vin).trip.door_arrival is not None
                    and self._stopped_for_door(vin, now)
                ):
                    # The door opened and the car is seen standing: arrived. Not
                    # within seconds of the last move, though: a door opened at a
                    # car park barrier is followed by the car driving on.
                    self.trip_log.cap(
                        "[trip.door] %s standing after the door opened -> close",
                        vin,
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="door")
                    open_trip = False
                elif open_trip:
                    # Stationary fix: start the countdown but don't keep resetting
                    # it, or a car that streams its parked position never closes.
                    self._arm_trip_close_timer(vin)
                if not open_trip:
                    self._settle_late_reading(vin, moved=False)
                    self._car(vin).trip.parked_reading = (
                        self.odometer_km(vin),
                        self._current_soc(vin),
                    )

            # Remember where the car is now, for the next trip's start seed.
            self._car(vin).gps.last_position = (latitude, longitude)
            self._note_capture_fix(vin, now, step_km, moving, gap_s)
            if self.trip_debug:
                self._log_gps_fix(
                    vin,
                    now,
                    latitude,
                    longitude,
                    step_km,
                    moving,
                    gap_s,
                    fix_ts,
                    open_trip,
                )
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("GPS trip detection failed for %s", mask_vin(vin))

    def _note_capture_fix(
        self,
        vin: str,
        now: datetime,
        step_km: float,
        moving: bool,
        gap_s: Optional[float],
    ) -> None:
        """Fold one fix into the open trip's capture stats (for [trip.post])."""

        stats = self._peek(vin).trip.capture
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

        last_move = self._peek(vin).gps.last_move
        since_move = (now - last_move).total_seconds() if last_move is not None else None
        due = self._peek(vin).trip.close_due
        timer = "none" if due is None else f"armed({int((due - now).total_seconds())}s)"
        latency = (now - fix_ts).total_seconds() if fix_ts is not None else None
        builder = self._peek(vin).trip.builder
        # GPS-quality context: distinguishes "the car stopped" from "the fix was
        # lost" when a run of fixes shows no movement.
        fix_state = self._raw_value(vin, DESC_GPS_FIX)
        sats = self._raw_value(vin, DESC_GPS_SATS)
        heading = self._raw_value(vin, DESC_GPS_HEADING)
        self.trip_log.cap(
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
            self.odometer_km(vin),
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

        A trip held open through stream silence counts as still moving too: the
        whole point of the hold is that we don't yet know the car stopped, and a
        segment batch is no help deciding -- the i5 emits them mid-drive.
        """

        if self._peek(vin).trip.held_since is not None:
            return True
        last = self._peek(vin).gps.last_move
        if last is None:
            return False
        return (now - last).total_seconds() < TRIP_CLOSE_DEBOUNCE_S

    def _trip_start_seed(
        self, vin: str, now: datetime, parked_until: Optional[datetime] = None
    ) -> tuple[Optional[tuple[float, float]], datetime]:
        """Where *and when* the trip actually began, not where we noticed it.

        Position prefers a recent driver-door-close spot (the driver got in
        there), then the last known parked position, so the route connects from
        where the car was rather than the already-moved-on fix that triggered
        detection. Falls back to the current fix when neither is available.

        The time matters just as much: detection can lag the real departure by
        minutes when the stream goes quiet mid-pull-away, and stamping the trip
        with that lag misreports both its start and its duration. A recent
        door-close is the earliest the drive can have begun, so it backdates the
        start -- but only as far as ``parked_until``, the last fix that still
        showed the car parked, because sitting in the car before pulling away is
        not driving. Without a door-close there is nothing trustworthy to
        backdate to (a car that stops streaming while parked would drag the start
        back hours), so detection time stands.
        """

        started_at = now
        marked_pos: Optional[tuple[float, float]] = None
        pending = self._peek(vin).trip.take("pending_start")
        if pending is not None:
            marked_at, pos = pending
            if (now - marked_at).total_seconds() <= PENDING_START_MAX_S:
                marked_pos = pos
                started_at = marked_at
                if parked_until is not None and parked_until > started_at:
                    started_at = min(parked_until, now)
        if marked_pos:
            return marked_pos, started_at
        parked = self._peek(vin).gps.last_position
        if parked is not None:
            return parked, started_at
        latitude = self._coordinate(vin, "latitude")
        longitude = self._coordinate(vin, "longitude")
        if latitude is not None and longitude is not None:
            return (latitude, longitude), started_at
        return None, started_at

    def _trip_start_readings(self, vin: str) -> tuple[Optional[float], Optional[float]]:
        """The odometer and SoC a new trip starts from: the parked car's.

        The live readings are already the moving car's on a car that streams in
        snapshots: the i4 sends its odometer and SoC ahead of the fix that first
        shows it moving, three minutes of driving on (issue #183). An odometer
        only grows by driving, so the parked one wins whenever it is not ahead.
        The SoC is kept only where it is higher: a lower parked SoC means the car
        charged since, and that charge is not the trip's.
        """

        odometer, soc = self.odometer_km(vin), self._current_soc(vin)
        parked = self._peek(vin).trip.take("parked_reading")
        if parked is None:
            return odometer, soc
        parked_odometer, parked_soc = parked
        if parked_odometer is not None and (odometer is None or parked_odometer <= odometer):
            odometer = parked_odometer
        if parked_soc is not None and (soc is None or parked_soc > soc):
            soc = parked_soc
        return odometer, soc

    async def _open_trip(
        self, vin: str, now: datetime, *, parked_until: Optional[datetime]
    ) -> None:
        """Start a trip, dated when the drive began rather than when we noticed.

        ``parked_until`` is the wall-clock of the last fix that still showed the
        car parked -- the last moment it is known not to have been driving. It
        bounds how far the start may be backdated (see :meth:`_trip_start_seed`);
        ``None`` means there is no such bound, not "use the current fix", so the
        caller must decide rather than let a just-updated fix time stand in.
        """

        self._peek(vin).trip.take("held_since")
        self._peek(vin).trip.take("door_arrival")
        # Moving again: a reading still pending after the last close is this drive's.
        self._settle_late_reading(vin, moved=True)
        seed, started_at = self._trip_start_seed(vin, now, parked_until)
        # Resolve the start place from the seed (the parked/door-close spot), not
        # the ~hundreds-of-metres-later fix that first registered as movement.
        start_place = await self._resolve_place(
            vin, seed[0] if seed else None, seed[1] if seed else None
        )
        mileage_start, soc_start = self._trip_start_readings(vin)
        builder = self._car(vin).trip.builder = TripBuilder(
            vin,
            started_at,
            start_place=start_place,
            soc_start=soc_start,
            mileage_start=mileage_start,
            location_assumed=start_place is None or start_place.get("label") == "Unknown",
            record_track=self.trip_settings_for(vin).record_track,
        )
        # Seed the route from where the drive actually started (see
        # _trip_start_seed), stamped t=0 -- the seed is that moment's position, so
        # it carries the start's own time, not detection time. A no-op when route
        # recording is off or no position is available.
        if seed is not None:
            builder.add_track_point(seed[0], seed[1], started_at)
        # Where "left a zone" is measured from: the parked spot, not the first
        # fix that registered as movement (already on its way out).
        self._car(vin).trip.zone_left_fired = False
        self._peek(vin).trip.take("start_zone")
        if seed is not None:
            start_zone = self._zone_at(seed[0], seed[1])
            if start_zone is not None:
                self._car(vin).trip.start_zone = start_zone
        # Start the per-trip capture stats (a no-op cost when capture is off, but
        # cheap and it means _note_capture_fix needn't check the mode).
        self._car(vin).trip.capture = {
            "fixes": 0,
            "moves": 0,
            "holds": 0,
            "max_gap": 0.0,
            "odo_start": builder.mileage_start,
        }
        self.trip_log.cap(
            "[trip] %s OPEN at %s (detected %s) place=%s soc=%s odo=%s",
            vin,
            started_at.isoformat(),
            now.isoformat(),
            (start_place or {}).get("label"),
            builder.soc_start,
            builder.mileage_start,
        )
        async_dispatcher_send(self.hass, self.signal_trip_active, vin)

    def _trip_end_time(self, vin: str, now: datetime, start: datetime, reason: str) -> datetime:
        """When the drive ended, as opposed to when the close was decided.

        A stationary close fires a full ``TRIP_CLOSE_DEBOUNCE_S`` *after* the last
        movement, and a segment close is gated on the car having already stopped,
        so ``now`` overstates the arrival by up to five minutes in both cases --
        inflating the trip's duration and everything derived from it. The last fix
        that showed movement is the arrival, clamped into the trip's own span so a
        stale reading can never end a trip before it began.

        A driver-door close is exempt: the door opening *is* the arrival, observed
        directly and usually within seconds of it, which beats a movement fix that
        may be minutes old on a sparse stream. So is an unload, which is simply
        "whatever we have, now".

        A ``silent`` close -- one that was held while the stream was quiet and then
        settled -- backdates as well, and matters more than the others: the hold can
        run for half an hour, so ``now`` would be wildly wrong.

        A door that opened too soon to close on (see ``_process_door_signal``) and
        was never followed by the car moving on is the arrival for any close,
        including the door close that comes when the driver gets back in to leave.
        """

        arrival = self._peek(vin).trip.door_arrival
        last_move = self._peek(vin).gps.last_move
        if arrival is not None and reason != "unload":
            # The final fix can land a moment after the door; take the later.
            ended = arrival if last_move is None else max(arrival, last_move)
            return max(start, min(ended, now))
        if reason not in ("stationary", "segment", "silent"):
            return now
        if last_move is None:
            return now
        return max(start, min(last_move, now))

    async def _close_trip(
        self, vin: str, now: datetime, *, reason: str = "stationary", announce: bool = True
    ) -> None:
        """Close the open trip. ``announce=False`` (the stop flush) fires no events."""

        builder = self._peek(vin).trip.take("builder")
        if builder is not None:
            # Where the next trip starts, should it open before a parked fix.
            self._car(vin).trip.parked_reading = (self.odometer_km(vin), self._current_soc(vin))
        cap_stats = self._peek(vin).trip.take("capture")
        start_zone = self._peek(vin).trip.take("start_zone")
        left_fired = self._peek(vin).trip.zone_left_fired
        self._car(vin).trip.zone_left_fired = False
        self._cancel_trip_close_timer(vin)
        self._peek(vin).trip.take("close_due")
        self._peek(vin).trip.take("held_since")
        if builder is not None:
            # The drive is over whatever happens to the record below -- including
            # the no-history and dropped-as-noise paths, which never reach
            # ``signal_trips`` -- so the "under way" flag has to be told here.
            async_dispatcher_send(self.hass, self.signal_trip_active, vin)
            if announce:
                self._evaluate_situations(vin, now)
        if builder is None or self.history is None:
            self._peek(vin).trip.take("door_arrival")
            return

        end_place = await self._resolve_place(vin)
        soc_end = self._trip_soc_end(vin, builder.start)
        stats, travelled_km = self._read_trip_segment(vin, builder.start)
        energy_kwh = self._trip_energy_kwh(vin, builder.soc_start, soc_end)
        ended_at = self._trip_end_time(vin, now, builder.start, reason)
        self._peek(vin).trip.take("door_arrival")

        trip = builder.close(
            ended_at,
            end_place=end_place,
            soc_end=soc_end,
            mileage_end=self.odometer_km(vin),
            energy_kwh=energy_kwh,
            travelled_km=travelled_km,
            stats=stats,
            hybrid=self.is_plug_in_hybrid(vin),
        )
        dropped = is_noise_trip(trip)
        self.trip_log.cap(
            "[trip] %s CLOSE(%s) %s -> %s dist=%s (bmw=%s) soc=%s->%s energy=%s stats=%s",
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
            odo_end = self.odometer_km(vin)
            odo_delta = (
                round(odo_end - odo_start, 1)
                if odo_start is not None and odo_end is not None
                else None
            )
            self.trip_log.cap(
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
        self._store_trip(vin, trip, start_zone=start_zone, left_fired=left_fired, announce=announce)
        self._peek(vin).trip.take("late_reading_at")
        if dropped or not announce:
            self._peek(vin).trip.take("closed")
        else:
            self._car(vin).trip.closed = ClosedTrip(
                trip=trip,
                mileage_start=builder.mileage_start,
                odometer=self.odometer_km(vin),
                soc=trip.soc_end,
                closed_at=now,
            )

    def _note_late_reading(self, vin: str, now: datetime) -> None:
        """Remember when a reading past the last trip's close came in.

        Only an odometer that went on or a SoC that went down counts, and only
        while no trip is open, the car hasn't moved since and the close is recent.
        Whose it is gets decided by what comes next (``_settle_late_reading``).
        """

        closed = self._peek(vin).trip.closed
        if (
            closed is None
            or self._peek(vin).trip.builder is not None
            or self._peek(vin).trip.late_reading_at is not None
        ):
            return
        if (now - closed.closed_at).total_seconds() > LATE_READING_MAX_S:
            self._peek(vin).trip.take("closed")
            return
        odometer, soc = self.odometer_km(vin), self._current_soc(vin)
        went_on = (
            odometer is not None and closed.odometer is not None and odometer > closed.odometer
        )
        went_down = soc is not None and closed.soc is not None and soc < closed.soc
        if went_on or went_down:
            self._car(vin).trip.late_reading_at = now

    def _settle_late_reading(self, vin: str, *, moved: bool) -> None:
        """Hand a late reading to the trip just closed, unless the car moved on.

        The i5 has sent the last km of a drive 52 s after the door ended it, so
        the stored trip lacked it and the next one started past it: a kilometre
        no trip owned. A snapshot car leaving again sends its odometer ahead of
        the fix that shows it moving, so a moving fix gives the reading to the
        new drive instead (its start is the parked reading from before it).
        """

        at = self._peek(vin).trip.take("late_reading_at")
        closed = self._peek(vin).trip.take("closed") if moved else self._peek(vin).trip.closed
        if moved or at is None or closed is None or self.history is None:
            return
        odometer, soc = self.odometer_km(vin), self._current_soc(vin)
        trip = closed.trip
        before = (trip.distance_km, trip.soc_end)
        if (
            odometer is not None
            and closed.mileage_start is not None
            and closed.odometer is not None
            and odometer > closed.odometer
        ):
            trip.distance_km = round(odometer - closed.mileage_start, 1)
            closed.odometer = odometer
        if soc is not None and trip.soc_end is not None and soc < trip.soc_end:
            trip.soc_end = soc
            trip.energy_kwh = self._trip_energy_kwh(vin, trip.soc_start, soc)
            closed.soc = soc
        if (trip.distance_km, trip.soc_end) == before:
            return
        self._car(vin).trip.parked_reading = (odometer, soc)
        self.trip_log.cap(
            "[trip] %s LATE reading -> %s: dist %s -> %s, soc_end %s -> %s",
            vin,
            trip.id,
            before[0],
            trip.distance_km,
            before[1],
            trip.soc_end,
        )
        try:
            self.history.add_trip(trip)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not amend trip for %s", mask_vin(vin))
            return
        async_dispatcher_send(self.hass, self.signal_trips, vin)

    def _store_trip(
        self,
        vin: str,
        trip: Trip,
        *,
        start_zone: Optional[tuple[str, str]],
        left_fired: bool,
        announce: bool,
    ) -> None:
        """Drop a noise trip, else classify it, announce the arrival and store it.

        Shared by the live detector's close and the report trips, so a trip is
        dropped, classified and chained by the same rules whichever built it.
        """

        if self.history is None:
            return
        if is_noise_trip(trip):
            # A parking manoeuvre or a spurious blip, not a drive worth logging.
            self.trip_log.cap(
                "[trip] %s DROPPED as noise (dist=%s, duration=%ss)",
                vin,
                trip.distance_km,
                trip.duration_s,
            )
            return

        home_zone = self._home_zone_name()
        work_zone = self._work_zone_name(vin)
        classification = classify_trip(
            trip.start_place,
            trip.end_place,
            home=home_zone,
            work=work_zone,
            default_class=self.trip_settings_for(vin).default_class,
        )
        # Not a commute on its own: it may still be the last leg of one that a
        # stop on the way split in two (groceries between home and work).
        chain: Optional[list] = None
        if classification != CLASS_COMMUTE:
            chain = self._commute_chain(vin, trip, home=home_zone, work=work_zone)
            if chain is not None:
                classification = CLASS_COMMUTE
        if classification is not None:
            trip.classification = classification
            trip.classification_source = SOURCE_AUTO
        if announce:
            self._announce_arrival(vin, trip, start_zone, left_fired)
        self.trip_log.cap(
            "[trip] %s RECORDED id=%s class=%s chain=%s track=%d pts (home=%s work=%s)",
            vin,
            trip.id,
            classification,
            "no" if chain is None else f"{len(chain) + 1} legs",
            len(trip.track),
            home_zone,
            work_zone,
        )

        try:
            self.history.add_trip(trip)
            if chain:
                self._promote_chain_legs(vin, chain)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not record trip for %s", mask_vin(vin))
            return
        async_dispatcher_send(self.hass, self.signal_trips, vin)

    def _announce_arrival(
        self,
        vin: str,
        trip: Trip,
        start_zone: Optional[tuple[str, str]],
        left_fired: bool,
    ) -> None:
        """Fire "left" (if the fixes were too sparse to catch it), "arrived" and "ended"."""

        try:
            end_zone = self._current_zone(vin)
            if start_zone is not None and not left_fired:
                if end_zone is None or end_zone[1] != start_zone[1]:
                    self._fire_zone_left(vin, start_zone)
            payload = {
                "vin": vin,
                "entry_id": self.entry_id,
                "zone": None if end_zone is None else end_zone[0],
                "zone_entity_id": None if end_zone is None else end_zone[1],
                "trip_id": trip.id,
                "trip_start": trip.start.isoformat(),
                "distance_km": trip.distance_km,
                "duration_s": trip.duration_s,
                "energy_kwh": trip.energy_kwh,
                "soc": trip.soc_end,
                "from": (trip.start_place or {}).get("label"),
            }
            if end_zone is not None:
                self._fire(EVENT_ZONE_ARRIVED, payload)
            self._fire(
                EVENT_TRIP_ENDED,
                {
                    **payload,
                    "trip_end": None if trip.end is None else trip.end.isoformat(),
                    "to": (trip.end_place or {}).get("label"),
                    "soc_start": trip.soc_start,
                    "classification": trip.classification,
                },
            )
        except Exception:  # noqa: BLE001 - a trigger must never break the stream
            _LOGGER.exception("Arrival trigger failed for %s", mask_vin(vin))

    def _commute_chain(
        self,
        vin: str,
        trip: Any,
        *,
        home: Optional[str],
        work: Optional[str],
    ) -> Optional[list]:
        """Earlier legs that make this trip the end of a commute, or ``None``.

        Thin wrapper over :func:`history.classify.commute_chain`: it only reads
        back as many recent trips as a chain may span, and never lets a lookup
        failure block the record being written.
        """

        gap_s = self.trip_settings_for(vin).commute_gap_s
        if self.history is None or gap_s <= 0:
            return None
        try:
            previous = self.history.trips(vin, limit=COMMUTE_CHAIN_MAX_LEGS + 1)
        except Exception:  # noqa: BLE001 - classification is best-effort
            _LOGGER.exception("Could not read trip history for %s", mask_vin(vin))
            return None
        return commute_chain(
            trip,
            previous,
            home=home,
            work=work,
            gap_s=gap_s,
        )

    def _promote_chain_legs(self, vin: str, legs: list) -> None:
        """Re-label the earlier legs of a completed commute chain.

        Those legs were stored when the chain's destination wasn't known yet, so
        each got the default type. Only automatic classifications are rewritten --
        a leg the user classified by hand keeps their answer (``commute_chain``
        filters those out). Writing them back through ``add_trip`` replaces each
        record by id, so this stays idempotent.
        """

        for leg in legs:
            leg.classification = CLASS_COMMUTE
            leg.classification_source = SOURCE_AUTO
            self.history.add_trip(leg)
            self.trip_log.cap("[trip] %s chain leg %s -> commute", vin, leg.id)

    def _stopped_for_door(self, vin: str, now: datetime) -> bool:
        """Whether the car last moved long enough ago for an open door to end the drive."""

        last_move = self._peek(vin).gps.last_move
        return last_move is None or (now - last_move).total_seconds() >= DOOR_ARRIVAL_STOP_S

    def _drop_door_arrival_on_move(self, vin: str, now: datetime) -> None:
        """Forget an early door-open once the car has clearly moved on.

        Movement within ``DOOR_ARRIVAL_STOP_S`` of the door doesn't count: the
        door and the final fix arrive within a second or two of each other, in
        either order, and a fix only completes when its second half arrives.
        """

        opened = self._peek(vin).trip.door_arrival
        if opened is not None and (now - opened).total_seconds() > DOOR_ARRIVAL_STOP_S:
            del self._peek(vin).trip.door_arrival

    def _hold_close_on_silence(self, vin: str, now: datetime) -> bool:
        """True when a due close has no evidence behind it and must wait.

        The stationary debounce fires ``TRIP_CLOSE_DEBOUNCE_S`` after the last
        movement -- but "no movement seen" has two very different causes. If a fix
        arrived showing the car standing still (or the car said ``isMoving:
        false``), the car stopped and the trip really is over. If nothing arrived
        at all, only the *stream* stopped, and closing here splits a drive through
        a tunnel or a cell-coverage hole into two trips -- the detector's known
        worst failure mode.

        So a close with no stop evidence newer than the last movement is held off,
        up to ``TRIP_SILENT_HOLD_MAX_S`` from when the hold began. Cars that stream
        no position at all (detection then runs off motion/ignition) are never
        held: there is no cadence to have gone quiet.
        """

        last_move = self._peek(vin).gps.last_move
        if last_move is None:
            return False
        evidence = self._peek(vin).trip.last_stop_evidence
        if evidence is not None and evidence >= last_move:
            return False  # the car was seen standing still: a real stop
        if self._peek(vin).trip.door_arrival is not None:
            return False  # the driver got out and the car never moved on
        trip = self._car(vin).trip
        if trip.held_since is None:
            trip.held_since = now
        held_since = trip.held_since
        if (now - held_since).total_seconds() >= TRIP_SILENT_HOLD_MAX_S:
            return False  # the stream never came back; close on what we have
        self.trip_log.cap(
            "[trip.timer] %s HOLD (stream silent %ds, no stop evidence)",
            vin,
            int((now - last_move).total_seconds()),
            substrate=True,
        )
        return True

    def _arm_trip_close_timer(
        self, vin: str, *, quiet: bool = False, delay_s: float = TRIP_CLOSE_DEBOUNCE_S
    ) -> None:
        if self._peek(vin).trip.close_timer is not None:
            return

        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, where async_create_task is
        # not thread-safe.
        @callback
        def _fire(_now) -> None:
            self._peek(vin).trip.take("close_timer")
            self._peek(vin).trip.take("close_due")
            now = datetime.now(timezone.utc)
            if self._hold_close_on_silence(vin, now):
                # Nothing says the car stopped -- only that the stream did. Keep
                # the trip open and look again after another debounce.
                self._arm_trip_close_timer(vin, quiet=True)
                return
            reason = "silent" if self._peek(vin).trip.take("held_since") else "stationary"
            self.trip_log.cap(
                "[trip.timer] %s FIRE -> closing (%s, %ds)",
                vin,
                reason,
                delay_s,
                substrate=True,
            )
            self.hass.async_create_task(self._close_trip(vin, now, reason=reason))

        self._car(vin).trip.close_due = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
        self._car(vin).trip.close_timer = async_call_later(self.hass, delay_s, _fire)
        # Only the first arm (car goes stationary) is worth a line; the per-fix
        # reset while moving would otherwise log every fix (the countdown is
        # already shown on each [trip.gps] line).
        if not quiet:
            self.trip_log.cap(
                "[trip.timer] %s ARM due_in=%ds",
                vin,
                delay_s,
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
        cancel = self._peek(vin).trip.take("close_timer")
        self._peek(vin).trip.take("close_due")
        if cancel is not None:
            cancel()

    async def async_flush_trips(self) -> None:
        """Close and persist any in-progress trips (called on unload/reload).

        A trip left open when Home Assistant restarts would otherwise be lost;
        closing it here captures whatever end state we have. Timers are cancelled
        first so a pending debounce can't fire against a stale builder.

        A trip being held open through stream silence is flushed as ``silent`` so
        its end is still backdated to the last movement -- for those, "whatever we
        have, now" could be half an hour past the arrival.
        """

        for car in self._cars.values():
            if car.trip.close_timer is not None:
                car.trip.close_timer()
                car.trip.close_timer = None
        # A pending vehicle report is dropped, not fired: the reading is newer
        # than the persisted record, so the first batch after the restart -- the
        # stream's or the REST catch-up's -- re-arms it.
        for vin in self._vins_where(lambda car: car.report.timer is not None):
            self._cancel_report_timer(vin)
        for vin in self._vins_where(lambda car: car.trip.builder is not None):
            reason = "silent" if self._peek(vin).trip.held_since is not None else "unload"
            with suppress(Exception):
                await self._close_trip(
                    vin, datetime.now(timezone.utc), reason=reason, announce=False
                )

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
        tracking = self._peek(vin).charge.tracking
        if tracking is None:
            return None
        if tracking.estimated_percent is not None:
            return round(tracking.estimated_percent, 1)
        if tracking.last_soc_percent is not None:
            return round(tracking.last_soc_percent, 1)
        return None

    def _trip_soc_end(self, vin: str, started: datetime) -> Optional[float]:
        """The SoC at the end of a drive; on a hybrid, only if read during it.

        Issue #25's X3 30e never streams its SoC (it arrives only with a REST
        catch-up), so the reading from before a drive was still the latest at its
        end, and every trip was stored as a battery that never moved: "38 → 38 %".
        Only on a plug-in hybrid, though. A battery car streams its SoC on
        change, so no reading during a short hop means it genuinely didn't drop
        a whole percent -- about half the i5's trips -- and that end is true.
        """

        if self.is_plug_in_hybrid(vin):
            tracking = self._peek(vin).charge.tracking
            if tracking is None or tracking.last_update is None or tracking.last_update <= started:
                return None
        return self._current_soc(vin)

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
        self, vin: str, since: datetime
    ) -> tuple[dict[str, Any], Optional[float]]:
        """BMW's per-segment statistics and its own travelled distance.

        The statistics ride the trip record (served via ``get_trips``) rather
        than becoming entities; the distance is a fallback for when the odometer
        didn't tick over the trip window.

        Only what BMW stamped since the trip began (``since``) is this trip's:
        the segment fields stay stored from the previous drive until BMW sends
        new ones, and a short hop that never ticked the odometer took that
        drive's whole distance as its own.
        """

        def _num(descriptor: str) -> Optional[float]:
            state = self.get_state(vin, descriptor)
            if state is None or state.value is None or not state.timestamp:
                return None
            stamped = dt_util.parse_datetime(state.timestamp)
            if stamped is None or stamped < since - timedelta(seconds=60):
                return None
            try:
                return float(state.value)
            except TypeError, ValueError:
                return None

        stats: dict[str, Any] = {}
        for key, descriptor in (
            # BMW documents ``recuperationTotal`` as an average per 100 km, not a
            # kWh total -- the key says so, because the earlier name led the
            # month-in-review to add the figures together (see summary.py).
            ("recuperation_kwh_per_100km", DESC_SEG_RECUP),
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
                self._settle_late_reading(vin, moved=False)
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
        self._seed_edge(vin, descriptor, stored_value)
        if descriptor in (DESC_GPS_LAT, DESC_GPS_LON):
            self._seed_gps_from_restore(vin)
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
