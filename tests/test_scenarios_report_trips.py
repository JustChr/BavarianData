"""Trips built from vehicle reports, run through the real coordinator (#56).

A car that reports only once parked (iDrive 6 and older) sends one burst per
drive: odometer, fuel and position, all stamped with the moment it parked. The
``reports`` trip mode turns each announced report into one trip -- from where the
car stood at the previous report to where it stands now -- and takes the start
time from ``bavariandata.mark_trip_start`` when something called it, because the
burst itself says only when the drive *ended*.

The scenarios pin what such a trip carries, which mark it takes (the earliest
since the car last parked; a mark after the report is already the next trip),
and that the live detectors stay out of it: read live, the burst's position jump
is a drive of its own.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import VIN, CoordinatorHarness

REPORT = load_module("vehicle_report")
REPORTS = load_module("trip_settings").TripSettings(detection="reports")
LIVE = load_module("trip_settings").TripSettings(detection="live")
QUIET_MIN = REPORT.REPORT_QUIET_S / 60
FUEL = "vehicle.drivetrain.fuelSystem.remainingFuel"

HOME = (50.9400, 6.9600)
WORK = (50.7300, 7.1000)


@pytest.fixture
def h():
    # A diesel, as in discussion #56: no battery, nothing charging alongside.
    harness = CoordinatorHarness(capacity_kwh=None)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    harness.hass.add_zone("Work", *WORK, radius_m=150)
    harness.coordinator.trip_settings = REPORTS
    yield harness
    harness.close()


def _burst(h: CoordinatorHarness, minute: float, km: int, where, fuel: float) -> None:
    """The G31's report on parking: everything at once, one timestamp."""

    h.send(minute, odometer=km, lat=where[0], lon=where[1], extra={FUEL: fuel})


def _mark(h: CoordinatorHarness, minute: float) -> None:
    if h.at(minute) > h.clock.now:
        h.advance_to(minute)
    h.coordinator.mark_trip_start(VIN, h.at(minute))


def _settle(h: CoordinatorHarness) -> None:
    h.advance_to(h.minute + QUIET_MIN + 1)


def test_a_report_without_a_mark_is_a_trip_without_a_duration(h) -> None:
    _burst(h, 0, 50_000, HOME, 50)
    _burst(h, 120, 50_012, WORK, 49)
    _settle(h)

    (trip,) = h.trips()
    assert trip.start_place["label"] == "Home"
    assert trip.end_place["label"] == "Work"
    assert trip.distance_km == 12
    assert trip.stats == {"fuel_start_l": 50.0, "fuel_end_l": 49.0, "fuel_used_l": 1.0}
    # Stored at the arrival, by the car's clock -- not at the previous report,
    # which is when it parked at Home, and not when the report was announced.
    assert trip.start == trip.end == h.at(120)
    assert trip.duration_unknown and trip.duration_s is None
    assert trip.to_dict()["duration_unknown"] is True


def test_a_marked_start_gives_the_trip_its_departure(h) -> None:
    _burst(h, 0, 50_000, HOME, 50)
    _mark(h, 100)
    _burst(h, 120, 50_012, WORK, 49)
    _settle(h)

    (trip,) = h.trips()
    assert (trip.start, trip.end) == (h.at(100), h.at(120))
    assert trip.duration_s == 20 * 60
    assert not trip.duration_unknown


def test_the_earliest_mark_wins_and_a_reconnect_mid_drive_is_ignored(h) -> None:
    """A phone that drops and rejoins the car's Bluetooth marks twice."""

    _burst(h, 0, 50_000, HOME, 50)
    for minute in (100, 100.2, 111):
        _mark(h, minute)
    _burst(h, 120, 50_012, WORK, 49)
    _settle(h)

    (trip,) = h.trips()
    assert trip.start == h.at(100)


def test_a_return_trip_started_before_the_report_was_announced(h) -> None:
    """The next drive's mark arrives while this report is still settling."""

    _burst(h, 0, 50_000, HOME, 50)
    _mark(h, 100)
    _burst(h, 120, 50_012, WORK, 49)
    _mark(h, 122)  # off again before the 5 minutes are up
    _burst(h, 140, 50_024, HOME, 48)
    _settle(h)

    back, out = h.trips()  # newest first
    assert (out.start, out.end) == (h.at(100), h.at(120))
    assert (back.start, back.end) == (h.at(122), h.at(140))
    assert (back.start_place["label"], back.end_place["label"]) == ("Work", "Home")


def test_a_mark_with_no_drive_after_it_expires(h) -> None:
    """A Bluetooth connect without a drive must not stretch the next trip to days."""

    _burst(h, 0, 50_000, HOME, 50)
    _mark(h, 10)
    _burst(h, 10 + 14 * 60, 50_012, WORK, 49)
    _settle(h)

    (trip,) = h.trips()
    assert trip.duration_unknown


