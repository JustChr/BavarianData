"""Persistence for the history layer.

The only module in ``history/`` that imports Home Assistant -- everything it
calls into (record shape, retention, cost) lives in the HA-free siblings so it
stays unit-testable.

History deliberately does *not* live in the recorder: the recorder purges after
ten days by default, and charging history is worth keeping for years. This is a
small JSON document maintained by the same ``Store`` helper the vehicle-image
cache and request log already use.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..debug import debug_enabled
from .cardata_history import (
    CostFn,
    ZoneFn,
    merge_cardata_sessions,
    session_from_cardata,
)
from .sessions import drop_house_figures_elsewhere
from .models import SCHEMA_VERSION, ChargingSession, merge_session, prune_sessions
from .fuel import CHANGED_REFUEL
from .fuel import note_reading as note_fuel_reading
from .fuel import report as fuel_report
from .odometer import km_per_day, note_reading
from .trips import Trip, merge_trip, prune_trips

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# Sessions end minutes apart at worst, so a generous debounce costs nothing and
# guarantees we never write from the hot stream path.
SAVE_DELAY_S = 30
# How often a plain tank reading is worth a save (see ``async_note_fuel``).
FUEL_SAVE_INTERVAL = timedelta(minutes=15)
DEFAULT_RETAIN_MONTHS: Optional[int] = 24
# Backstop against unbounded growth even with retention set to "forever".
MAX_SESSIONS_PER_VIN = 2000
# Trips are far more frequent than charges (several a day vs a few a week), so
# their cap is higher for the same "forever" retention to remain useful.
MAX_TRIPS_PER_VIN = 5000


class HistoryStore:
    """In-memory history for one config entry, persisted lazily."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        retain_months: Optional[int] = DEFAULT_RETAIN_MONTHS,
        max_sessions: int = MAX_SESSIONS_PER_VIN,
        max_trips: int = MAX_TRIPS_PER_VIN,
        remember_locations: bool = False,
    ) -> None:
        self.hass = hass
        self.retain_months = retain_months
        # *Remember charging locations*: whether a charge keeps the coordinates
        # it was made at. Off by default -- it is the one charging setting that
        # writes raw positions to disk (see ``forget_locations``).
        self.remember_locations = remember_locations
        self.max_sessions = max_sessions
        self.max_trips = max_trips
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}_{entry_id}_history")
        self._sessions: dict[str, list[ChargingSession]] = {}
        self._trips: dict[str, list[Trip]] = {}
        # In-progress charges, one per VIN: the snapshot of a session that had
        # not closed yet. Kept out of ``_sessions`` on purpose -- nothing that
        # sums, exports or backfills history may see a record that is still
        # moving. The coordinator drains this at startup.
        self._open_sessions: dict[str, dict[str, Any]] = {}
        # The last odometer reading announced as a vehicle report, one per VIN
        # (``vehicle_report.py``). Persisted because a restart's REST catch-up
        # replays the car's last values, and only a record that survives the
        # restart can tell that replay from a new report.
        self._reports: dict[str, dict[str, Any]] = {}
        # One odometer reading per local day, per VIN (``odometer.py``): the
        # car's pace for projected service dates and the mileage overview.
        self._odometer: dict[str, dict[str, float]] = {}
        # The tank's last reading and its refuels, per VIN (``fuel.py``): the
        # Fuel view of a petrol or diesel car.
        self._fuel: dict[str, dict[str, Any]] = {}
        self._fuel_saved_at: dict[str, datetime] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Read the store once at setup. Never raises -- history is not critical."""

        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a corrupt store must not block setup
            _LOGGER.exception("Could not read charging history; starting empty")
            data = None

        self._loaded = True
        if not data:
            return

        schema = data.get("schema", 0)
        if schema > SCHEMA_VERSION:
            # Written by a newer version of the integration (a downgrade).
            # Refusing to touch it means an upgrade back doesn't find it mangled.
            _LOGGER.warning(
                "Charging history was written by a newer version (schema %s > %s); "
                "leaving it untouched and starting empty",
                schema,
                SCHEMA_VERSION,
            )
            return

        for vin, raw_sessions in (data.get("sessions") or {}).items():
            restored = [
                session
                for session in (ChargingSession.from_dict(item) for item in raw_sessions or [])
                if session is not None
            ]
            if restored:
                self._sessions[vin] = self._prune(restored)

        # ``open_sessions`` is absent in every store written before in-progress
        # charges were snapshotted; like ``trips``, a plain ``.get`` is the whole
        # migration. Kept additive for the same reason the trip track was: a
        # schema bump makes an older build refuse the entire document.
        for vin, snapshot in (data.get("open_sessions") or {}).items():
            if isinstance(snapshot, dict):
                self._open_sessions[vin] = snapshot

        # Additive, like ``open_sessions``: absent in every older store.
        for vin, report in (data.get("reports") or {}).items():
            if isinstance(report, dict):
                self._reports[vin] = report

        # Additive as well. Seeded from the charging sessions' odometer, so a
        # store written before the log existed starts with its charges' readings
        # rather than empty -- a pace needs four weeks of span.
        for vin, raw_log in (data.get("odometer") or {}).items():
            if isinstance(raw_log, dict):
                self._odometer[vin] = {
                    str(day): float(km)
                    for day, km in raw_log.items()
                    if isinstance(km, (int, float))
                }
        for vin, sessions in self._sessions.items():
            log = self._odometer.setdefault(vin, {})
            for session in sessions:
                stamp = session.end or session.start
                if session.mileage_km is not None and stamp is not None:
                    note_reading(log, dt_util.as_local(stamp).date(), session.mileage_km)
            if not log:
                del self._odometer[vin]

        # Additive, like ``odometer``: absent in every older store.
        for vin, raw_fuel in (data.get("fuel") or {}).items():
            if isinstance(raw_fuel, dict):
                self._fuel[vin] = raw_fuel

        # ``trips`` is absent in schema-1 stores; a plain ``.get`` handles the
        # upgrade with no migration step.
        for vin, raw_trips in (data.get("trips") or {}).items():
            restored_trips = [
                trip
                for trip in (Trip.from_dict(item) for item in raw_trips or [])
                if trip is not None
            ]
            if restored_trips:
                self._trips[vin] = self._prune_trips(restored_trips)

        if debug_enabled():
            # Confirms the store survived a restart, and how much of it did --
            # the one thing a purely in-memory bug would look identical to.
            _LOGGER.debug(
                "[history] restored schema=%s sessions=%s trips=%s open=%s (retain=%s months)",
                schema,
                {vin: len(items) for vin, items in self._sessions.items()},
                {vin: len(items) for vin, items in self._trips.items()},
                list(self._open_sessions),
                self.retain_months,
            )

    @callback
    def reresolve_zones(self, zone_fn: ZoneFn) -> int:
        """Fix legacy imported sessions that stored raw coordinates but no zone.

        Older builds imported charging history before the import path resolved
        zones, leaving records like ``{"zone": None, "lat": .., "lon": ..}`` --
        which the card shows as "public" even for a charge at Home. Re-run the
        zone lookup locally (no BMW request, no quota) and, on a hit, replace the
        record with a plain ``{"zone": name}`` -- which also drops the stored
        coordinates, restoring the "coordinates are never persisted" invariant.

        A miss is left untouched (coordinates and any BMW address kept) so a
        genuinely-public charge still shows its address and a lookup that ran
        before zones finished loading is retried on the next start. Idempotent.
        """

        if zone_fn is None:
            return 0
        fixed = 0
        for sessions in self._sessions.values():
            for session in sessions:
                loc = session.location
                if not isinstance(loc, dict) or loc.get("zone"):
                    continue
                lat = loc.get("lat")
                lon = loc.get("lon")
                if lat is None or lon is None:
                    continue
                zone = zone_fn(lat, lon)
                if zone:
                    session.location = {"zone": zone}
                    if self.remember_locations:
                        session.location.update(lat=lat, lon=lon)
                    fixed += 1
        if fixed:
            if debug_enabled():
                _LOGGER.debug("[history] re-resolved %s legacy charge location(s) to a zone", fixed)
            self.async_schedule_save()
        return fixed

    @callback
    def forget_locations(self) -> int:
        """Strip stored coordinates from every charge, open ones included.

        What turning *Remember charging locations* off means, and run at every
        setup while it is off -- which also clears coordinates an older build's
        imports left behind. Run after :meth:`reresolve_zones`, which needs
        them once more to name a legacy record's zone. Zones, addresses and
        operators stay: they are what the places list groups by without a map.
        Returns how many records changed; idempotent.
        """

        def strip(location: Any) -> Any:
            if not isinstance(location, dict) or ("lat" not in location and "lon" not in location):
                return None
            return {k: v for k, v in location.items() if k not in ("lat", "lon")}

        changed = 0
        for sessions in self._sessions.values():
            for session in sessions:
                stripped = strip(session.location)
                if stripped is not None:
                    session.location = stripped
                    changed += 1
        for snapshot in self._open_sessions.values():
            stripped = strip(snapshot.get("location"))
            if stripped is not None:
                snapshot["location"] = stripped
                changed += 1
        if changed:
            if debug_enabled():
                _LOGGER.debug("[history] removed coordinates from %s charge(s)", changed)
            self.async_schedule_save()
        return changed

    @callback
    def drop_house_figures_elsewhere(self) -> int:
        """Strip the home mix and tariff cost from stored charges made elsewhere.

        Before #97 a charge at a public charger was given the house's energy
        mix and billed at the home tariff. Run after :meth:`reresolve_zones`,
        so a legacy import that now resolves to Home is judged as Home. The
        rule is :func:`sessions.drop_house_figures_elsewhere`. Idempotent.
        """

        fixed = sum(
            drop_house_figures_elsewhere(session)
            for sessions in self._sessions.values()
            for session in sessions
        )
        if fixed:
            if debug_enabled():
                _LOGGER.debug("[history] cleared house mix/cost from %s away charge(s)", fixed)
            self.async_schedule_save()
        return fixed

    def _prune(self, sessions: list[ChargingSession]) -> list[ChargingSession]:
        return prune_sessions(
            sessions,
            now=datetime.now(timezone.utc),
            retain_months=self.retain_months,
            max_entries=self.max_sessions,
        )

    def _prune_trips(self, trips: list[Trip]) -> list[Trip]:
        return prune_trips(
            trips,
            now=datetime.now(timezone.utc),
            retain_months=self.retain_months,
            max_entries=self.max_trips,
        )

    def sessions(
        self,
        vin: Optional[str] = None,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[ChargingSession]:
        """Recorded sessions, newest first, optionally filtered."""

        if vin is not None:
            found = list(self._sessions.get(vin, []))
        else:
            found = [item for group in self._sessions.values() for item in group]
            found.sort(key=lambda item: item.start, reverse=True)

        if start is not None:
            found = [item for item in found if item.start >= start]
        if end is not None:
            found = [item for item in found if item.start <= end]
        if limit is not None:
            found = found[:limit]
        return found

    def add_session(self, session: ChargingSession) -> None:
        """Store a finished session (replacing one with the same id) and save."""

        existing = self._sessions.get(session.vin, [])
        merged = merge_session(existing, session)
        self._sessions[session.vin] = prune_sessions(
            merged,
            now=session.end or session.start,
            retain_months=self.retain_months,
            max_entries=self.max_sessions,
        )
        self.async_schedule_save()

    def open_sessions(self) -> dict[str, dict[str, Any]]:
        """The snapshotted in-progress charges, by VIN."""

        return dict(self._open_sessions)

    @callback
    def async_set_open_session(self, vin: str, snapshot: dict[str, Any]) -> None:
        """Record (or refresh) the in-progress charge for one VIN."""

        self._open_sessions[vin] = snapshot
        self.async_schedule_save()

    @callback
    def async_clear_open_session(self, vin: str) -> bool:
        """Forget the in-progress charge for one VIN; True if there was one.

        Called when the session closes -- by then it is a real record, and
        leaving the snapshot behind would resurrect it as a duplicate on the
        next start.
        """

        if self._open_sessions.pop(vin, None) is None:
            return False
        self.async_schedule_save()
        return True

    def last_report(self, vin: str) -> Optional[dict[str, Any]]:
        """The last odometer reading announced for one VIN, or ``None``."""

        report = self._reports.get(vin)
        return dict(report) if report is not None else None

    @callback
    def async_set_last_report(self, vin: str, report: dict[str, Any]) -> None:
        """Record the reading just announced (or seeded) for one VIN."""

        self._reports[vin] = dict(report)
        self.async_schedule_save()

    def odometer_log(self, vin: str) -> dict[str, float]:
        """The daily odometer log for one VIN (a copy), ``{"YYYY-MM-DD": km}``."""

        return dict(self._odometer.get(vin, {}))

    @callback
    def async_note_odometer(self, vin: str, day: date, km: float) -> None:
        """Record an odometer reading for ``day``; saved only when it changed."""

        if note_reading(self._odometer.setdefault(vin, {}), day, km):
            self.async_schedule_save()

    @callback
    def async_note_fuel(
        self,
        vin: str,
        when: datetime,
        *,
        litres: Optional[float],
        percent: Optional[float],
        km: Optional[float],
    ) -> None:
        """Feed one tank reading to the fuel log.

        A refuel is saved at once (debounced like everything else); a plain
        reading only every :data:`FUEL_SAVE_INTERVAL`, because the tank reports
        every few minutes while driving and the last reading only matters as
        the "before" of the next refuel.
        """

        changed = note_fuel_reading(
            self._fuel.setdefault(vin, {}), when, litres=litres, percent=percent, km=km
        )
        if changed is None:
            return
        now = datetime.now(timezone.utc)
        last_saved = self._fuel_saved_at.get(vin)
        if (
            changed == CHANGED_REFUEL
            or last_saved is None
            or now - last_saved >= FUEL_SAVE_INTERVAL
        ):
            self._fuel_saved_at[vin] = now
            self.async_schedule_save()

    def fuel_report(self, vin: str, now: datetime) -> dict[str, Any]:
        """Refuels and consumption for one VIN (``fuel.report``)."""

        return fuel_report(self._fuel.get(vin, {}), now)

    def km_per_day(self, vin: str, today: date) -> Optional[float]:
        """The car's pace over the last 90 days, or ``None`` until it is known."""

        return km_per_day(self._odometer.get(vin, {}), today)

    def import_cardata_sessions(
        self,
        vin: str,
        raw_sessions: Any,
        *,
        cost_fn: Optional[CostFn] = None,
        zone_fn: Optional[ZoneFn] = None,
        home_zone: Optional[str] = None,
    ) -> tuple[int, int]:
        """Import BMW's REST charging history, returning (added, updated).

        Overlapping live-recorded sessions are enriched in place (grid energy,
        SoC, odometer, location, power curve, cost) rather than duplicated;
        genuinely new charges -- pre-install or from while HA was down -- are
        added. Saves only when something changed.
        """

        incoming = [
            session
            for session in (
                session_from_cardata(
                    vin,
                    raw,
                    cost_fn=cost_fn,
                    zone_fn=zone_fn,
                    home_zone=home_zone,
                    keep_coordinates=self.remember_locations,
                )
                for raw in (raw_sessions or [])
            )
            if session is not None
        ]
        if not incoming:
            return (0, 0)

        merged, added, updated = merge_cardata_sessions(self._sessions.get(vin, []), incoming)
        if not (added or updated):
            return (0, 0)
        self._sessions[vin] = prune_sessions(
            merged,
            now=datetime.now(timezone.utc),
            retain_months=self.retain_months,
            max_entries=self.max_sessions,
        )
        self.async_schedule_save()
        return (added, updated)

    def trips(
        self,
        vin: Optional[str] = None,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[Trip]:
        """Recorded trips, newest first, optionally filtered by VIN / date range."""

        if vin is not None:
            found = list(self._trips.get(vin, []))
        else:
            found = [item for group in self._trips.values() for item in group]
            found.sort(key=lambda item: item.start, reverse=True)

        if start is not None:
            found = [item for item in found if item.start >= start]
        if end is not None:
            found = [item for item in found if item.start <= end]
        if limit is not None:
            found = found[:limit]
        return found

    def find_trip(self, vin: str, trip_id: str) -> Optional[Trip]:
        """A single stored trip by id, for the reclassification service."""

        for trip in self._trips.get(vin, []):
            if trip.id == trip_id:
                return trip
        return None

    def add_trip(self, trip: Trip) -> None:
        """Store a finished trip (replacing one with the same id) and save."""

        existing = self._trips.get(trip.vin, [])
        merged = merge_trip(existing, trip)
        self._trips[trip.vin] = prune_trips(
            merged,
            now=trip.end or trip.start,
            retain_months=self.retain_months,
            max_entries=self.max_trips,
        )
        self.async_schedule_save()

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_S)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "sessions": {
                vin: [session.to_dict() for session in sessions]
                for vin, sessions in self._sessions.items()
            },
            "trips": {
                vin: [trip.to_dict() for trip in trips] for vin, trips in self._trips.items()
            },
            "open_sessions": dict(self._open_sessions),
            "reports": dict(self._reports),
            "odometer": {vin: dict(log) for vin, log in self._odometer.items()},
            "fuel": {vin: dict(log) for vin, log in self._fuel.items()},
        }

    async def async_save_now(self) -> None:
        """Flush immediately -- used on unload so a pending debounce isn't lost."""

        if self._loaded:
            await self._store.async_save(self._data_to_save())

    def forget_vehicle(self, vin: str) -> None:
        """Drop everything recorded for one car (its device was deleted)."""

        for records in (
            self._sessions,
            self._trips,
            self._open_sessions,
            self._reports,
            self._odometer,
            self._fuel,
        ):
            records.pop(vin, None)
        self.async_schedule_save()

    async def async_clear(self) -> None:
        """Delete all stored history (the user-facing 'Delete all history')."""

        self._sessions = {}
        self._trips = {}
        self._open_sessions = {}
        # Emptied with the rest: the next reading then only seeds the record,
        # so deleting history can never announce a report that is a replay.
        self._reports = {}
        self._odometer = {}
        self._fuel = {}
        await self._store.async_remove()
