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
from datetime import datetime, timezone
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from .models import SCHEMA_VERSION, ChargingSession, merge_session, prune_sessions
from .trips import Trip, merge_trip, prune_trips

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# Sessions end minutes apart at worst, so a generous debounce costs nothing and
# guarantees we never write from the hot stream path.
SAVE_DELAY_S = 30
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
    ) -> None:
        self.hass = hass
        self.retain_months = retain_months
        self.max_sessions = max_sessions
        self.max_trips = max_trips
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}_{entry_id}_history")
        self._sessions: dict[str, list[ChargingSession]] = {}
        self._trips: dict[str, list[Trip]] = {}
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
                for session in (
                    ChargingSession.from_dict(item) for item in raw_sessions or []
                )
                if session is not None
            ]
            if restored:
                self._sessions[vin] = self._prune(restored)

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
                vin: [trip.to_dict() for trip in trips]
                for vin, trips in self._trips.items()
            },
        }

    async def async_save_now(self) -> None:
        """Flush immediately -- used on unload so a pending debounce isn't lost."""

        if self._loaded:
            await self._store.async_save(self._data_to_save())

    async def async_clear(self) -> None:
        """Delete all stored history (the user-facing 'Delete all history')."""

        self._sessions = {}
        self._trips = {}
        await self._store.async_remove()