def test_a_mark_survives_a_restart_before_the_report(h) -> None:
    _burst(h, 0, 50_000, HOME, 50)
    _mark(h, 100)
    h.restart()
    h.coordinator.trip_settings = REPORTS  # read from the options at setup
    _burst(h, 120, 50_012, WORK, 49)
    _settle(h)

    (trip,) = h.trips()
    assert trip.start == h.at(100)
    assert trip.start_place["label"] == "Home"


def test_the_live_detectors_stay_out_of_it(h) -> None:
    """Read live, a burst is a 20 km jump in one fix: a drive of its own."""

    _burst(h, 0, 50_000, HOME, 50)
    h.send(60, door=False)
    h.send(61, moving=True)
    _burst(h, 120, 50_012, WORK, 49)
    h.advance_to(300)

    assert len(h.trips()) == 1
    assert not h.coordinator.has_open_trip(VIN)


def test_the_live_mode_ignores_reports(h) -> None:
    """The default mode builds nothing from a report; the GPS detector decides."""

    h.coordinator.trip_settings = LIVE
    _burst(h, 0, 50_000, HOME, 50)
    _mark(h, 100)
    _burst(h, 120, 50_012, WORK, 49)
    h.advance_to(300)

    assert all(not trip.duration_unknown for trip in h.trips())
    assert all(trip.start != h.at(100) for trip in h.trips())


@pytest.fixture
def bev():
    """An electric car on the report mode -- an older i3, say."""

    harness = CoordinatorHarness(capacity_kwh=75.0)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    harness.hass.add_zone("Work", *WORK, radius_m=150)
    harness.coordinator.trip_settings = REPORTS
    yield harness
    harness.close()


def _ev_burst(h: CoordinatorHarness, minute: float, km: int, where, **values) -> None:
    h.send(minute, odometer=km, lat=where[0], lon=where[1], **values)


def test_a_charge_between_drives_is_not_part_of_the_trip(bev) -> None:
    """The previous report's SoC predates the charge: 60 -> 82 % was stored."""

    _ev_burst(bev, 0, 50_000, HOME, soc=60)
    bev.send(60, soc=75)
    bev.send(120, soc=90)  # charged at home overnight
    _ev_burst(bev, 300, 50_030, WORK, soc=82)
    _settle(bev)

    (trip,) = bev.trips()
    assert (trip.soc_start, trip.soc_end) == (90, 82)
    assert trip.energy_kwh == 6.0


def test_the_charge_set_off_with_survives_a_restart_mid_report(bev) -> None:
    """The restart's replay of the burst must not become the starting SoC."""

    _ev_burst(bev, 0, 50_000, HOME, soc=60)
    bev.send(120, soc=90)
    _ev_burst(bev, 300, 50_030, WORK, soc=82)
    bev.advance_to(301)
    bev.restart()
    bev.coordinator.trip_settings = REPORTS
    _ev_burst(bev, bev.minute + 2, 50_030, WORK, soc=82)
    _settle(bev)

    (trip,) = bev.trips()
    assert (trip.soc_start, trip.soc_end) == (90, 82)


def test_a_hybrid_burst_without_a_charge_reading_has_no_end_soc(bev) -> None:
    """A hybrid that sent no SoC with its burst: "90 -> 90 %" would be made up."""

    _ev_burst(bev, 0, 50_000, HOME, soc=90, extra={FUEL: 40})
    _ev_burst(bev, 300, 50_030, WORK, extra={FUEL: 38})
    _settle(bev)

    (trip,) = bev.trips()
    assert trip.hybrid
    assert (trip.soc_start, trip.soc_end) == (90, None)
    assert trip.stats["fuel_used_l"] == 2.0


def test_a_mixed_account_runs_each_car_in_its_own_mode(h) -> None:
    """The G31 on reports, a car that streams while driving on live, one account.

    Trip detection used to be one setting per account, so a mixed account had to
    run every car in the mode of its oldest. The streaming car's report must not
    become a report trip of its own.
    """

    streaming = "WBATEST0000000002"
    h.coordinator.vehicle_trip_settings = {streaming: LIVE}
    _burst(h, 0, 50_000, HOME, 50)
    h.vin = streaming
    _burst(h, 10, 20_000, HOME, 40)
    h.vin = VIN
    _burst(h, 120, 50_012, WORK, 49)
    h.vin = streaming
    _burst(h, 130, 20_012, WORK, 39)
    _settle(h)

    assert all(not trip.duration_unknown for trip in h.trips())
    h.vin = VIN
    (trip,) = h.trips()
    assert trip.duration_unknown
    assert trip.end_place["label"] == "Work"


