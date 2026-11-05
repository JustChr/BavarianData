"""The weather along a trip, run through the real coordinator.

Open-Meteo is played by :class:`_FakeWeather`: it notes what it is asked and
answers with one sample per point (or fails, when told to). What the scenarios
pin is the part the coordinator owns -- waypoints only while the switch is on,
one lookup after the close, one retry, the backfill for older trips, and no
coordinate ever on the stored record.
"""

from __future__ import annotations

import json

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

rw = load_module("route_weather")
trip_weather = load_module("trip_weather")

HOME = (48.1000, 16.3000)
WORK = (48.2000, 16.4000)
STEP = 0.01  # degrees per fix, ~1.3 km


class _FakeWeather:
    """Answers each lookup with a sample per point, unless ``failing``."""

    def __init__(self, *, failing: int = 0) -> None:
        self.enabled = True
        self.failing = failing
        self.calls: list[dict] = []

    async def lookup(self, points, start, *, source, now):
        self.calls.append({"points": list(points), "source": source})
        if self.failing:
            self.failing -= 1
            return None
        samples = [
            {"t": int((p.at - start).total_seconds()), "temp_c": 4.0 + i, "heading": p.heading}
            for i, p in enumerate(points)
        ]
        return rw.summarize(samples, source)


@pytest.fixture
def h():
    harness = CoordinatorHarness(home=HOME)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    harness.hass.add_zone("Work", *WORK, radius_m=150)
    yield harness
    harness.close()


def _drive(h: CoordinatorHarness, start: float, fixes: int, *, every: float = 2):
    for i in range(1, fixes + 1):
        h.fix(start + i * every, HOME[0] + i * STEP, HOME[1] + i * STEP)
    here = (HOME[0] + fixes * STEP, HOME[1] + fixes * STEP)
    h.fix(start + fixes * every + 2, *here)  # parked, still reporting
    return here


def test_a_drive_is_looked_up_once_along_its_route(h):
    weather = h.coordinator.route_weather = _FakeWeather()
    _drive(h, 5, 10)  # 20 minutes, ~13 km
    h.advance_to(40)

    (call,) = weather.calls
    assert call["source"] == "route"
    points = call["points"]
    # The start, a waypoint every five minutes or so, and where it stopped.
    assert len(points) >= 4
    assert points[0].at == h.trips()[0].start
    assert (points[-1].lat, points[-1].lon) == (48.2, 16.4)
    (trip,) = h.trips()
    assert trip.weather["source"] == "route"
    assert trip.weather["temp_c"] is not None
    # The record holds the weather, never where it was looked up.
    stored = json.dumps(trip.to_dict()["weather"])
    assert "48.1" not in stored and "16.3" not in stored


def test_nothing_is_kept_or_asked_while_the_switch_is_off(h):
    weather = h.coordinator.route_weather = _FakeWeather()
    weather.enabled = False
    _drive(h, 5, 10)
    h.advance_to(40)

    assert weather.calls == []
    assert h.trips()[0].weather is None


def test_a_failed_lookup_is_tried_once_more_then_left(h):
    weather = h.coordinator.route_weather = _FakeWeather(failing=5)
    _drive(h, 5, 10)
    h.advance_to(40)
    assert len(weather.calls) == 1
    h.advance_to(40 + trip_weather.RETRY_S / 60 + 1)
    assert len(weather.calls) == 2
    h.advance_to(200)
    assert len(weather.calls) == 2
    assert h.trips()[0].weather is None


def test_an_older_trip_is_filled_in_at_its_zones(h):
    _drive(h, 5, 10)  # Home -> Work, stored while the switch was off
    h.advance_to(90)
    assert h.trips()[0].weather is None

    weather = h.coordinator.route_weather = _FakeWeather()
    h.coordinator.weather.schedule_backfill()
    h.advance_to(90 + trip_weather.BACKFILL_PAUSE_S / 60 + 1)

    (call,) = weather.calls
    assert call["source"] == "ends"
    assert [(p.lat, p.lon) for p in call["points"]] == [(48.1, 16.3), (48.2, 16.4)]
    assert h.trips()[0].weather["source"] == "ends"


def test_a_trip_just_closed_is_left_to_its_own_lookup(h):
    weather = h.coordinator.route_weather = _FakeWeather(failing=1)
    _drive(h, 5, 10)
    h.advance_to(40)  # closed, its own lookup failed, the retry not yet due
    assert len(weather.calls) == 1
    h.coordinator.weather.schedule_backfill(0)
    h.advance_to(41)
    # Not yet half an hour since the close: the backfill doesn't take it over.
    assert len(weather.calls) == 1
