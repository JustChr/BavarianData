"""The weather along a trip: waypoints, the Open-Meteo request, the record."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from .conftest import load_module

rw = load_module("route_weather")

T0 = datetime(2026, 11, 4, 10, 52, 30, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# -- waypoints ------------------------------------------------------------------


def test_a_waypoint_is_kept_every_five_minutes_or_five_km() -> None:
    sampler = rw.WaypointSampler()
    sampler.add(48.1000, 11.5000, _at(0))
    sampler.add(48.1010, 11.5010, _at(2))  # close and soon: skipped
    sampler.add(48.1020, 11.5020, _at(5))  # five minutes on: kept
    sampler.add(48.1600, 11.5020, _at(6))  # 6.4 km on, a minute later: kept
    assert [p.at for p in sampler.points] == [_at(0), _at(5), _at(6)]


def test_waypoints_are_rounded_to_about_a_kilometre() -> None:
    sampler = rw.WaypointSampler()
    sampler.add(48.123456, 11.987654, _at(0))
    point = sampler.points[0]
    assert (point.lat, point.lon) == (48.12, 11.99)


def test_the_heading_is_taken_from_the_real_positions() -> None:
    sampler = rw.WaypointSampler()
    sampler.add(48.10, 11.50, _at(0))
    sampler.add(48.20, 11.50, _at(10))  # due north
    sampler.add(48.20, 11.65, _at(20))  # due east
    assert [p.heading for p in sampler.points] == [None, 0, 90]


def test_finish_adds_where_the_drive_stopped() -> None:
    sampler = rw.WaypointSampler()
    sampler.add(48.10, 11.50, _at(0))
    sampler.add(48.11, 11.50, _at(2))  # skipped, but the newest fix
    assert [p.at for p in sampler.finish(None, None, _at(3))] == [_at(0), _at(2)]

    sampler = rw.WaypointSampler()
    sampler.add(48.10, 11.50, _at(0))
    points = sampler.finish(48.30, 11.60, _at(30))
    assert [(p.lat, p.lon, p.at) for p in points][-1] == (48.30, 11.60, _at(30))


def test_a_stored_track_gives_waypoints_at_its_own_times() -> None:
    track = [[48.10, 11.50, 0], [48.10, 11.50, 60], [48.20, 11.50, 600]]
    points = rw.points_from_track(track, T0, _at(10))
    assert [p.at for p in points] == [T0, _at(10)]


def test_a_track_without_times_is_spread_over_the_drive() -> None:
    track = [[48.10, 11.50], [48.15, 11.50], [48.20, 11.50]]
    points = rw.points_from_track(track, T0, _at(20))
    assert [p.at for p in points] == [T0, _at(10), _at(20)]


def test_thinning_keeps_both_ends() -> None:
    points = [rw.WeatherPoint(_at(i), 48.0, 11.0) for i in range(200)]
    thin = rw.thin(points, 60)
    assert len(thin) == 60
    assert thin[0] is points[0] and thin[-1] is points[-1]


# -- the request ----------------------------------------------------------------


def test_the_request_asks_for_each_point_and_the_quarter_hours_spanned() -> None:
    points = [rw.WeatherPoint(_at(0), 48.1, 11.5), rw.WeatherPoint(_at(40), 48.37, 10.9)]
    url, params = rw.build_request(points, now=_at(60))
    assert url == rw.FORECAST_URL
    assert params["latitude"] == "48.10,48.37"
    assert params["longitude"] == "11.50,10.90"
    assert params["start_minutely_15"] == "2026-11-04T10:45"
    assert params["end_minutely_15"] == "2026-11-04T11:30"
    assert params["minutely_15"] == "temperature_2m,precipitation,wind_speed_10m,wind_direction_10m"


def test_an_old_trip_goes_to_the_archive() -> None:
    points = [rw.WeatherPoint(_at(0), 48.1, 11.5)]
    url, _params = rw.build_request(points, now=_at(0) + timedelta(days=200))
    assert url == rw.HISTORICAL_URL


# -- the answer -----------------------------------------------------------------


def _location(temps: list[float], elevation: float = 520.0) -> dict:
    times = ["2026-11-04T10:45", "2026-11-04T11:00", "2026-11-04T11:15", "2026-11-04T11:30"]
    return {
        "elevation": elevation,
        "minutely_15": {
            "time": times,
            "temperature_2m": temps,
            "precipitation": [0.0, 0.1, 0.0, 0.2],
            "wind_speed_10m": [10.0, 12.0, 14.0, 16.0],
            "wind_direction_10m": [200, 210, 220, 230],
        },
    }


def test_each_point_reads_the_quarter_hour_it_was_passed() -> None:
    points = [
        rw.WeatherPoint(_at(0), 48.1, 11.5),
        rw.WeatherPoint(_at(25), 48.2, 11.5, heading=0),
    ]
    payload = [_location([5.0, 5.5, 6.0, 6.5]), _location([-1.0, -2.0, -3.0, -4.0], 1400.0)]
    samples = rw.parse_samples(payload, points, T0)
    assert samples == [
        {
            "t": 0,
            "temp_c": 5.0,
            "rain_mm": 0.0,
            "wind_kmh": 10.0,
            "wind_dir": 200,
            "elevation_m": 520.0,
            "heading": None,
        },
        {
            "t": 1500,
            "temp_c": -3.0,
            "rain_mm": 0.0,
            "wind_kmh": 14.0,
            "wind_dir": 220,
            "elevation_m": 1400.0,
            "heading": 0,
        },
    ]


def test_one_location_comes_back_as_an_object() -> None:
    points = [rw.WeatherPoint(_at(0), 48.1, 11.5)]
    assert rw.parse_samples(_location([5.0, 5.5, 6.0, 6.5]), points, T0)[0]["temp_c"] == 5.0


def test_a_point_without_a_temperature_is_left_out() -> None:
    points = [rw.WeatherPoint(_at(0), 48.1, 11.5), rw.WeatherPoint(_at(80), 48.1, 11.5)]
    payload = [_location([None, 5.5, 6.0, 6.5]), _location([5.0, 5.5, 6.0, 6.5])]
    assert rw.parse_samples(payload, points, T0) == []  # no value, and a quarter not asked


def test_the_mean_is_weighted_by_the_time_each_sample_stands_for() -> None:
    samples = [
        {"t": 0, "temp_c": 10.0},
        {"t": 60, "temp_c": 10.0},  # a dense patch in town
        {"t": 120, "temp_c": 10.0},
        {"t": 3720, "temp_c": 0.0},  # an hour of open road
    ]
    record = rw.summarize(samples, "route")
    # The three town samples together stand for 1.6 minutes plus half the hour;
    # counted one each, they would make it 7.5.
    assert record["temp_c"] == 5.2
    assert (record["temp_min_c"], record["temp_max_c"]) == (0.0, 10.0)
    assert record["source"] == "route"
    assert record["samples"] == samples


def test_an_answer_without_temperatures_is_still_a_record() -> None:
    assert rw.summarize([], "home") == {
        "source": "home",
        "temp_c": None,
        "temp_min_c": None,
        "temp_max_c": None,
        "samples": [],
    }


# -- the client -----------------------------------------------------------------


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, status: int = 200, payload: object = None, error: Exception | None = None):
        self.status, self.payload, self.error = status, payload, error
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params))
        if self.error is not None:
            raise self.error
        return _Response(self.status, self.payload)


def _lookup(client, points):
    return asyncio.run(client.lookup(points, T0, source="route", now=_at(60)))


def test_the_client_asks_nothing_while_off() -> None:
    session = _Session(payload=_location([5.0, 5.5, 6.0, 6.5]))
    assert _lookup(rw.RouteWeather(session), [rw.WeatherPoint(_at(0), 48.1, 11.5)]) is None
    assert session.calls == []


def test_the_client_returns_the_record() -> None:
    session = _Session(payload=_location([5.0, 5.5, 6.0, 6.5]))
    record = _lookup(rw.RouteWeather(session, enabled=True), [rw.WeatherPoint(_at(0), 48.1, 11.5)])
    assert record["temp_c"] == 5.0
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "session",
    [_Session(status=429), _Session(error=TimeoutError()), _Session(error=ValueError("bad json"))],
)
def test_a_failed_lookup_is_none_and_never_raises(session: _Session) -> None:
    assert (
        _lookup(rw.RouteWeather(session, enabled=True), [rw.WeatherPoint(_at(0), 48.1, 11.5)])
        is None
    )