# -- the route from a phone (an i3, discussion #54) ---------------------------

PHONE = "device_tracker.phone"
IN_CAR = "binary_sensor.phone_android_auto"


def _phone_settings(**overrides):
    values = {
        "detection": "reports",
        "record_track": True,
        "phone_tracker": PHONE,
        "phone_in_car": IN_CAR,
        **overrides,
    }
    return load_module("trip_settings").TripSettings(**values)


@pytest.fixture
def i3():
    harness = CoordinatorHarness(capacity_kwh=None)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    harness.hass.add_zone("Work", *WORK, radius_m=150)
    harness.hass.set_state(IN_CAR, "off")
    harness.coordinator.trip_settings = _phone_settings()
    harness.coordinator.watch_phone_routes()
    yield harness
    harness.close()


def _phone(h: CoordinatorHarness, minute: float, where, accuracy: float = 8) -> None:
    h.advance_to(minute)
    h.hass.set_state(
        PHONE, "not_home", latitude=where[0], longitude=where[1], gps_accuracy=accuracy
    )


def _in_car(h: CoordinatorHarness, minute: float, on: bool) -> None:
    h.advance_to(minute)
    h.hass.set_state(IN_CAR, "on" if on else "off")


def _between(a, b, share: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share)


def _drive_with_phone(h: CoordinatorHarness, *, end=WORK) -> None:
    """Home to ``end`` with the phone on Android Auto from 100 to 120."""

    _burst(h, 0, 50_000, HOME, 50)
    _phone(h, 99, HOME)  # walking to the car: not held
    _in_car(h, 100, True)
    for step in range(1, 20):
        _phone(h, 100 + step, _between(HOME, end, step / 20))
    _phone(h, 119.5, end)
    _in_car(h, 120, False)
    _burst(h, 120, 50_026, WORK, 49)
    _phone(h, 121, _between(end, HOME, 0.05))  # walking away: not held
    _settle(h)


def test_the_phone_draws_the_route_and_the_car_keeps_the_numbers(i3) -> None:
    _drive_with_phone(i3)

    (trip,) = i3.trips()
    # The in-car sensor turning on was the start mark: no automation needed.
    assert (trip.start, trip.end) == (i3.at(100), i3.at(120))
    assert trip.distance_km == 26  # the odometer's, not the phone's
    assert trip.track_source == "phone"
    assert trip.to_dict()["track_source"] == "phone"
    # Where the phone was on getting in and its drive; the car's own fix
    # closes the line (the same spot as the phone's last, so not twice).
    assert len(trip.track) == 21
    assert trip.track[0][:2] == [round(HOME[0], 5), round(HOME[1], 5)]
    assert trip.track[-1][:2] == [round(WORK[0], 5), round(WORK[1], 5)]
    assert trip.track[-1][2] == 19.5 * 60  # the phone got there first


def test_a_phone_that_was_not_in_the_car_leaves_the_straight_line(i3) -> None:
    """The partner drove the car to Work; the phone went somewhere else."""

    elsewhere = (50.8500, 6.8000)
    _drive_with_phone(i3, end=elsewhere)

    (trip,) = i3.trips()
    assert trip.track == []
    assert trip.track_source is None
    assert trip.start == i3.at(100)  # the start mark still stands


def test_without_route_recording_nothing_is_kept(i3) -> None:
    i3.coordinator.trip_settings = _phone_settings(record_track=False)
    i3.coordinator.watch_phone_routes()
    _drive_with_phone(i3)

    (trip,) = i3.trips()
    assert trip.track == []
    # Nor does the sensor mark a start: the whole phone route is off.
    assert trip.duration_unknown


def test_without_an_in_car_sensor_a_marked_start_opens_the_route(i3) -> None:
    i3.coordinator.trip_settings = _phone_settings(phone_in_car=None)
    i3.coordinator.watch_phone_routes()
    _burst(i3, 0, 50_000, HOME, 50)
    _phone(i3, 50, _between(HOME, WORK, 0.5))  # earlier errand: before the mark
    _mark(i3, 100)
    for step in range(1, 21):
        _phone(i3, 100 + step, _between(HOME, WORK, step / 20))
    _burst(i3, 120, 50_026, WORK, 49)
    _settle(i3)

    (trip,) = i3.trips()
    assert trip.track_source == "phone"
    assert len(trip.track) == 20  # the errand before the mark is not in it
    assert all(point[2] >= 0 for point in trip.track)


def test_a_live_car_never_listens_to_the_phone(i3) -> None:
    i3.coordinator.trip_settings = _phone_settings(detection="live")
    i3.coordinator.watch_phone_routes()

    assert i3.hass.state_listeners == []
