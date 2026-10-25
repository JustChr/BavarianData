"""Vehicle reports: the odometer's new reading as an event, and as a trip.

A car that reports only once parked (an end-of-trip car) sends a burst of new
readings when it stops. The odometer's new value, settled for a quiet
``REPORT_QUIET_S``, fires ``EVENT_VEHICLE_REPORT``; on a car whose trips are
built from reports (``TripSettings.report_trips``) it also becomes a trip
from the previous report to this one, dated by a start mark when there is
one, and routed by the phone's positions when the car takes its route from a
phone (``phone_route.py``). The rules are ``vehicle_report.py`` (HA-free,
fully tested); this applies them to the record and the car through its host.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .car_signals import DESC_FUEL_L, DESC_GPS_HEADING, DESC_GPS_LAT, DESC_GPS_LON
from .car_state import Vehicle
from .const import EVENT_VEHICLE_REPORT
from .debug import mask_vin
from .history.trip_builder import TripBuilder
from .phone_route import PhoneRoute
from .trip_capture import TripLog
from .trip_settings import TripSettings
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

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")


class ReportHost(Protocol):
    """What the reports read and do, from the coordinator."""

    hass: Any
    entry_id: str
    history: Any
    data: Dict[str, Dict[str, Any]]
    names: Dict[str, str]
    trip_log: TripLog
    trip_settings: TripSettings
    vehicle_trip_settings: Dict[str, TripSettings]

    @property
    def _cars(self) -> Dict[str, Vehicle]: ...
    def _car(self, vin: str) -> Vehicle: ...
    def _peek(self, vin: str) -> Vehicle: ...
    def _numeric(self, vin: str, descriptor: str) -> Optional[float]: ...
    def _coordinate(self, vin: str, kind: str) -> Optional[float]: ...
    def _current_soc(self, vin: str) -> Optional[float]: ...
    def _odometer_timestamp(self, vin: str) -> Optional[str]: ...
    def odometer_km(self, vin: str) -> Optional[float]: ...
    def is_plug_in_hybrid(self, vin: str) -> bool: ...
    def report_trips(self, vin: str) -> bool: ...
    def trip_settings_for(self, vin: str) -> TripSettings: ...
    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None: ...

    trips: Any  # vehicle_trips.VehicleTrips: resolve_place, trip_energy_kwh, store_trip


class VehicleReports:
    """The vehicle-report event, report trips, and the phone's route for them."""

    def __init__(self, host: ReportHost) -> None:
        self._host = host
        # The one state listener feeding the cars' phone routes.
        self._phone_unsub: Optional[Callable[[], None]] = None

    def note_odometer(
        self, vin: str, *, parked_soc: Optional[tuple[float, Optional[datetime]]] = None
    ) -> None:
        """Arm the vehicle-report debounce when the odometer reached a new reading.

        Re-armed only by a *new* value: the heartbeat repeats of an unchanged
        reading must not hold the event back, and must never fire it either
        (``vehicle_report.py``).
        """

        if self._host.history is None:
            return
        odometer_km = self._host.odometer_km(vin)
        last = self._host.history.last_report(vin)
        decision = self._report_decision(vin, odometer_km)
        if odometer_km is None:  # IGNORE
            return
        if decision.kind == SEED:
            self._host.history.async_set_last_report(
                vin, self._report_record(vin, odometer_km, starts=(last or {}).get("starts"))
            )
            if self._host.report_trips(vin):
                # Where the car stands now is where its first trip will start.
                self._host.hass.async_create_task(self._remember_report_place(vin, odometer_km))
            return
        if decision.kind == CANDIDATE:
            # Kept beside the record, never in it: see ``vehicle_report.py``.
            self._host.history.async_set_last_report(
                vin, {**(last or {}), "candidate": self._report_record(vin, odometer_km)}
            )
            return
        if decision.kind == ARM and parked_soc is not None and "soc_parked" not in (last or {}):
            # The first new reading of a drive: what the car stood with before
            # it. Kept in the record, so a restart's replay of the burst can't
            # overwrite it with the SoC the car arrived with.
            soc, soc_at = parked_soc
            self._host.history.async_set_last_report(
                vin,
                {
                    **(last or {}),
                    "soc_parked": soc,
                    "soc_parked_at": soc_at.isoformat() if soc_at else None,
                },
            )
        if decision.kind != ARM or self._host._peek(vin).report.pending == odometer_km:
            return
        self.cancel_report_timer(vin)
        self._host._car(vin).report.pending = odometer_km

        @callback
        def _fire(_now: datetime) -> None:
            self._host._peek(vin).report.take("timer")
            armed = self._host._peek(vin).report.take("pending")
            # A newer reading would have re-armed the timer; anything else is a
            # value that has gone away again, and is not announced.
            if armed is None or self._host.odometer_km(vin) != armed:
                return
            # Decided again, against the record as it is now: what the report
            # is measured from may have changed while it held.
            decision = self._report_decision(vin, armed)
            if decision.kind == ARM and decision.baseline is not None:
                self._fire_vehicle_report(vin, armed, decision.baseline)

        self._host._car(vin).report.timer = async_call_later(self._host.hass, REPORT_QUIET_S, _fire)

    def _report_decision(self, vin: str, odometer_km: Optional[float]) -> ReportDecision:
        return report_decision(
            odometer_km,
            self._host.history.last_report(vin) if self._host.history is not None else None,
            timestamp=self._host._odometer_timestamp(vin),
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
            "timestamp": self._host._odometer_timestamp(vin),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "fuel_l": self._host._numeric(vin, DESC_FUEL_L),
            "soc": self._host._current_soc(vin),
        }
        if starts:
            record["starts"] = list(starts)
        return record

    @callback
    def mark_trip_start(self, vin: str, at: datetime) -> None:
        """Note that a drive began at ``at`` (``bavariandata.mark_trip_start``).

        Kept with the report record, so a restart before the car reports loses
        nothing; the report that ends the drive spends it (``vehicle_report.py``).
        """

        if self._host.history is None:
            return
        self._host.history.async_set_last_report(
            vin, add_start_mark(self._host.history.last_report(vin), at)
        )
        self._host.trip_log.cap("[trip.report] %s start marked at %s", vin, at.isoformat())

    def watch_phone_routes(self) -> None:
        """Listen to every phone and in-car sensor a car's trip settings name.

        Called whenever the trip settings are (re)read. Every car starts over,
        since its in-car sensor -- what decides which positions are held -- may
        be a different one now.
        """

        self.stop_watching_phone_routes()
        for car in self._host._cars.values():
            car.report.phone_route = None
        entities = sorted(
            {
                entity_id
                for settings in (
                    self._host.trip_settings,
                    *self._host.vehicle_trip_settings.values(),
                )
                if settings.phone_route
                for entity_id in (settings.phone_tracker, settings.phone_in_car)
                if entity_id
            }
        )
        if entities:
            self._phone_unsub = async_track_state_change_event(
                self._host.hass, entities, self._phone_state_changed
            )

    def stop_watching_phone_routes(self) -> None:
        if self._phone_unsub is not None:
            self._phone_unsub()
            self._phone_unsub = None

    def _phone_route(self, vin: str, settings: TripSettings, in_car_state: Any) -> PhoneRoute:
        route = self._host._peek(vin).report.phone_route
        if route is None:
            route = PhoneRoute(gated=bool(settings.phone_in_car))
            if settings.phone_in_car:
                if in_car_state is None:
                    in_car_state = self._host.hass.states.get(settings.phone_in_car)
                route.in_car = in_car_state is not None and in_car_state.state == "on"
            self._host._car(vin).report.phone_route = route
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
            for vin in sorted(
                {*self._host.data, *self._host.names, *self._host.vehicle_trip_settings}
            ):
                settings = self._host.trip_settings_for(vin)
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
                        tracker = self._host.hass.states.get(settings.phone_tracker)
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
            self._host.trip_log.cap(
                "[trip.phone] %s %.5f,%.5f acc=%s", vin, latitude, longitude, accuracy
            )

    async def _remember_report_place(self, vin: str, odometer_km: float) -> None:
        """Add where the car stands to the record of the reading it just gave.

        The next report trip starts there. Written only while the record is still
        that reading's: a newer one may have landed during the geocode.
        """

        try:
            resolved = await self._host.trips.resolve_place(vin)
            record = self._host.history.last_report(vin) if self._host.history is not None else None
            if resolved is None or record is None or record.get("odometer_km") != odometer_km:
                return
            self._host.history.async_set_last_report(vin, {**record, "place": resolved})
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not place the vehicle report for %s", mask_vin(vin))

    def cancel_report_timer(self, vin: str) -> None:
        cancel = self._host._peek(vin).report.take("timer")
        if cancel is not None:
            cancel()
        self._host._peek(vin).report.take("pending")

    def _fire_vehicle_report(
        self, vin: str, odometer_km: float, baseline: Mapping[str, Any]
    ) -> None:
        if self._host.history is None:
            return
        timestamp = self._host._odometer_timestamp(vin)
        record = self._host.history.last_report(vin) or {}
        starts = record.get("starts")
        parked = {key: record.get(key) for key in ("soc_parked", "soc_parked_at")}
        start: Optional[datetime] = None
        end_at = dt_util.parse_datetime(timestamp) if timestamp else None
        if end_at is None:
            end_at = datetime.now(timezone.utc)
        if self._host.report_trips(vin):
            start, starts = take_start_mark(
                starts, previous_at=report_time(baseline), end_at=end_at
            )
        payload = report_payload(
            vin,
            odometer_km=odometer_km,
            timestamp=timestamp,
            last=baseline,
            values={
                "fuel_l": self._host._numeric(vin, DESC_FUEL_L),
                "fuel_percent": self._host._numeric(vin, "vehicle.drivetrain.fuelSystem.level"),
                "range_km": self._host._numeric(vin, "vehicle.drivetrain.lastRemainingRange"),
                "soc_percent": self._host._numeric(
                    vin, "vehicle.drivetrain.batteryManagement.header"
                ),
                "latitude": self._host._numeric(vin, DESC_GPS_LAT),
                "longitude": self._host._numeric(vin, DESC_GPS_LON),
                "altitude_m": self._host._numeric(
                    vin, "vehicle.cabin.infotainment.navigation.currentLocation.altitude"
                ),
                "heading": self._host._numeric(vin, DESC_GPS_HEADING),
            },
        )
        payload["entry_id"] = self._host.entry_id
        # Recorded before firing: an automation that raises must not leave the
        # reading unrecorded, to be announced a second time. A fresh record
        # also drops any candidate: this reading has settled the question.
        self._host.history.async_set_last_report(
            vin, self._report_record(vin, odometer_km, starts=starts)
        )
        self._host._fire(EVENT_VEHICLE_REPORT, payload)
        if self._host.report_trips(vin):
            self._host.hass.async_create_task(
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
            end_place = await self._host.trips.resolve_place(vin)
            record = self._host.history.last_report(vin) if self._host.history is not None else None
            if end_place is not None and record and record.get("odometer_km") == odometer_km:
                self._host.history.async_set_last_report(vin, {**record, "place": end_place})
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
            soc_end = self._host._current_soc(vin)
            if self._host.is_plug_in_hybrid(vin):
                # As on a live trip (``_trip_soc_end``): a hybrid that sent no
                # SoC with its burst still shows the one it set off with, and
                # "38 -> 38 %" would claim the battery never moved.
                tracking = self._host._peek(vin).charge.tracking
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
                energy_kwh=self._host.trips.trip_energy_kwh(vin, soc_start, soc_end),
                stats=fuel_stats(baseline.get("fuel_l"), self._host._numeric(vin, DESC_FUEL_L)),
                hybrid=self._host.is_plug_in_hybrid(vin),
            )
            trip.duration_unknown = start is None
            if route:
                trip.track_source = "phone"
            self._host.trip_log.cap(
                "[trip.report] %s %s -> %s dist=%s start=%s phone_points=%s",
                vin,
                (trip.start_place or {}).get("label"),
                (trip.end_place or {}).get("label"),
                trip.distance_km,
                "marked" if start is not None else "unknown",
                len(route),
            )
            self._host.trips.store_trip(vin, trip, start_zone=None, left_fired=False, announce=True)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not record the report trip for %s", mask_vin(vin))

    def _phone_report_route(
        self, vin: str, start: Optional[datetime], end_at: datetime
    ) -> list[tuple[datetime, float, float]]:
        """The phone's route for a report trip, ending where the car parked.

        Empty when the car takes no route from a phone, or the phone's does not
        fit the trip (``phone_route.py``).
        """

        route = self._host._peek(vin).report.phone_route
        if route is None or not self._host.trip_settings_for(vin).phone_route:
            return []
        latitude = self._host._coordinate(vin, "latitude")
        longitude = self._host._coordinate(vin, "longitude")
        end_fix = (latitude, longitude) if latitude is not None and longitude is not None else None
        points = route.take(start, end_at, end_fix)
        if not points or end_fix is None:
            return []
        # The car's own fix closes the line: it is where the trip ends.
        return [*points, (end_at, *end_fix)]
