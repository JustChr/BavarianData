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
    harness.coordinator.trip_detection = "reports"
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
    h.coordinator.trip_detection = "reports"  # read from the options at setup
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

    h.coordinator.trip_detection = "live"
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
    harness.coordinator.trip_detection = "reports"
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
    bev.coordinator.trip_detection = "reports"
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
