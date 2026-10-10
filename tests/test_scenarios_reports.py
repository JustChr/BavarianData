"""The vehicle-report event, run through the real coordinator (discussion #56).

``bavariandata_vehicle_report`` announces a new odometer reading once it has
held for ``REPORT_QUIET_S``. The scenarios pin the cases that must fire exactly
once -- an end-of-trip burst, a live-streamed drive -- and, mostly, the ones that
must not fire at all: a heartbeat repeating an unchanged odometer, a restart's
REST catch-up replaying the last reading, and the very first reading ever seen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _distances(h: CoordinatorHarness) -> list[tuple[float, float]]:
    return [(r["previous_odometer_km"], r["odometer_km"]) for r in _reports(h)]


def test_a_reading_too_far_ahead_is_never_announced_nor_kept(h) -> None:
    """A km odometer labelled as miles lands 1.6 times too high, and holds.

    Announced, it was one 30 000 km trip; kept, every true reading after it
    looked like the odometer running backwards, and the event went silent for
    good -- until history was deleted.
    """

    h.send(0, odometer=50_000)
    h.send(120, odometer=80_467)  # one whole burst, mislabelled
    h.advance_to(300)
    assert _reports(h) == [], "an impossible jump was announced"
    h.send(600, odometer=50_030)
    h.advance_to(600 + QUIET_MIN + 1)
    h.send(1200, odometer=50_060)
    h.advance_to(1200 + QUIET_MIN + 1)
    assert _distances(h) == [(50_000, 50_030), (50_030, 50_060)]


@pytest.mark.parametrize("garbage", [0, 999_999])
def test_a_wrong_first_reading_heals_from_the_next_two_that_agree(h, garbage) -> None:
    """Nothing to check the very first reading against: the next ones settle it."""

    h.send(0, odometer=garbage)
    h.send(60, odometer=50_000)
    h.advance_to(60 + QUIET_MIN + 1)
    assert _reports(h) == [], "measured from the wrong first reading"
    h.send(180, odometer=50_012)
    h.advance_to(180 + QUIET_MIN + 1)
    h.send(400, odometer=50_020)
    h.advance_to(400 + QUIET_MIN + 1)
    assert _distances(h) == [(50_000, 50_012), (50_012, 50_020)]


def test_a_candidate_survives_a_restart(h) -> None:
    h.send(0, odometer=0)
    h.send(60, odometer=50_000)
    h.restart()
    h.send(h.minute + 120, odometer=50_012)
    h.advance_to(h.minute + QUIET_MIN + 1)
    assert _distances(h) == [(50_000, 50_012)]


def test_a_long_drive_while_home_assistant_was_off_is_announced(h) -> None:
    """3000 km in three days is a road trip, not a glitch."""

    h.send(0, odometer=50_000)
    h.send(3 * 24 * 60, odometer=53_000)
    h.advance_to(h.minute + QUIET_MIN + 1)
    assert _distances(h) == [(50_000, 53_000)]


def test_a_car_with_a_stuck_clock_still_reports(h) -> None:
    """The car's timestamps never move; our own clock still allows the drive."""

    h.send(0, odometer=50_000)
    h.send(240, stamped=0, odometer=50_150)
    h.advance_to(240 + QUIET_MIN + 1)
    assert _distances(h) == [(50_000, 50_150)]


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


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
HOUR_AGO = (NOW - timedelta(hours=1)).isoformat()
STAMPED = {"odometer_km": 5.0, "timestamp": HOUR_AGO}
WITH_CANDIDATE = {**STAMPED, "candidate": {"odometer_km": 900.0, "timestamp": HOUR_AGO}}


@pytest.mark.parametrize(
    "odometer, last, expected",
    [
        (None, {"odometer_km": 1.0}, "IGNORE"),
        (5.0, None, "SEED"),
        (5.0, {"odometer_km": "x"}, "SEED"),
        (5.0, {"odometer_km": True}, "SEED"),
        (5.0, {"odometer_km": 5.0}, "IGNORE"),
        (6.0, {"odometer_km": 5.0}, "ARM"),  # nothing to time it by
        (4.0, {"odometer_km": 5.0}, "CANDIDATE"),
        (255.0, STAMPED, "ARM"),  # 250 km in an hour, plus the slack
        (261.0, STAMPED, "CANDIDATE"),
        (900.0, WITH_CANDIDATE, "IGNORE"),  # the candidate, repeated
        (950.0, WITH_CANDIDATE, "ARM"),  # follows on from the candidate
        (1.0, WITH_CANDIDATE, "CANDIDATE"),  # agrees with neither
    ],
)
def test_report_decision(odometer, last, expected) -> None:
    decision = REPORT.report_decision(odometer, last, timestamp=NOW.isoformat(), now=NOW)
    assert decision.kind == getattr(REPORT, expected)


def test_an_arm_from_the_candidate_is_measured_from_it() -> None:
    decision = REPORT.report_decision(950.0, WITH_CANDIDATE, timestamp=NOW.isoformat(), now=NOW)
    assert decision.baseline["odometer_km"] == 900.0


def test_the_longer_clock_decides() -> None:
    """Car clock stuck (zero elapsed); ours says a day: the drive is allowed."""

    base = {
        "odometer_km": 5.0,
        "timestamp": NOW.isoformat(),
        "recorded_at": (NOW - timedelta(days=1)).isoformat(),
    }
    assert REPORT.plausible_step(base, 1000.0, timestamp=NOW.isoformat(), now=NOW)
    assert not REPORT.plausible_step(base, 9000.0, timestamp=NOW.isoformat(), now=NOW)


@pytest.mark.parametrize("stamp", [None, "", "garbage", "2026-09-26T12:00:00"])
def test_an_unusable_timestamp_is_ignored_not_trusted(stamp) -> None:
    base = {"odometer_km": 5.0, "timestamp": stamp, "recorded_at": stamp}
    assert REPORT.plausible_step(base, 6.0, timestamp=stamp, now=NOW)
