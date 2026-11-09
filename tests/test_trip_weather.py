"""The weather engine's edges, against a small host (the scenarios cover the drive)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from .conftest import load_module
from .fake_ha import Clock, FakeHass, load_with_fake_ha

tw = load_with_fake_ha("trip_weather")
rw = load_module("route_weather")
trips = load_module("history.trips")
car_state = load_module("car_state")

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class _Client:
    def __init__(self, *, enabled=True, answer=True):
        self.enabled, self.answer = enabled, answer
        self.calls: list[tuple[str, int]] = []

    async def lookup(self, points, start, *, source, now):
        self.calls.append((source, len(points)))
        return rw.summarize([{"t": 0, "temp_c": 2.0}], source) if self.answer else None

    async def day(self, lat, lon, day):
        self.calls.append(("day", (lat, lon)))
        if not self.answer:
            return None
        return {"date": day.isoformat(), "temp_c": 3.0, "temp_min_c": 1.0, "temp_max_c": 5.0}


class _History:
    def __init__(self, stored=(), *, fail=False):
        self.stored = {trip.id: trip for trip in stored}
        self.fail = fail

    def trips(self):
        return sorted(self.stored.values(), key=lambda trip: trip.start, reverse=True)

    def find_trip(self, vin, trip_id):
        return self.stored.get(trip_id)

    def add_trip(self, trip):
        if self.fail:
            raise OSError("disk full")
        self.stored[trip.id] = trip


class _Host:
    signal_trips = "trips"
    signal_history = "history"

    def __init__(self, history=None, client=None, *, zones=None, home=None):
        self.hass = FakeHass(Clock(NOW))
        self.history = history
        self.route_weather = client
        self.zones = zones or {}
        self.home = home
        self.car = car_state.Vehicle("VIN")

    def _car(self, vin):
        return self.car

    def _peek(self, vin):
        return self.car

    def _zone_location(self, name):
        return self.zones.get(name)

    def _home_location(self):
        return self.home

    def _vins_where(self, test):
        return ["VIN"] if test(self.car) else []


def _trip(hours_ago: float, **kw):
    start = NOW - timedelta(hours=hours_ago)
    return trips.Trip(vin="VIN", start=start, end=start + timedelta(minutes=20), **kw)


def _run_tasks(host: _Host) -> None:
    while host.hass.tasks:
        asyncio.run(host.hass.tasks.pop(0))


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(tw, "datetime", _Now)


def test_waypoints_are_kept_only_while_the_switch_is_on() -> None:
    host = _Host(client=_Client(enabled=False))
    weather = tw.TripWeather(host)
    weather.trip_opened("VIN")
    weather.note_fix("VIN", 48.1, 11.5, NOW)
    assert weather.take_waypoints("VIN") is None

    host.route_weather.enabled = True
    weather.trip_opened("VIN")
    weather.note_fix("VIN", 48.1, 11.5, NOW)
    assert len(weather.take_waypoints("VIN").points) == 1


def test_a_trip_dropped_as_noise_is_not_looked_up() -> None:
    host = _Host(_History(), _Client())
    sampler = rw.WaypointSampler()
    sampler.add(48.1, 11.5, NOW)
    tw.TripWeather(host).trip_stored("VIN", _trip(1), sampler, None)
    assert host.hass.tasks == []


def test_a_lookup_after_the_switch_went_off_asks_nothing() -> None:
    trip = _trip(1)
    host = _Host(_History([trip]), _Client())
    sampler = rw.WaypointSampler()
    sampler.add(48.1, 11.5, trip.start)
    tw.TripWeather(host).trip_stored("VIN", trip, sampler, (48.2, 11.6))
    host.route_weather.enabled = False
    _run_tasks(host)
    assert host.route_weather.calls == []


def test_a_trip_removed_meanwhile_is_not_written() -> None:
    weather = tw.TripWeather(_Host(_History(), _Client()))
    assert weather._write("VIN", "gone", {"temp_c": 1.0}) is False
    assert tw.TripWeather(_Host(None, _Client()))._write("VIN", "x", {}) is False


def test_a_store_that_fails_is_logged_not_raised(caplog) -> None:
    trip = _trip(1)
    weather = tw.TripWeather(_Host(_History([trip], fail=True), _Client()))
    assert weather._write("VIN", trip.id, {"temp_c": 1.0}) is False
    assert "Could not store the weather" in caplog.text


def test_the_backfill_walks_the_track_then_the_zones_then_home() -> None:
    tracked = _trip(5, track=[[48.10, 11.50, 0], [48.20, 11.50, 900]])
    zoned = _trip(
        4, start_place={"zone": "Home", "label": "Home"}, end_place={"label": "Somewhere"}
    )
    elsewhere = _trip(3, start_place={"label": "A"}, end_place={"label": "B"})
    fresh = _trip(0.2)  # its own lookup may still come
    old = _trip(24 * 365 * 6)  # before Open-Meteo's archive
    host = _Host(
        _History([tracked, zoned, elsewhere, fresh, old]),
        _Client(),
        zones={"Home": (48.1, 11.5)},
        home=(48.0, 11.0),
    )
    done = asyncio.run(tw.TripWeather(host).async_backfill())
    assert done == 3
    assert sorted(host.route_weather.calls) == [("ends", 1), ("home", 1), ("track", 2)]
    assert fresh.weather is None and old.weather is None


def test_a_trip_with_nowhere_to_ask_about_is_left_alone() -> None:
    host = _Host(_History([_trip(3)]), _Client())  # no zone, no home
    assert asyncio.run(tw.TripWeather(host).async_backfill()) == 0
    assert host.route_weather.calls == []


def test_a_full_batch_schedules_the_next_one_and_a_switched_off_one_none() -> None:
    host = _Host(
        _History([_trip(3 + i) for i in range(tw.BACKFILL_BATCH + 2)]), _Client(), home=(48.0, 11.0)
    )
    weather = tw.TripWeather(host)
    assert asyncio.run(weather.async_backfill()) == tw.BACKFILL_BATCH
    assert len([t for t in host.hass.timers if not t.cancelled]) == 1
    weather.schedule_backfill()  # one already due: not a second
    assert len([t for t in host.hass.timers if not t.cancelled]) == 1
    weather.cancel_backfill()
    assert not [t for t in host.hass.timers if not t.cancelled]

    host.route_weather.enabled = False
    assert asyncio.run(weather.async_backfill()) == 0
    weather.schedule_backfill()
    assert not [t for t in host.hass.timers if not t.cancelled]


def test_without_a_history_nothing_waits() -> None:
    assert tw.TripWeather(_Host(None, _Client()))._waiting(NOW) == []


# --- the day's temperature --------------------------------------------------


def _check_day(host):
    done = asyncio.run(tw.TripWeather(host).async_check_day())
    return done


def test_a_car_that_never_sent_a_position_is_asked_about_at_home():
    host = _Host(client=_Client(), home=(48.1, 16.3))
    assert _check_day(host) == 1
    assert host.route_weather.calls == [("day", (48.1, 16.3))]
    assert host.car.outside["source"] == "home"
    assert host.car.outside["date"] == NOW.date().isoformat()


def test_the_parked_position_wins_over_home():
    host = _Host(client=_Client(), home=(48.1, 16.3))
    host.car.gps.last_position = (48.21, 16.37)
    _check_day(host)
    assert host.route_weather.calls == [("day", (48.21, 16.37))]
    assert host.car.outside["source"] == "car"


def test_nowhere_to_ask_about_asks_nothing():
    host = _Host(client=_Client(), home=None)
    assert _check_day(host) == 0
    assert host.route_weather.calls == [] and host.car.outside is None


def test_the_day_is_not_asked_with_the_switch_off_or_without_a_client():
    host = _Host(client=_Client(enabled=False), home=(48.1, 16.3))
    assert _check_day(host) == 0 and host.route_weather.calls == []
    assert _check_day(_Host(client=None, home=(48.1, 16.3))) == 0


def test_a_day_already_asked_is_not_asked_again():
    host = _Host(client=_Client(), home=(48.1, 16.3))
    host.car.outside = {"date": NOW.date().isoformat(), "temp_c": 1.0, "source": "home"}
    assert _check_day(host) == 0 and host.route_weather.calls == []
