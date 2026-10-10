"""The vehicle-report event, run through the real coordinator (discussion #56).

``bavariandata_vehicle_report`` announces a new odometer reading once it has
held for ``REPORT_QUIET_S``. The scenarios pin the cases that must fire exactly
once -- an end-of-trip burst, a live-streamed drive -- and, mostly, the ones that
must not fire at all: a heartbeat repeating an unchanged odometer, a restart's
REST catch-up replaying the last reading, and the very first reading ever seen.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

REPORT = load_module("vehicle_report")
QUIET_MIN = REPORT.REPORT_QUIET_S / 60
EVENT = "bavariandata_vehicle_report"
FUEL = "vehicle.drivetrain.fuelSystem.remainingFuel"
RANGE = "vehicle.drivetrain.lastRemainingRange"


@pytest.fixture
def h():
    # A diesel: no battery capacity, so nothing charging-related runs alongside.
    harness = CoordinatorHarness(capacity_kwh=None)
    yield harness
    harness.close()


def _reports(h: CoordinatorHarness) -> list[dict]:
    return [data for _name, data in h.events(EVENT)]


def test_the_first_reading_ever_seen_only_seeds(h) -> None:
    """With nothing to compare against, a report cannot be told from a replay."""

    h.send(0, odometer=50_000)
    h.advance_to(60)
    assert _reports(h) == []


def test_an_end_of_trip_burst_is_announced_once_with_what_came_with_it(h) -> None:
    h.send(0, odometer=50_000)
    # The G31's burst: odometer, fuel, range and position in one go.
    h.send(120, odometer=50_012, lat=50.94, lon=6.96, extra={FUEL: 41, RANGE: 610})
    h.advance_to(120 + QUIET_MIN - 0.5)
    assert _reports(h) == [], "announced before the reading had held"
    h.advance_to(120 + QUIET_MIN + 0.5)
    (report,) = _reports(h)
    assert report["odometer_km"] == 50_012
    assert report["previous_odometer_km"] == 50_000
    assert report["distance_km"] == 12
    assert report["fuel_l"] == 41
    assert report["range_km"] == 610
    assert (report["latitude"], report["longitude"]) == (50.94, 6.96)
    assert report["soc_percent"] is None  # a key the car does not send stays, as None
    assert report["timestamp"].startswith("2026-09-26T10:00")
    h.advance_to(600)
    assert len(_reports(h)) == 1


def test_a_heartbeat_repeating_the_odometer_never_fires(h) -> None:
    """An iX1 repeats its unchanged odometer every few minutes (issue #62)."""

    for minute in range(0, 24 * 60, 7):
        h.send(minute, odometer=31_400)
    assert _reports(h) == []


def test_a_live_streamed_drive_is_announced_once_at_the_end(h) -> None:
    """The i5 streams 1 km steps while driving; one report, for the whole drive."""

    h.send(0, odometer=12_000)
    for step in range(1, 31):
        h.send(10 + step, odometer=12_000 + step)
        h.send(10 + step + 0.5, odometer=12_000 + step)  # a heartbeat in between
    h.advance_to(10 + 30 + QUIET_MIN + 1)
    (report,) = _reports(h)
    assert report["odometer_km"] == 12_030
    assert report["distance_km"] == 30


def test_a_restart_and_its_catch_up_replay_nothing(h) -> None:
    """The REST catch-up hands back the car's last values: not a new report."""

    h.send(0, odometer=50_000)
    h.send(60, odometer=50_020)
    h.advance_to(60 + QUIET_MIN + 1)
    assert len(_reports(h)) == 1
    for order in ("descriptors-first", "derived-first"):
        h.restart(order=order)
        h.send(h.minute + 2, stamped=60, odometer=50_020)  # the catch-up
        h.advance_to(h.minute + 60)
    assert len(_reports(h)) == 1


def test_a_report_pending_at_a_restart_is_announced_after_it(h) -> None:
    """Dropped on the way down, re-armed by the first batch back -- once."""

    h.send(0, odometer=50_000)
    h.send(60, odometer=50_007)
    h.advance_to(61)
    h.restart()
    h.send(h.minute + 2, stamped=60, odometer=50_007)
    h.advance_to(h.minute + QUIET_MIN + 1)
    (report,) = _reports(h)
    assert (report["previous_odometer_km"], report["odometer_km"]) == (50_000, 50_007)
    h.advance_to(h.minute + 60)
    assert len(_reports(h)) == 1


def test_an_odometer_running_backwards_is_ignored(h) -> None:
    h.send(0, odometer=50_000)
    h.send(30, odometer=49_990)
    h.advance_to(120)
    assert _reports(h) == []


def test_deleting_history_seeds_again_instead_of_announcing(h) -> None:
    h.send(0, odometer=50_000)
    h._run(h.coordinator.history.async_clear())
    h.send(30, odometer=50_000)  # a replay after the delete
    h.advance_to(120)
    assert _reports(h) == []


def test_the_report_sensor_follows_the_odometer_not_other_fields(h) -> None:
    """Service-demand fields arrive on BMW's own schedule, some months old."""

    h.send(0, odometer=50_000)
    h.send(600, extra={"vehicle.electricalSystem.battery.serviceDemand.recharge": "NO"})
    assert h.coordinator.last_vehicle_report(h.vin) == h.at(0)


@pytest.mark.parametrize(
    "odometer, last, expected",
    [
        (None, {"odometer_km": 1.0}, REPORT.IGNORE),
        (5.0, None, REPORT.SEED),
        (5.0, {"odometer_km": "x"}, REPORT.SEED),
        (5.0, {"odometer_km": 5.0}, REPORT.IGNORE),
        (4.0, {"odometer_km": 5.0}, REPORT.IGNORE),
        (6.0, {"odometer_km": 5.0}, REPORT.ARM),
    ],
)
def test_report_decision(odometer, last, expected) -> None:
    assert REPORT.report_decision(odometer, last) == expected
