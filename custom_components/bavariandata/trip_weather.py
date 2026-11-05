"""When a trip's weather is looked up, and where it is written.

The trip engine hands this one the drive's fixes (:meth:`TripWeather.note_fix`)
and, once the trip is stored, its end (:meth:`TripWeather.trip_stored`). The
waypoints live on the car's trip state only while the drive is open; after the
close they go to Open-Meteo once (``route_weather.RouteWeather``), and the
answer is written onto the stored trip. A failed lookup is tried once more
:data:`RETRY_S` later; after that the waypoints are dropped, since they are the
one thing never kept.

Trips without a lookup of their own -- stored before the switch was turned on,
built from a vehicle report, or whose lookup failed twice -- are filled in by
:meth:`TripWeather.async_backfill`, a few at a time: along their stored route
where they have one, else at their two ends where those are zones, else at the
home location. Each record says which (``source``).

Reaches the coordinator only through :class:`WeatherHost`, and imports the same
few HA helpers the other engines do, so it runs under ``tests/fake_ha.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional, Protocol, Sequence

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .car_state import Vehicle
from .debug import mask_vin
from .route_weather import (
    RouteWeather,
    WaypointSampler,
    WeatherPoint,
    points_from_ends,
    points_from_track,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .history.store import HistoryStore
    from .history.trips import Trip

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")

# A failed lookup is tried once more after this long.
RETRY_S = 900
# A trip is left to its own lookup this long before the backfill takes it.
BACKFILL_AFTER = timedelta(minutes=30)
# Open-Meteo's archive of past forecasts reaches back to 2022; older trips stay empty.
BACKFILL_OLDEST = datetime(2022, 1, 1, tzinfo=timezone.utc)
# Trips one backfill pass asks for, and the pause before the next pass.
BACKFILL_BATCH = 10
BACKFILL_PAUSE_S = 120


class WeatherHost(Protocol):
    """What the weather lookup reads and does, from the coordinator."""

    hass: HomeAssistant
    history: Optional[HistoryStore]
    route_weather: Optional[RouteWeather]

    @property
    def signal_trips(self) -> str: ...

    def _car(self, vin: str) -> Vehicle: ...
    def _peek(self, vin: str) -> Vehicle: ...
    def _zone_location(self, name: str) -> Optional[tuple[float, float]]: ...
    def _home_location(self) -> Optional[tuple[float, float]]: ...


class TripWeather:
    """Collects a drive's waypoints and writes the weather onto its trip."""

    def __init__(self, host: WeatherHost) -> None:
        self._host = host
        self._backfill_timer: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        client = self._host.route_weather
        return client is not None and client.enabled

    # --- during the drive -----------------------------------------------------

    def trip_opened(self, vin: str) -> None:
        self._host._car(vin).trip.waypoints = WaypointSampler() if self.enabled else None

    def note_fix(self, vin: str, latitude: float, longitude: float, at: datetime) -> None:
        sampler = self._host._peek(vin).trip.waypoints
        if sampler is not None:
            sampler.add(latitude, longitude, at)

    def take_waypoints(self, vin: str) -> Optional[WaypointSampler]:
        sampler: Optional[WaypointSampler] = self._host._peek(vin).trip.take("waypoints")
        return sampler

    # --- after the close ------------------------------------------------------

    def trip_stored(
        self,
        vin: str,
        trip: Trip,
        sampler: Optional[WaypointSampler],
        end: Optional[tuple[float, float]],
    ) -> None:
        """Look the weather up for a trip just stored, from its own waypoints."""

        if sampler is None or not self.enabled or self._host.history is None:
            return
        if self._host.history.find_trip(vin, trip.id) is None:
            return  # dropped as noise
        lat, lon = end if end is not None else (None, None)
        points = sampler.finish(lat, lon, trip.end or trip.start)
        if points:
            self._host.hass.async_create_task(
                self._look_up(vin, trip.id, trip.start, points, "route", retry=True)
            )

    async def _look_up(
        self,
        vin: str,
        trip_id: str,
        start: datetime,
        points: Sequence[WeatherPoint],
        source: str,
        *,
        retry: bool,
    ) -> bool:
        client = self._host.route_weather
        if client is None or not client.enabled:
            return False
        weather = await client.lookup(points, start, source=source, now=datetime.now(timezone.utc))
        if weather is None:
            if retry:
                held = list(points)

                # @callback keeps the timer on the loop; without it HA runs it in
                # the executor, where async_create_task refuses.
                @callback
                def _again(_now: datetime) -> None:
                    self._host.hass.async_create_task(
                        self._look_up(vin, trip_id, start, held, source, retry=False)
                    )

                async_call_later(self._host.hass, RETRY_S, _again)
            return False
        return self._write(vin, trip_id, weather)

    def _write(self, vin: str, trip_id: str, weather: dict[str, Any]) -> bool:
        history = self._host.history
        if history is None:
            return False
        trip = history.find_trip(vin, trip_id)
        if trip is None:
            return False
        trip.weather = weather
        try:
            history.add_trip(trip)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not store the weather of a trip for %s", mask_vin(vin))
            return False
        async_dispatcher_send(self._host.hass, self._host.signal_trips, vin)
        return True

    # --- trips without a lookup of their own ----------------------------------

    def schedule_backfill(self, delay_s: float = BACKFILL_PAUSE_S) -> None:
        """Fill in older trips soon, unless a pass is already due."""

        if not self.enabled or self._backfill_timer is not None:
            return

        @callback
        def _run(_now: datetime) -> None:
            self._backfill_timer = None
            self._host.hass.async_create_task(self.async_backfill())

        self._backfill_timer = async_call_later(self._host.hass, delay_s, _run)

    def cancel_backfill(self) -> None:
        if self._backfill_timer is not None:
            self._backfill_timer()
            self._backfill_timer = None

    def _waiting(self, now: datetime) -> list[Trip]:
        history = self._host.history
        if history is None:
            return []
        return [
            trip
            for trip in history.trips()
            if trip.weather is None
            and trip.start >= BACKFILL_OLDEST
            and (trip.end or trip.start) <= now - BACKFILL_AFTER
        ]

    def _points_for(self, trip: Trip) -> tuple[list[WeatherPoint], str]:
        if trip.track:
            points = points_from_track(trip.track, trip.start, trip.end)
            if points:
                return points, "track"
        ends = [
            self._host._zone_location(place["zone"])
            if isinstance(place, dict) and place.get("zone")
            else None
            for place in (trip.start_place, trip.end_place)
        ]
        if ends[0] is not None or ends[1] is not None:
            return points_from_ends(ends[0], ends[1], trip.start, trip.end), "ends"
        home = self._host._home_location()
        if home is not None:
            return points_from_ends(home, None, trip.start, None), "home"
        return [], "home"

    async def async_backfill(self) -> int:
        """Look up one batch of waiting trips; schedule the next if more wait."""

        if not self.enabled:
            return 0
        now = datetime.now(timezone.utc)
        waiting = self._waiting(now)
        done = asked = 0
        for trip in waiting:
            if asked >= BACKFILL_BATCH:
                break
            points, source = self._points_for(trip)
            if not points:
                continue  # nowhere to ask about; left as it is
            asked += 1
            if await self._look_up(trip.vin, trip.id, trip.start, points, source, retry=False):
                done += 1
        # Another pass while this one got answers; a failing service is left be
        # until the next start or the next trip.
        if done and asked >= BACKFILL_BATCH:
            self.schedule_backfill()
        return done
