"""How a car reports, measured through the real coordinator.

Each test plays the shape of a car we have seen: the i5 (SoC and odometer on
change, GPS every few seconds while driving), the X3 30e (issue #25: the SoC
only ever comes with a REST fetch), the iDrive 6 G31 (discussion #56: one burst
per park, sharing one timestamp), the iX1 (issue #62: the odometer repeated
unchanged with a fresh timestamp) and the MINI (issue #100: a zero placeholder,
stamped days old).
"""

from __future__ import annotations

import json

import pytest

from .harness import CoordinatorHarness

TRIP_END_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"
SERVICE = "vehicle.status.serviceDistance.next"


@pytest.fixture
def h():
    harness = CoordinatorHarness(capacity_kwh=None)
    yield harness
    harness.close()


def _profile(h: CoordinatorHarness) -> dict:
    return h.coordinator.car_profiles[h.vin].as_dict(h.clock.now)


def test_a_streamed_soc_is_told_from_the_first_reading(h):
    h.send(1, soc=60, step="soc on the stream")

    profile = _profile(h)
    assert profile["soc_source"] == "stream"
    assert profile["soc_arrivals"] == {"stream": 1, "rest": 0}


def test_a_soc_only_fetched_is_rest_only_after_a_day(h):
    # The X3 30e (#25): the stream carries the rest, never the SoC.
    h.send(1, soc=60, rest=True, step="daily fetch")
    h.send(2, odometer=1000, step="stream")
    assert _profile(h)["soc_source"] is None, "a parked i5 can be quiet for hours"

    h.send(24 * 60 + 5, soc=58, rest=True, step="next daily fetch")

    profile = _profile(h)
    assert profile["soc_source"] == "rest_only"
    assert profile["batches"] == {"stream": 1, "rest": 2}


def test_a_live_drive_ticks_the_odometer_with_fixes_in_between(h):
    lat, lon = 48.10, 11.50
    h.send(0, odometer=1000)
    minute = 1.0
    for km in range(1001, 1007):
        for _ in range(3):
            lat += 0.003
            h.fix(minute, lat, lon)
            minute += 0.4
        h.send(minute, odometer=km)
        minute += 0.1

    profile = _profile(h)
    assert profile["drive_reporting"] == "live"
    assert profile["odometer_steps"] == 6
    assert profile["median_km_per_step"] == 1
    assert profile["median_moving_fixes_per_step"] == 3


def test_an_end_of_trip_car_jumps_by_a_whole_trip_in_one_burst(h):
    # The G31 (#56): nothing while driving, then everything at once, one stamp.
    h.send(0, odometer=50_000, lat=48.10, lon=11.50)
    for n, (minute, km) in enumerate(((120, 50_023), (300, 50_041), (600, 50_090), (900, 50_112))):
        h.send(minute, odometer=km, lat=48.10 + n / 10, lon=11.50, door=False)

    profile = _profile(h)
    assert profile["drive_reporting"] == "end_of_trip"
    assert profile["median_moving_fixes_per_step"] <= 1
    assert profile["cadence"]["single_timestamp_bursts"] == 0, "bursts here hold 4 fields"

    h.send(1200, odometer=50_140, lat=48.9, lon=11.5, door=False, soc=40, plug="disconnected")
    assert _profile(h)["cadence"]["single_timestamp_bursts"] == 1


def test_a_rest_fetch_does_not_make_the_next_stream_reading_one_long_step(h):
    h.send(0, odometer=1000)
    h.send(60, odometer=1050, rest=True, step="fetch after a drive the stream missed")
    h.send(120, odometer=1051)

    assert _profile(h)["median_km_per_step"] == 1


def test_a_value_repeated_with_a_fresh_stamp_is_a_heartbeat(h):
    # The iX1's odometer (#62): same value, new timestamp, every few minutes.
    for minute in range(0, 30, 5):
        h.send(minute, odometer=1000)

    profile = _profile(h)
    assert profile["repeats"]["heartbeat"] == 5
    assert profile["repeats"]["changed"] == 1
    assert profile["repeats"]["mostly_heartbeat"] == ["vehicle.vehicle.travelledDistance"]
    assert profile["cadence"]["median_awake_gap_s"] == 300


def test_placeholders_and_stale_stamps_are_counted(h):
    # The MINI (#100): a trip-end battery charge of 0, stamped six days old.
    h.send(10, stamped=10 - 6 * 24 * 60, extra={TRIP_END_SOC: 0})
    h.send(11, stamped=11 - 90 * 24 * 60, extra={SERVICE: 12000})
    h.send(12, stamped=12 - 90 * 24 * 60, rest=True, extra={SERVICE: 12000})

    profile = _profile(h)
    assert profile["placeholders"] == {TRIP_END_SOC: 1}
    assert profile["stale_on_stream"] == {SERVICE: 1}, "REST hands back its cache: not stale"


def test_the_profile_holds_no_values(h):
    h.send(0, odometer=123_456, lat=48.137154, lon=11.576124, soc=61)
    h.send(5, odometer=123_457, lat=48.139999, lon=11.576124, soc=61)

    text = json.dumps(_profile(h))
    for value in ("48.13", "11.57", "123456", "123457", "61"):
        assert value not in text
