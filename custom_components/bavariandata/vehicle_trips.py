"""Trip detection: when a drive starts and ends, and the trip it becomes.

BMW sends no "trip started" or "trip ended" a detector can rely on: the i5
streams no motion or ignition, its ``trip.segment`` batches arrive mid-drive,
and the position comes as two messages a second apart. So a drive is read off
the evidence there is -- a fix that moved, the driver door, motion and
ignition on cars that stream them, a fresh segment batch -- and closed on a
debounce that holds through stream silence rather than splitting a drive in a
tunnel. The trip is then dated by the car's own readings, classified (commute,
chains of legs), announced (arrived, ended) and stored.

The pieces that need no Home Assistant -- the track, the jitter rule, the
classification -- live in ``history/``; this applies them to one car's state
(``car_state.TripState`` and ``GpsState``) through its host, the coordinator.
Trip bookkeeping must never break the stream: every entry point swallows and
logs.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .car_signals import (
    DESC_GPS_FIX,
    DESC_GPS_HEADING,
    DESC_GPS_LAT,
    DESC_GPS_LON,
    DESC_GPS_SATS,
    DESC_SEG_ACCEL_STARS,
    DESC_SEG_BRAKE_STARS,
    DESC_SEG_CONSUMPTION,
    DESC_SEG_DISTANCE,
    DESC_SEG_ECO,
    DESC_SEG_ELECTRIC,
    DESC_SEG_RECUP,
    DOOR_ARRIVAL_STOP_S,
    DOOR_SETTLE_S,
    GPS_PAIR_PARTS,
    GPS_PAIR_STALE_S,
    LATE_READING_MAX_S,
    PENDING_START_MAX_S,
    TRIP_CLOSE_DEBOUNCE_S,
    TRIP_SILENT_HOLD_MAX_S,
)
from .car_state import ClosedTrip, Vehicle
from .const import EVENT_TRIP_ENDED, EVENT_ZONE_ARRIVED
from .debug import mask_vin
from .history.classify import COMMUTE_CHAIN_MAX_LEGS, classify_trip, commute_chain
from .history.trip_builder import (
    GpsTracker,
    TripBuilder,
    is_gps_movement,
    is_noise_trip,
    silence_implies_stop,
)
from .history.trips import CLASS_COMMUTE, SOURCE_AUTO, Trip, place
from .trip_capture import TripLog
from .trip_settings import TripSettings

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")


class TripHost(Protocol):
    """What trip detection reads and does, from the coordinator."""

    hass: Any
    entry_id: str
    history: Any
    geocoder: Any
    trip_debug: bool
    trip_log: TripLog
    events: Any  # vehicle_events.VehicleEvents
    reports: Any  # vehicle_reports.VehicleReports

    @property
    def signal_trips(self) -> str: ...
    @property
    def signal_trip_active(self) -> str: ...

    @property
    def _cars(self) -> Dict[str, Vehicle]: ...
    def _car(self, vin: str) -> Vehicle: ...
    def _peek(self, vin: str) -> Vehicle: ...
    def _vins_where(self, test: Any) -> list[str]: ...
    def get_state(self, vin: str, descriptor: str) -> Any: ...
    def _raw_value(self, vin: str, descriptor: str) -> Any: ...
    def _coordinate(self, vin: str, kind: str) -> Optional[float]: ...
    def _current_soc(self, vin: str) -> Optional[float]: ...
    def odometer_km(self, vin: str) -> Optional[float]: ...
    def battery_capacity_kwh(self, vin: str) -> Optional[float]: ...
    def is_plug_in_hybrid(self, vin: str) -> bool: ...
    def trip_settings_for(self, vin: str) -> TripSettings: ...
    def _zone_at(self, latitude: float, longitude: float) -> Optional[tuple[str, str]]: ...
    def _zone_name(self, latitude: float, longitude: float) -> Optional[str]: ...
    def _current_zone(self, vin: str) -> Optional[tuple[str, str]]: ...
    def _home_zone_name(self) -> Optional[str]: ...
    def _work_zone_name(self, vin: str) -> Optional[str]: ...
    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None: ...


class VehicleTrips:
    """Detects, closes and records the drives of one account's cars."""

    def __init__(self, host: TripHost) -> None:
        self._host = host

    # --- trips (roadmap Phase 3) -------------------------------------------

    async def process_trip_signals(
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

        if self._host.history is None:
            return
        try:
            open_trip = self._host._peek(vin).trip.builder is not None
            # Which of these BMW actually streams (and in what order) is the one
            # thing no unit test here can answer, so record every observation
            # under the opt-in debug flag. Tag it so a drive can be grepped out
            # of an otherwise very chatty debug log.
            if motion is not None or ignition is not None or segment_fresh:
                self._host.trip_log.cap(
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
                await self._open_trip(vin, now, parked_until=self._host._peek(vin).gps.last_fix_at)
            if motion is True:
                # Moving again: cancel any pending stationary-close.
                self._drop_door_arrival_on_move(vin, now)
                self._host._peek(vin).trip.take("held_since")
                self._cancel_trip_close_timer(vin)
            elif motion is False:
                # An explicit "not moving" is stop evidence in its own right, so a
                # car that streams it never has its close held (see
                # ``_hold_close_on_silence``).
                self._host._car(vin).trip.last_stop_evidence = now
                if open_trip:
                    self._arm_trip_close_timer(vin)
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Trip detection failed for %s", mask_vin(vin))

    async def process_door_signal(self, vin: str, now: datetime, door_open: bool) -> None:
        """Refine trip start/end from the driver door, when the car streams it.

        Closing the door (driver got in) arms a start marker at that position, so
        the next movement seeds the trip there. Opening the door after the car has
        stopped means the driver is getting out -- an arrival -- so an open trip
        is closed promptly instead of waiting out the GPS debounce. A pure
        accelerator: cars that never send the door fall back to GPS alone.
        """

        if self._host.history is None:
            return
        try:
            previous = self._host._peek(vin).trip.driver_door_open
            self._host._car(vin).trip.driver_door_open = door_open
            if previous is None or previous == door_open:
                return  # first reading or no transition
            open_trip = self._host._peek(vin).trip.builder is not None

            if door_open and open_trip:
                # Opened while a trip is open: an arrival, but only once the car
                # has actually stopped (guards a flickery read mid-move).
                last_move = self._host._peek(vin).gps.last_move
                stopped = self._stopped_for_door(vin, now)
                # Seen standing: a fix since the last movement showed it, or the
                # door already opened on arrival and the car never moved on.
                evidence = self._host._peek(vin).trip.last_stop_evidence
                seen_standing = (
                    last_move is None
                    or (evidence is not None and evidence >= last_move)
                    or self._host._peek(vin).trip.door_arrival is not None
                )
                if stopped and seen_standing:
                    self._host.trip_log.cap(
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
                    trip = self._host._car(vin).trip
                    if trip.door_arrival is None:
                        trip.door_arrival = now
                    self._cancel_trip_close_timer(vin)
                    self._arm_trip_close_timer(vin, delay_s=DOOR_SETTLE_S)
                    self._host.trip_log.cap(
                        "[trip.door] %s driver door opened -> close in %ds",
                        vin,
                        DOOR_SETTLE_S,
                        substrate=True,
                    )
                else:
                    # Too soon to close on: it arrives right behind the final fix.
                    # Remember it -- if the car moves on it is dropped, otherwise
                    # it dates the arrival and says the car stopped.
                    trip = self._host._car(vin).trip
                    if trip.door_arrival is None:
                        trip.door_arrival = now
            elif not door_open and not open_trip:
                # Closed with no trip open: the driver just got in. Mark the start
                # here so the next movement seeds the trip from this spot.
                self._host._car(vin).trip.pending_start = (
                    now,
                    self._host._peek(vin).gps.last_position,
                )
                self._host.trip_log.cap(
                    "[trip.door] %s driver door closed -> start marked at %s",
                    vin,
                    self._host._peek(vin).gps.last_position,
                    substrate=True,
                )
        except Exception:  # noqa: BLE001 - never let trip logic break the stream
            _LOGGER.exception("Door trip detection failed for %s", mask_vin(vin))

    def seed_gps_from_restore(self, vin: str) -> None:
        """Start GPS tracking from where the car stood when we last stopped.

        Without it, the first fix after a restart is only a starting point: a
        car that set off meanwhile has its trip opened one fix late, from that
        already-moved-on fix -- so a drive out of the Home zone no longer starts
        at Home, and commute classification loses it. Only a fresh tracker is
        seeded; a live fix always wins over a restored one.
        """

        if self._host._peek(vin).gps.tracker is not None:
            return
        try:
            latitude = float(self._host.get_state(vin, DESC_GPS_LAT).value)
            longitude = float(self._host.get_state(vin, DESC_GPS_LON).value)
        except AttributeError, TypeError, ValueError:
            return  # the other half has not been restored yet
        tracker = self._host._car(vin).gps.tracker = GpsTracker()
        tracker.last_lat, tracker.last_lon = latitude, longitude
        if self._host._car(vin).gps.last_position is None:
            self._host._car(vin).gps.last_position = (latitude, longitude)

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

        gps = self._host._car(vin).gps
        if gps.pending_parts is None:
            gps.pending_parts = set()
        pending = gps.pending_parts
        pending |= parts
        if not GPS_PAIR_PARTS <= pending:
            since = self._host._peek(vin).gps.pending_since
            if since is None:
                self._host._car(vin).gps.pending_since = now
                return False
            if (now - since).total_seconds() < GPS_PAIR_STALE_S:
                return False  # half a fix; wait for the mate
        self._host._peek(vin).gps.take("pending_parts")
        self._host._peek(vin).gps.take("pending_since")

        fix_ts = self._gps_fix_timestamp(vin)
        if fix_ts is not None:
            last = self._host._peek(vin).gps.last_fix_ts
            if last is not None and fix_ts <= last:
                return False  # BMW re-sent a fix already processed
            self._host._car(vin).gps.last_fix_ts = fix_ts
        return True

    def _gps_fix_timestamp(self, vin: str) -> Optional[datetime]:
        """BMW's own time for the current fix, or None when unstamped.

        Both halves of a fix carry the same timestamp, so the newer of the two is
        that fix's time whichever message completed the pair.
        """

        stamps = []
        for descriptor in (DESC_GPS_LAT, DESC_GPS_LON):
            state = self._host.get_state(vin, descriptor)
            if state is None or not state.timestamp:
                continue
            parsed = dt_util.parse_datetime(state.timestamp)
            if parsed is not None:
                stamps.append(parsed)
        return max(stamps) if stamps else None

    async def process_gps_signal(
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

        if self._host.history is None:
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
        latitude = self._host._coordinate(vin, "latitude")
        longitude = self._host._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return
        try:
            tracker = self._host._peek(vin).gps.tracker
            if tracker is None:
                tracker = self._host._car(vin).gps.tracker = GpsTracker()
            step_km = tracker.step(latitude, longitude)
            moving = is_gps_movement(step_km)
            open_trip = self._host._peek(vin).trip.builder is not None

            # Inter-fix gap and stream latency -- the two numbers that explain a
            # mid-drive split (a gap over the debounce) or a laggy close.
            prev_fix = self._host._peek(vin).gps.last_fix_at
            gap_s = (now - prev_fix).total_seconds() if prev_fix is not None else None
            self._host._car(vin).gps.last_fix_at = now

            if moving:
                if (
                    open_trip
                    and self._host._peek(vin).trip.held_since is not None
                    and silence_implies_stop(gap_s, step_km, min_gap_s=TRIP_CLOSE_DEBOUNCE_S)
                ):
                    # The fixes are back, but barely anywhere: the car stood
                    # through the silence (a garage that swallows the fix), so the
                    # held close was right after all. Close it *before* stamping
                    # this movement, or the backdated end would land on this
                    # departure instead of the real arrival -- then let the fix
                    # below open the new trip.
                    self._host._peek(vin).trip.take("held_since")
                    self._cancel_trip_close_timer(vin)
                    self._host.trip_log.cap(
                        "[trip.timer] %s held close CONFIRMED by distance (%dm over %ds silence)",
                        vin,
                        round(step_km * 1000),
                        int(gap_s or 0),
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="silent")
                    open_trip = False
                self._drop_door_arrival_on_move(vin, now)
                self._host._car(vin).gps.last_move = now
                if not open_trip:
                    # Seed the track from where the car actually started (the
                    # parked position / door-close point), not this already
                    # moved-on fix -- done inside _open_trip before we overwrite
                    # the last GPS position below. ``prev_fix`` is the last fix that
                    # still showed the car parked, which bounds how far the start
                    # may be backdated (see _trip_start_seed).
                    await self._open_trip(vin, now, parked_until=prev_fix)
                    open_trip = True
                builder = self._host._peek(vin).trip.builder
                if builder is not None:
                    builder.add_gps_km(step_km)
                    # Record the route point too, stamped with this fix's time
                    # (opt-in; a no-op otherwise).
                    builder.add_track_point(latitude, longitude, now)
                    self._host.events.check_zone_left(vin, latitude, longitude)
                # The fixes are back and the car has moved on: the drive never
                # ended, so drop any hold and keep going as one trip.
                self._host._peek(vin).trip.take("held_since")
                # Rolling window: keep the trip alive while the car is moving,
                # but guarantee a close even if the fixes stop arriving.
                self._reset_trip_close_timer(vin)
            else:
                # A fix that arrived showing no movement is the one thing that
                # positively says the *car* stopped rather than the stream.
                self._host._car(vin).trip.last_stop_evidence = now
                if open_trip and self._host._peek(vin).trip.held_since is not None:
                    # A held close, now confirmed: the stream went quiet while the
                    # car was already parked. Close on the spot -- the end is
                    # backdated to the last movement, so nothing is lost.
                    self._host._peek(vin).trip.take("held_since")
                    self._cancel_trip_close_timer(vin)
                    self._host.trip_log.cap(
                        "[trip.timer] %s held close CONFIRMED by a stationary fix",
                        vin,
                        substrate=True,
                    )
                    await self._close_trip(vin, now, reason="silent")
                    open_trip = False
                elif (
                    open_trip
                    and self._host._peek(vin).trip.door_arrival is not None
                    and self._stopped_for_door(vin, now)
                ):
                    # The door opened and the car is seen standing: arrived. Not
                    # within seconds of the last move, though: a door opened at a
                    # car park barrier is followed by the car driving on.
                    self._host.trip_log.cap(
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
                    self.settle_late_reading(vin, moved=False)
                    self._host._car(vin).trip.parked_reading = (
                        self._host.odometer_km(vin),
                        self._host._current_soc(vin),
                    )

            # Remember where the car is now, for the next trip's start seed.
            self._host._car(vin).gps.last_position = (latitude, longitude)
            self._note_capture_fix(vin, now, step_km, moving, gap_s)
            if self._host.trip_debug:
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

        stats = self._host._peek(vin).trip.capture
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

        last_move = self._host._peek(vin).gps.last_move
        since_move = (now - last_move).total_seconds() if last_move is not None else None
        due = self._host._peek(vin).trip.close_due
        timer = "none" if due is None else f"armed({int((due - now).total_seconds())}s)"
        latency = (now - fix_ts).total_seconds() if fix_ts is not None else None
        builder = self._host._peek(vin).trip.builder
        # GPS-quality context: distinguishes "the car stopped" from "the fix was
        # lost" when a run of fixes shows no movement.
        fix_state = self._host._raw_value(vin, DESC_GPS_FIX)
        sats = self._host._raw_value(vin, DESC_GPS_SATS)
        heading = self._host._raw_value(vin, DESC_GPS_HEADING)
        self._host.trip_log.cap(
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
            self._host.odometer_km(vin),
            self._host._current_soc(vin),
            builder.gps_km if builder is not None else 0.0,
            open_trip,
            timer,
            fix_state,
            sats,
            heading,
            substrate=True,
        )

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

        if self._host._peek(vin).trip.held_since is not None:
            return True
        last = self._host._peek(vin).gps.last_move
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
        pending = self._host._peek(vin).trip.take("pending_start")
        if pending is not None:
            marked_at, pos = pending
            if (now - marked_at).total_seconds() <= PENDING_START_MAX_S:
                marked_pos = pos
                started_at = marked_at
                if parked_until is not None and parked_until > started_at:
                    started_at = min(parked_until, now)
        if marked_pos:
            return marked_pos, started_at
        parked = self._host._peek(vin).gps.last_position
        if parked is not None:
            return parked, started_at
        latitude = self._host._coordinate(vin, "latitude")
        longitude = self._host._coordinate(vin, "longitude")
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

        odometer, soc = self._host.odometer_km(vin), self._host._current_soc(vin)
        parked = self._host._peek(vin).trip.take("parked_reading")
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

        self._host._peek(vin).trip.take("held_since")
        self._host._peek(vin).trip.take("door_arrival")
        # Moving again: a reading still pending after the last close is this drive's.
        self.settle_late_reading(vin, moved=True)
        seed, started_at = self._trip_start_seed(vin, now, parked_until)
        # Resolve the start place from the seed (the parked/door-close spot), not
        # the ~hundreds-of-metres-later fix that first registered as movement.
        start_place = await self.resolve_place(
            vin, seed[0] if seed else None, seed[1] if seed else None
        )
        mileage_start, soc_start = self._trip_start_readings(vin)
        builder = self._host._car(vin).trip.builder = TripBuilder(
            vin,
            started_at,
            start_place=start_place,
            soc_start=soc_start,
            mileage_start=mileage_start,
            location_assumed=start_place is None or start_place.get("label") == "Unknown",
            record_track=self._host.trip_settings_for(vin).record_track,
        )
        # Seed the route from where the drive actually started (see
        # _trip_start_seed), stamped t=0 -- the seed is that moment's position, so
        # it carries the start's own time, not detection time. A no-op when route
        # recording is off or no position is available.
        if seed is not None:
            builder.add_track_point(seed[0], seed[1], started_at)
        # Where "left a zone" is measured from: the parked spot, not the first
        # fix that registered as movement (already on its way out).
        self._host._car(vin).trip.zone_left_fired = False
        self._host._peek(vin).trip.take("start_zone")
        if seed is not None:
            start_zone = self._host._zone_at(seed[0], seed[1])
            if start_zone is not None:
                self._host._car(vin).trip.start_zone = start_zone
        # Start the per-trip capture stats (a no-op cost when capture is off, but
        # cheap and it means _note_capture_fix needn't check the mode).
        self._host._car(vin).trip.capture = {
            "fixes": 0,
            "moves": 0,
            "holds": 0,
            "max_gap": 0.0,
            "odo_start": builder.mileage_start,
        }
        self._host.trip_log.cap(
            "[trip] %s OPEN at %s (detected %s) place=%s soc=%s odo=%s",
            vin,
            started_at.isoformat(),
            now.isoformat(),
            (start_place or {}).get("label"),
            builder.soc_start,
            builder.mileage_start,
        )
        async_dispatcher_send(self._host.hass, self._host.signal_trip_active, vin)

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

        arrival = self._host._peek(vin).trip.door_arrival
        last_move = self._host._peek(vin).gps.last_move
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

        builder = self._host._peek(vin).trip.take("builder")
        if builder is not None:
            # Where the next trip starts, should it open before a parked fix.
            self._host._car(vin).trip.parked_reading = (
                self._host.odometer_km(vin),
                self._host._current_soc(vin),
            )
        cap_stats = self._host._peek(vin).trip.take("capture")
        start_zone = self._host._peek(vin).trip.take("start_zone")
        left_fired = self._host._peek(vin).trip.zone_left_fired
        self._host._car(vin).trip.zone_left_fired = False
        self._cancel_trip_close_timer(vin)
        self._host._peek(vin).trip.take("close_due")
        self._host._peek(vin).trip.take("held_since")
        if builder is not None:
            # The drive is over whatever happens to the record below -- including
            # the no-history and dropped-as-noise paths, which never reach
            # ``signal_trips`` -- so the "under way" flag has to be told here.
            async_dispatcher_send(self._host.hass, self._host.signal_trip_active, vin)
            if announce:
                self._host.events.evaluate_situations(vin, now)
        if builder is None or self._host.history is None:
            self._host._peek(vin).trip.take("door_arrival")
            return

        end_place = await self.resolve_place(vin)
        soc_end = self.trip_soc_end(vin, builder.start)
        stats, travelled_km = self._read_trip_segment(vin, builder.start)
        energy_kwh = self.trip_energy_kwh(vin, builder.soc_start, soc_end)
        ended_at = self._trip_end_time(vin, now, builder.start, reason)
        self._host._peek(vin).trip.take("door_arrival")

        trip = builder.close(
            ended_at,
            end_place=end_place,
            soc_end=soc_end,
            mileage_end=self._host.odometer_km(vin),
            energy_kwh=energy_kwh,
            travelled_km=travelled_km,
            stats=stats,
            hybrid=self._host.is_plug_in_hybrid(vin),
        )
        dropped = is_noise_trip(trip)
        self._host.trip_log.cap(
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
        if self._host.trip_debug:
            odo_start = (cap_stats or {}).get("odo_start")
            odo_end = self._host.odometer_km(vin)
            odo_delta = (
                round(odo_end - odo_start, 1)
                if odo_start is not None and odo_end is not None
                else None
            )
            self._host.trip_log.cap(
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
        self.store_trip(vin, trip, start_zone=start_zone, left_fired=left_fired, announce=announce)
        self._host._peek(vin).trip.take("late_reading_at")
        if dropped or not announce:
            self._host._peek(vin).trip.take("closed")
        else:
            self._host._car(vin).trip.closed = ClosedTrip(
                trip=trip,
                mileage_start=builder.mileage_start,
                odometer=self._host.odometer_km(vin),
                soc=trip.soc_end,
                closed_at=now,
            )

    def note_late_reading(self, vin: str, now: datetime) -> None:
        """Remember when a reading past the last trip's close came in.

        Only an odometer that went on or a SoC that went down counts, and only
        while no trip is open, the car hasn't moved since and the close is recent.
        Whose it is gets decided by what comes next (``_settle_late_reading``).
        """

        closed = self._host._peek(vin).trip.closed
        if (
            closed is None
            or self._host._peek(vin).trip.builder is not None
            or self._host._peek(vin).trip.late_reading_at is not None
        ):
            return
        if (now - closed.closed_at).total_seconds() > LATE_READING_MAX_S:
            self._host._peek(vin).trip.take("closed")
            return
        odometer, soc = self._host.odometer_km(vin), self._host._current_soc(vin)
        went_on = (
            odometer is not None and closed.odometer is not None and odometer > closed.odometer
        )
        went_down = soc is not None and closed.soc is not None and soc < closed.soc
        if went_on or went_down:
            self._host._car(vin).trip.late_reading_at = now

    def settle_late_reading(self, vin: str, *, moved: bool) -> None:
        """Hand a late reading to the trip just closed, unless the car moved on.

        The i5 has sent the last km of a drive 52 s after the door ended it, so
        the stored trip lacked it and the next one started past it: a kilometre
        no trip owned. A snapshot car leaving again sends its odometer ahead of
        the fix that shows it moving, so a moving fix gives the reading to the
        new drive instead (its start is the parked reading from before it).
        """

        at = self._host._peek(vin).trip.take("late_reading_at")
        closed = (
            self._host._peek(vin).trip.take("closed")
            if moved
            else self._host._peek(vin).trip.closed
        )
        if moved or at is None or closed is None or self._host.history is None:
            return
        odometer, soc = self._host.odometer_km(vin), self._host._current_soc(vin)
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
            trip.energy_kwh = self.trip_energy_kwh(vin, trip.soc_start, soc)
            closed.soc = soc
        if (trip.distance_km, trip.soc_end) == before:
            return
        self._host._car(vin).trip.parked_reading = (odometer, soc)
        self._host.trip_log.cap(
            "[trip] %s LATE reading -> %s: dist %s -> %s, soc_end %s -> %s",
            vin,
            trip.id,
            before[0],
            trip.distance_km,
            before[1],
            trip.soc_end,
        )
        try:
            self._host.history.add_trip(trip)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not amend trip for %s", mask_vin(vin))
            return
        async_dispatcher_send(self._host.hass, self._host.signal_trips, vin)

    def store_trip(
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

        if self._host.history is None:
            return
        if is_noise_trip(trip):
            # A parking manoeuvre or a spurious blip, not a drive worth logging.
            self._host.trip_log.cap(
                "[trip] %s DROPPED as noise (dist=%s, duration=%ss)",
                vin,
                trip.distance_km,
                trip.duration_s,
            )
            return

        home_zone = self._host._home_zone_name()
        work_zone = self._host._work_zone_name(vin)
        classification = classify_trip(
            trip.start_place,
            trip.end_place,
            home=home_zone,
            work=work_zone,
            default_class=self._host.trip_settings_for(vin).default_class,
        )
        # Not a commute on its own: it may still be the last leg of one that a
        # stop on the way split in two (groceries between home and work).
        chain: Optional[list[Any]] = None
        if classification != CLASS_COMMUTE:
            chain = self._commute_chain(vin, trip, home=home_zone, work=work_zone)
            if chain is not None:
                classification = CLASS_COMMUTE
        if classification is not None:
            trip.classification = classification
            trip.classification_source = SOURCE_AUTO
        if announce:
            self._announce_arrival(vin, trip, start_zone, left_fired)
        self._host.trip_log.cap(
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
            self._host.history.add_trip(trip)
            if chain:
                self._promote_chain_legs(vin, chain)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not record trip for %s", mask_vin(vin))
            return
        async_dispatcher_send(self._host.hass, self._host.signal_trips, vin)

    def _announce_arrival(
        self,
        vin: str,
        trip: Trip,
        start_zone: Optional[tuple[str, str]],
        left_fired: bool,
    ) -> None:
        """Fire "left" (if the fixes were too sparse to catch it), "arrived" and "ended"."""

        try:
            end_zone = self._host._current_zone(vin)
            if start_zone is not None and not left_fired:
                if end_zone is None or end_zone[1] != start_zone[1]:
                    self._host.events.fire_zone_left(vin, start_zone)
            payload = {
                "vin": vin,
                "entry_id": self._host.entry_id,
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
                self._host._fire(EVENT_ZONE_ARRIVED, payload)
            self._host._fire(
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
    ) -> Optional[list[Any]]:
        """Earlier legs that make this trip the end of a commute, or ``None``.

        Thin wrapper over :func:`history.classify.commute_chain`: it only reads
        back as many recent trips as a chain may span, and never lets a lookup
        failure block the record being written.
        """

        gap_s = self._host.trip_settings_for(vin).commute_gap_s
        if self._host.history is None or gap_s <= 0:
            return None
        try:
            previous = self._host.history.trips(vin, limit=COMMUTE_CHAIN_MAX_LEGS + 1)
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

    def _promote_chain_legs(self, vin: str, legs: list[Any]) -> None:
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
            self._host.history.add_trip(leg)
            self._host.trip_log.cap("[trip] %s chain leg %s -> commute", vin, leg.id)

    def _stopped_for_door(self, vin: str, now: datetime) -> bool:
        """Whether the car last moved long enough ago for an open door to end the drive."""

        last_move = self._host._peek(vin).gps.last_move
        return last_move is None or (now - last_move).total_seconds() >= DOOR_ARRIVAL_STOP_S

    def _drop_door_arrival_on_move(self, vin: str, now: datetime) -> None:
        """Forget an early door-open once the car has clearly moved on.

        Movement within ``DOOR_ARRIVAL_STOP_S`` of the door doesn't count: the
        door and the final fix arrive within a second or two of each other, in
        either order, and a fix only completes when its second half arrives.
        """

        opened = self._host._peek(vin).trip.door_arrival
        if opened is not None and (now - opened).total_seconds() > DOOR_ARRIVAL_STOP_S:
            del self._host._peek(vin).trip.door_arrival

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

        last_move = self._host._peek(vin).gps.last_move
        if last_move is None:
            return False
        evidence = self._host._peek(vin).trip.last_stop_evidence
        if evidence is not None and evidence >= last_move:
            return False  # the car was seen standing still: a real stop
        if self._host._peek(vin).trip.door_arrival is not None:
            return False  # the driver got out and the car never moved on
        trip = self._host._car(vin).trip
        if trip.held_since is None:
            trip.held_since = now
        held_since = trip.held_since
        if (now - held_since).total_seconds() >= TRIP_SILENT_HOLD_MAX_S:
            return False  # the stream never came back; close on what we have
        self._host.trip_log.cap(
            "[trip.timer] %s HOLD (stream silent %ds, no stop evidence)",
            vin,
            int((now - last_move).total_seconds()),
            substrate=True,
        )
        return True

    def _arm_trip_close_timer(
        self, vin: str, *, quiet: bool = False, delay_s: float = TRIP_CLOSE_DEBOUNCE_S
    ) -> None:
        if self._host._peek(vin).trip.close_timer is not None:
            return

        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, where async_create_task is
        # not thread-safe.
        @callback
        def _fire(_now: datetime) -> None:
            self._host._peek(vin).trip.take("close_timer")
            self._host._peek(vin).trip.take("close_due")
            now = datetime.now(timezone.utc)
            if self._hold_close_on_silence(vin, now):
                # Nothing says the car stopped -- only that the stream did. Keep
                # the trip open and look again after another debounce.
                self._arm_trip_close_timer(vin, quiet=True)
                return
            reason = "silent" if self._host._peek(vin).trip.take("held_since") else "stationary"
            self._host.trip_log.cap(
                "[trip.timer] %s FIRE -> closing (%s, %ds)",
                vin,
                reason,
                delay_s,
                substrate=True,
            )
            self._host.hass.async_create_task(self._close_trip(vin, now, reason=reason))

        self._host._car(vin).trip.close_due = datetime.now(timezone.utc) + timedelta(
            seconds=delay_s
        )
        self._host._car(vin).trip.close_timer = async_call_later(self._host.hass, delay_s, _fire)
        # Only the first arm (car goes stationary) is worth a line; the per-fix
        # reset while moving would otherwise log every fix (the countdown is
        # already shown on each [trip.gps] line).
        if not quiet:
            self._host.trip_log.cap(
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
        cancel = self._host._peek(vin).trip.take("close_timer")
        self._host._peek(vin).trip.take("close_due")
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

        for car in self._host._cars.values():
            if car.trip.close_timer is not None:
                car.trip.close_timer()
                car.trip.close_timer = None
        # A pending vehicle report is dropped, not fired: the reading is newer
        # than the persisted record, so the first batch after the restart -- the
        # stream's or the REST catch-up's -- re-arms it.
        for vin in self._host._vins_where(lambda car: car.report.timer is not None):
            self._host.reports.cancel_report_timer(vin)
        for vin in self._host._vins_where(lambda car: car.trip.builder is not None):
            reason = "silent" if self._host._peek(vin).trip.held_since is not None else "unload"
            with suppress(Exception):
                await self._close_trip(
                    vin, datetime.now(timezone.utc), reason=reason, announce=False
                )

    async def resolve_place(
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
            latitude = self._host._coordinate(vin, "latitude")
            longitude = self._host._coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        zone = self._host._zone_name(latitude, longitude)
        if zone:
            return place(zone=zone)
        address = None
        if self._host.geocoder is not None:
            address = await self._host.geocoder.resolve(latitude, longitude)
        return place(address=address) if address else place()

    def trip_soc_end(self, vin: str, started: datetime) -> Optional[float]:
        """The SoC at the end of a drive; on a hybrid, only if read during it.

        Issue #25's X3 30e never streams its SoC (it arrives only with a REST
        catch-up), so the reading from before a drive was still the latest at its
        end, and every trip was stored as a battery that never moved: "38 → 38 %".
        Only on a plug-in hybrid, though. A battery car streams its SoC on
        change, so no reading during a short hop means it genuinely didn't drop
        a whole percent -- about half the i5's trips -- and that end is true.
        """

        if self._host.is_plug_in_hybrid(vin):
            tracking = self._host._peek(vin).charge.tracking
            if tracking is None or tracking.last_update is None or tracking.last_update <= started:
                return None
        return self._host._current_soc(vin)

    def trip_energy_kwh(
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
        capacity = self._host.battery_capacity_kwh(vin)
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
            state = self._host.get_state(vin, descriptor)
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
