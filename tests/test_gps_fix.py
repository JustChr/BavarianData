"""The tracker moves only on a whole fix (``gps_fix``).

On the live i5 three of nine position changes in a day landed one half at a
time, each a phantom point for ~40 ms in the recorder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .conftest import load_module

gps_fix = load_module("gps_fix")
car_signals = load_module("car_signals")

T0 = datetime(2026, 11, 21, 10, 40, tzinfo=UTC)
FIX1 = "2026-11-21T10:39:57.000Z"
FIX2 = "2026-11-21T10:40:04.000Z"


def _settled() -> object:
    gate = gps_fix.FixGate()
    assert gate.offer(48.1, FIX1, 11.5, FIX1, T0)
    return gate


def test_a_half_waits_for_its_mate() -> None:
    gate = _settled()
    # The new longitude lands first, the latitude is still the last fix's.
    assert not gate.offer(48.1, FIX1, 11.6, FIX2, T0 + timedelta(seconds=1))
    assert gate.position == (48.1, 11.5)
    assert gate.offer(48.2, FIX2, 11.6, FIX2, T0 + timedelta(seconds=2))
    assert gate.position == (48.2, 11.6)
    assert gate.waiting_since is None


def test_a_repeat_of_the_same_fix_changes_nothing() -> None:
    gate = _settled()
    assert not gate.offer(48.1, FIX1, 11.5, FIX1, T0 + timedelta(seconds=5))
    assert not gate.offer("48.1", FIX1, "11.5", FIX1, T0 + timedelta(seconds=6))


def test_a_mate_that_never_comes_is_given_up_on() -> None:
    gate = _settled()
    stale = timedelta(seconds=car_signals.GPS_PAIR_STALE_S)
    assert not gate.offer(48.1, FIX1, 11.6, FIX2, T0)
    assert not gate.offer(48.1, FIX1, 11.6, FIX2, T0 + stale - timedelta(seconds=1))
    assert gate.offer(48.1, FIX1, 11.6, FIX2, T0 + stale)
    assert gate.position == (48.1, 11.6)
    assert gate.waiting_since is None


def test_a_reading_without_a_timestamp_is_taken_as_it_stands() -> None:
    gate = gps_fix.FixGate()
    assert gate.offer(48.1, None, 11.5, FIX1, T0)
    assert gate.position == (48.1, 11.5)


def test_a_missing_or_unreadable_half_shows_no_position() -> None:
    gate = _settled()
    assert gate.offer("somewhere", FIX2, 11.6, FIX2, T0)
    assert gate.position is None
    assert not gate.offer(None, None, None, None, T0)
