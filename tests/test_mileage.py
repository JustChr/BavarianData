"""The mileage overview and allowance: read off the daily odometer log.

The log keeps one reading a day; months are interpolated between readings, the
lease total is the yearly allowance times the contract's length, and a yearly
limit renews on its anniversary with the odometer the log had that day.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from .conftest import load_module

mileage = load_module("mileage")

TODAY = date(2026, 10, 14)


def _steady(first: date, last: date, km_per_day: float, start_km: float = 10000.0) -> dict:
    """A car driving the same distance every day; a reading each day."""

    log = {}
    day, km = first, start_km
    while day <= last:
        log[day.isoformat()] = km
        day += timedelta(days=1)
        km += km_per_day
    return log


def test_odometer_at_interpolates_between_readings():
    log = {"2026-10-01": 1000.0, "2026-10-11": 2000.0}
    # The 1 Oct reading stands for the start of 2 Oct.
    assert mileage.odometer_at(log, date(2026, 10, 2)) == 1000.0
    assert mileage.odometer_at(log, date(2026, 10, 7)) == 1500.0
    assert mileage.odometer_at(log, date(2026, 10, 20)) == 2000.0
    assert mileage.odometer_at(log, date(2026, 9, 1)) is None


def test_overview_of_a_steady_car():
    log = _steady(date(2025, 6, 1), TODAY, 40.0)
    report = mileage.mileage_report(log, TODAY)
    assert report["km_per_day"] == 40.0
    assert report["projected_year_km"] == 14600
    # 1 Oct start of day .. end of 14 Oct: 14 days.
    assert report["this_month"] == {"km": 560, "partial": False}
    assert report["this_year"]["km"] == 40 * (TODAY - date(2026, 1, 1)).days + 40
    months = report["months"]
    assert len(months) == 12 and months[-1]["month"] == "2026-10"
    assert months[-2] == {"month": "2026-09", "km": 1200, "partial": False}
    assert months[-1]["partial"]
    assert report["allowance"] is None


def test_a_young_log_marks_its_first_month_partial_and_leaves_older_ones_empty():
    log = _steady(date(2026, 9, 20), TODAY, 30.0)
    report = mileage.mileage_report(log, TODAY)
    months = {row["month"]: row for row in report["months"]}
    assert months["2026-08"]["km"] is None
    assert (
        months["2026-09"]["partial"] and months["2026-09"]["km"] == 300
    )  # from the end of the 20th
    assert report["this_year"]["partial"]
    assert report["km_per_day"] is None  # 24 days is no pace yet


def test_an_empty_log_gives_an_empty_overview():
    report = mileage.mileage_report({}, TODAY)
    assert report["odometer_km"] is None and report["this_month"] is None
    assert all(row["km"] is None for row in report["months"])


LEASE = {
    "mileage_kind": "lease",
    "mileage_start": "2025-10-14",
    "mileage_end": "2028-10-13",
    "mileage_allowance_km": 15000,
    "mileage_start_km": 10000,
}


def test_a_lease_under_plan():
    log = _steady(date(2025, 10, 14), TODAY, 35.0)  # 12,775 km a year against 15,000
    status = mileage.mileage_report(log, TODAY, LEASE)["allowance"]
    assert status["status"] == "ok"
    assert status["allowance_km"] == round(
        15000 * (date(2028, 10, 13) - date(2025, 10, 14)).days / 365.25
    )
    assert status["used_km"] == 35 * 365
    assert status["balance_km"] > 0
    assert status["projected_over_km"] < 0
    assert status["km_per_day_left"] > 35


def test_a_lease_without_its_handover_reading_is_unknown_not_guessed():
    lease = {**LEASE, "mileage_start_km": None}
    status = mileage.mileage_report(_steady(date(2026, 1, 1), TODAY, 30.0), TODAY, lease)[
        "allowance"
    ]
    assert status["status"] == "unknown" and "used_km" not in status


def test_a_lease_that_has_not_started_or_has_ended():
    later = {**LEASE, "mileage_start": "2027-01-01", "mileage_end": "2030-01-01"}
    assert mileage.mileage_report({}, TODAY, later)["allowance"]["status"] == "not_started"
    ended = {**LEASE, "mileage_start": "2023-01-01", "mileage_end": "2026-01-01"}
    log = _steady(date(2023, 1, 1), TODAY, 30.0)
    status = mileage.mileage_report(log, TODAY, ended)["allowance"]
    assert status["status"] == "ended" and status["days_left"] == 0


def test_a_yearly_limit_renews_and_reads_the_anniversary_from_the_log():
    yearly = {
        "mileage_kind": "yearly",
        "mileage_start": "2025-03-01",
        "mileage_allowance_km": 12000,
        "mileage_start_km": 5000,  # 1 Mar 2025, only for the first period
    }
    log = _steady(date(2025, 1, 1), TODAY, 30.0)
    status = mileage.mileage_report(log, TODAY, yearly)["allowance"]
    assert (status["start"], status["end"]) == ("2026-03-01", "2027-03-01")
    assert status["allowance_km"] == 12000
    assert status["used_km"] == 30 * (TODAY - date(2026, 3, 1)).days + 30


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"mileage_kind": "none"},
        {**LEASE, "mileage_end": "2020-01-01"},
        {**LEASE, "mileage_allowance_km": 0},
        {**LEASE, "mileage_start": "soon"},
    ],
)
def test_no_or_broken_allowance_is_none(values):
    assert mileage.allowance_settings(values) is None


def test_29_february_renews_on_the_28th():
    assert mileage.yearly_period(date(2024, 2, 29), date(2025, 6, 1)) == (
        date(2025, 2, 28),
        date(2026, 2, 28),
    )


def test_the_coordinator_reports_a_car_against_its_lease():
    from .harness import CoordinatorHarness

    with CoordinatorHarness() as h:
        day = h.at(0).date()
        # Two months of readings from the charging history, as a fresh upgrade has.
        log = h.coordinator.history._odometer.setdefault(h.vin, {})
        log[(day - timedelta(days=60)).isoformat()] = 10000.0
        h.coordinator.mileage_allowances = {
            h.vin: {
                "mileage_kind": "lease",
                "mileage_start": (day - timedelta(days=60)).isoformat(),
                "mileage_end": (day + timedelta(days=305)).isoformat(),
                "mileage_allowance_km": 12000,
                "mileage_start_km": 10000,
            }
        }
        h.send(10, odometer=12400, step="drove")
        report = h.coordinator.mileage_report(h.vin)
        assert report["km_per_day"] == pytest.approx(40.0)
        allowance = report["allowance"]
        assert allowance["used_km"] == 2400
        assert allowance["status"] == "ok"
        assert allowance["balance_km"] < 0  # 40 km a day against ~33 planned
        # Another car on the account has no allowance.
        assert h.coordinator.mileage_report("WBA00000000000099")["allowance"] is None
