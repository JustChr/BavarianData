"""The rules for a report trip's route from a phone (``phone_route.py``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

PR = load_module("phone_route")

T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
PARKED = (50.7300, 7.1000)


def at(minute: float) -> datetime:
    return T0 + timedelta(minutes=minute)


def drive(route, minutes=range(21), *, accuracy=10.0) -> None:
    """A phone moving ~1 km a minute north, ending at ``PARKED``."""

    for minute in minutes:
        route.add(at(minute), PARKED[0] - (20 - minute) * 0.009, PARKED[1], accuracy)


def test_a_drive_gives_its_points_and_lets_them_go() -> None:
    route = PR.PhoneRoute()
    drive(route)

    points = route.take(at(0), at(20), PARKED)

    assert len(points) == 21
    assert points[-1][1:] == PARKED
    assert route.points == []


def test_a_phone_that_ends_elsewhere_was_not_in_this_car() -> None:
    route = PR.PhoneRoute()
    drive(route)

    assert route.take(at(0), at(20), (PARKED[0] + 0.05, PARKED[1])) == []
    # Let go all the same: those points belong to no later trip either.
    assert route.points == []


def test_no_start_or_no_parked_position_means_no_route() -> None:
    for start, end_fix in ((None, PARKED), (at(0), None)):
        route = PR.PhoneRoute()
        drive(route)
        assert route.take(start, at(20), end_fix) == []


def test_only_points_inside_the_trip_are_taken() -> None:
    route = PR.PhoneRoute()
    drive(route)

    points = route.take(at(5), at(15), (PARKED[0] - 5 * 0.009, PARKED[1]))

    assert [p[0] for p in points] == [at(m) for m in range(5, 16)]
    # What came after the arrival waits for the next trip.
    assert [p[0] for p in route.points] == [at(m) for m in range(16, 21)]


def test_a_queue_and_a_vague_fix_are_left_out() -> None:
    route = PR.PhoneRoute()
    assert route.add(at(0), 50.0, 7.0, 10)
    assert not route.add(at(1), 50.0002, 7.0, 10)  # ~20 m on: still the queue
    assert not route.add(at(2), 50.01, 7.0, 250)  # indoors, wandering
    assert route.add(at(3), 50.01, 7.0, None)  # no accuracy given: kept
    assert not route.add(at(4), 91.0, 7.0, 5)


def test_with_an_in_car_sensor_only_positions_in_the_car_are_held() -> None:
    route = PR.PhoneRoute(gated=True)
    assert not route.add(at(0), 50.0, 7.0, 10)  # the walk to the car
    assert route.set_in_car(True)
    assert not route.set_in_car(True)  # still on: not a new start
    assert route.add(at(1), 50.01, 7.0, 10)
    route.set_in_car(False)
    assert not route.add(at(2), 50.02, 7.0, 10)


def test_positions_are_held_no_longer_than_a_start_mark_lives() -> None:
    route = PR.PhoneRoute()
    route.add(at(0), 50.0, 7.0, 10)
    route.add(at(0) + timedelta(seconds=PR.MAX_AGE_S + 60), 50.1, 7.0, 10)

    assert len(route.points) == 1
