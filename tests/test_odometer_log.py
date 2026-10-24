"""The daily odometer log under the mileage overview and the service calendar."""

from __future__ import annotations

from datetime import date, timedelta

from .conftest import load_module

odometer = load_module("history.odometer")

DAY = date(2026, 10, 24)


def test_no_reading_or_a_negative_one_is_ignored() -> None:
    log: dict = {}
    assert not odometer.note_reading(log, DAY, None)
    assert not odometer.note_reading(log, DAY, -1)
    assert log == {}


def test_an_odometer_never_runs_back() -> None:
    log = {"2026-10-20": 1000.0}
    assert not odometer.note_reading(log, DAY, 990)
    assert odometer.note_reading(log, DAY, 1010.04)
    assert not odometer.note_reading(log, DAY, 1010)
    assert log == {"2026-10-20": 1000.0, "2026-10-24": 1010.0}


def test_a_later_day_holding_less_was_a_stale_value() -> None:
    log = {"2026-10-20": 1000.0, "2026-10-26": 1005.0}
    assert odometer.note_reading(log, DAY, 1010)
    assert log == {"2026-10-20": 1000.0, "2026-10-24": 1010.0}


def test_the_log_keeps_ten_years_of_days() -> None:
    first = DAY - timedelta(days=odometer.MAX_DAYS)
    log = {(first + timedelta(days=n)).isoformat(): float(n) for n in range(odometer.MAX_DAYS)}
    assert odometer.note_reading(log, DAY, odometer.MAX_DAYS)
    assert len(log) == odometer.MAX_DAYS
    assert first.isoformat() not in log


def test_unreadable_entries_are_skipped() -> None:
    log = {"2026-10-20": 1000.0, "someday": 5.0, "2026-10-21": None}
    assert odometer.readings(log) == [(date(2026, 10, 20), 1000.0)]


def test_the_newest_reading_on_or_before_a_day() -> None:
    log = {"2026-10-20": 1000.0, "2026-10-22": 1100.0, "2026-10-25": 1200.0}
    assert odometer.reading_on_or_before(log, DAY) == (date(2026, 10, 22), 1100.0)
    assert odometer.reading_on_or_before(log, date(2026, 10, 1)) is None
