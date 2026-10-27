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


def test_a_phone_without_a_usable_fix_adds_nothing(i3) -> None:
    """A phone removed, one with no position, one with a garbled accuracy."""

    # Another car on the account drives live: the phone is not its business.
    i3.coordinator.vehicle_trip_settings = {"WBATEST0000000002": LIVE}
    _burst(i3, 0, 50_000, HOME, 50)
    _in_car(i3, 100, True)
    _phone(i3, 101, _between(HOME, WORK, 0.25))
    i3.hass.remove_state(PHONE)
    i3.advance_to(102)
    i3.hass.set_state(PHONE, "not_home")
    i3.advance_to(103)
    i3.hass.set_state(PHONE, "not_home", latitude="north", longitude=7.0)
    i3.advance_to(104)
    middle = _between(HOME, WORK, 0.5)
    i3.hass.set_state(
        PHONE, "not_home", latitude=middle[0], longitude=middle[1], gps_accuracy="poor"
    )
    _phone(i3, 110, _between(HOME, WORK, 0.75))
    _phone(i3, 119.5, WORK)
    _in_car(i3, 120, False)
    _burst(i3, 120, 50_026, WORK, 49)
    _settle(i3)

    (trip,) = i3.trips()
    assert trip.track_source == "phone"
    # The good fixes and the one without a stated accuracy; the car's own fix
    # at the same spot as the phone's last is not drawn twice.
    expected = [_between(HOME, WORK, 0.25), middle, _between(HOME, WORK, 0.75), WORK]
    assert [tuple(point[:2]) for point in trip.track] == [
        pytest.approx(point, abs=1e-6) for point in expected
    ]


def test_reports_on_an_account_without_history_record_nothing() -> None:
    harness = CoordinatorHarness(capacity_kwh=None, history=False)
    try:
        harness.coordinator.trip_settings = REPORTS
        _burst(harness, 0, 50_000, HOME, 50)
        _mark(harness, 100)
        _burst(harness, 120, 50_026, WORK, 49)
        _settle(harness)
        assert harness.hass.event_types().count("bavariandata_vehicle_report") == 0
    finally:
        harness.close()


def test_a_report_without_bmw_s_timestamp_ends_when_it_settles(h) -> None:
    """No car clock to go by: the trip ends when the report's quiet window closes."""

    from .harness import DESCRIPTORS

    _burst(h, 0, 50_000, HOME, 50)
    arrived = h.at(120)
    h.replay(
        [
            {
                "at": arrived.isoformat(),
                "data": {
                    DESCRIPTORS["odometer"][0]: {"value": 50_012, "unit": "km"},
                    DESCRIPTORS["lat"][0]: {"value": WORK[0]},
                    DESCRIPTORS["lon"][0]: {"value": WORK[1]},
                    FUEL: {"value": 49},
                },
            }
        ]
    )
    _settle(h)

    (trip,) = h.trips()
    assert trip.end == h.at(120 + QUIET_MIN)
    assert trip.distance_km == 12


# -- a second phone (discussion #231) -----------------------------------------

PHONE_2 = "device_tracker.partner_phone"
IN_CAR_2 = "binary_sensor.partner_android_auto"


@pytest.fixture
def two_phones(i3):
    i3.hass.set_state(IN_CAR_2, "off")
    i3.coordinator.trip_settings = _phone_settings(phone_tracker_2=PHONE_2, phone_in_car_2=IN_CAR_2)
    i3.coordinator.watch_phone_routes()
    return i3


def _phone_2(h: CoordinatorHarness, minute: float, where) -> None:
    h.advance_to(minute)
    h.hass.set_state(PHONE_2, "not_home", latitude=where[0], longitude=where[1], gps_accuracy=8)


def _drive_with_second_phone(h: CoordinatorHarness, *, first_rides: bool, offset: float) -> None:
    """Home to Work. The second phone rides; the first only if ``first_rides``.

    ``offset`` moves the second phone's line sideways so the two are told apart.
    """

    _burst(h, 0, 50_000, HOME, 50)
    h.advance_to(100)
    h.hass.set_state(IN_CAR_2, "on")
    if first_rides:
        h.hass.set_state(IN_CAR, "on")
    for step in range(1, 20):
        where = _between(HOME, WORK, step / 20)
        _phone_2(h, 100 + step, (where[0] + offset, where[1]))
        if first_rides:
            _phone(h, 100 + step, where)
    _phone_2(h, 119.5, WORK)
    if first_rides:
        _phone(h, 119.5, WORK)
    h.advance_to(120)
    h.hass.set_state(IN_CAR_2, "off")
    h.hass.set_state(IN_CAR, "off")
    _burst(h, 120, 50_026, WORK, 49)
    _settle(h)


def test_the_second_phone_draws_the_route_when_the_first_stayed_home(two_phones) -> None:
    _drive_with_second_phone(two_phones, first_rides=False, offset=0)

    (trip,) = two_phones.trips()
    assert trip.track_source == "phone"
    assert (trip.start, trip.end) == (two_phones.at(100), two_phones.at(120))
    assert len(trip.track) == 20  # no fix of the phone's before it got in


def test_when_both_phones_rode_along_the_first_one_wins(two_phones) -> None:
    # 0.0005 degrees (~55 m) apart: both end at Work, only one line is drawn.
    _drive_with_second_phone(two_phones, first_rides=True, offset=0.0005)

    (trip,) = two_phones.trips()
    assert trip.track_source == "phone"
    expected = _between(HOME, WORK, 1 / 20)
    assert tuple(trip.track[0][:2]) == pytest.approx(expected, abs=1e-5)


def test_a_second_phone_that_was_not_in_the_car_adds_nothing(two_phones) -> None:
    """Neither phone ends where the car parked: the straight line stays."""

    elsewhere = (50.8500, 6.8000)
    two_phones.hass.set_state(IN_CAR_2, "off")
    _drive_with_phone(two_phones, end=elsewhere)

    (trip,) = two_phones.trips()
    assert trip.track == []
