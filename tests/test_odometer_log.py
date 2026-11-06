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


def _days(*kms: float) -> dict:
    """A log of one reading a day, ending the day before DAY."""

    first = DAY - timedelta(days=len(kms))
    return {(first + timedelta(days=n)).isoformat(): float(km) for n, km in enumerate(kms)}


def test_a_reading_no_car_could_drive_to_gives_way_to_the_next_true_one() -> None:
    # A km reading taken for miles lands 1.6 times too high. Kept, it held every
    # true reading below it out of the log until the car caught up, months on.
    log = _days(20000, 20050, 32190)
    assert odometer.note_reading(log, DAY, 20120)
    assert log == {"2026-10-21": 20000.0, "2026-10-22": 20050.0, "2026-10-24": 20120.0}


def test_a_spike_is_replaced_on_its_own_day() -> None:
    log = _days(20000, 20050)
    assert odometer.note_reading(log, DAY, 32190)
    assert odometer.note_reading(log, DAY, 20090)
    assert log[DAY.isoformat()] == 20090.0


def test_several_wrong_days_go_together() -> None:
    log = _days(20000, 32190, 32230)
    assert odometer.note_reading(log, DAY, 20100)
    assert log == {"2026-10-21": 20000.0, "2026-10-24": 20100.0}


def test_a_stale_replay_still_cannot_fold_the_log() -> None:
    log = _days(20000, 20050, 20100)
    assert not odometer.note_reading(log, DAY, 20020)
    assert log == _days(20000, 20050, 20100)


def test_a_long_drive_is_no_spike() -> None:
    # 1,500 km in a day is a lot, but a car can do it; a replay of the morning
    # reading must not undo it.
    log = _days(20000, 21500)
    assert not odometer.note_reading(log, DAY, 20010)
    assert log == _days(20000, 21500)


def test_a_wrong_first_reading_is_not_judged() -> None:
    # Nothing to measure it from: which of the two is wrong can't be told.
    log = _days(32190)
    assert not odometer.note_reading(log, DAY, 20000)
    assert log == _days(32190)


def test_a_true_reading_below_a_spike_that_is_itself_too_far_is_ignored() -> None:
    log = _days(20000, 32190)
    assert not odometer.note_reading(log, DAY, 26500)
    assert log == _days(20000, 32190)


def test_an_unreadable_day_does_not_stop_the_check() -> None:
    # The store keeps only numbers, but not only dates.
    log = {**_days(20000, 20050, 32190), "someday": 5.0}
    assert odometer.note_reading(log, DAY, 20120)
    assert "2026-10-23" not in log
