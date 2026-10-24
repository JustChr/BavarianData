"""The stream's health: outages for the catch-up, and when a repair is due.

A long outage loses whatever the cars sent meanwhile, just as a restart does, so
the coordinator reports how long the stream was away when it connects again.
The first connect after setup has no outage behind it and must report nothing
(the startup catch-up covers it); a reconnect storm is one outage, measured from
its first drop. ``stream_health.py`` decides all of it, and the two repairs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

stream_health = load_module("stream_health")
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _at(minute: float) -> datetime:
    return T0 + timedelta(minutes=minute)


def _run(events: list[tuple[int, str]]) -> list[float]:
    """Feed (minute, status) events; return the outage lengths reported."""

    health = stream_health.StreamHealth()
    outages = [health.note(status, None, _at(minute)) for minute, status in events]
    return [outage for outage in outages if outage is not None]


def test_the_first_connect_after_setup_is_not_an_outage():
    assert _run([(0, "connected")]) == []


def test_a_reconnect_reports_how_long_the_stream_was_away():
    assert _run([(0, "connected"), (10, "disconnected"), (40, "connected")]) == [30 * 60]


def test_a_reconnect_storm_is_one_outage_from_its_first_drop():
    events = [
        (0, "connected"),
        (10, "disconnected"),
        (20, "unauthorized"),
        (30, "disconnected"),
        (70, "connected"),
    ]
    assert _run(events) == [60 * 60]


def test_a_failed_first_connect_is_not_an_outage_either():
    """Setup that took a while to connect is the startup catch-up's business."""

    assert _run([(0, "disconnected"), (30, "connected")]) == []


def test_the_reason_stays_until_a_connect_clears_it():
    health = stream_health.StreamHealth()
    health.note("disconnected", "rc=7", _at(0))
    health.note("connecting", None, _at(1))
    assert health.last_disconnect_reason == "rc=7"
    health.note("connected", None, _at(2))
    assert health.last_disconnect_reason is None
    assert [row["status"] for row in health.history] == ["disconnected", "connecting", "connected"]


def test_the_history_keeps_the_last_transitions_only():
    health = stream_health.StreamHealth()
    for minute in range(stream_health.CONNECTION_HISTORY_LEN + 5):
        health.note("connected" if minute % 2 else "disconnected", None, _at(minute))
    assert len(health.history) == stream_health.CONNECTION_HISTORY_LEN


def _due(health, minute, *, last_message=None, started=0):
    return health.repairs_due(
        _at(minute),
        last_message_at=None if last_message is None else _at(last_message),
        started_at=None if started is None else _at(started),
    )


def test_a_persisting_rc5_is_a_repair_and_hides_the_no_data_one():
    health = stream_health.StreamHealth()
    health.note("unauthorized", "rc=5", _at(0))
    after = stream_health.UNAUTHORIZED_REPAIR_AFTER_S / 60

    assert _due(health, after - 1) == (False, False)
    # Two days silent as well: only the more specific diagnosis stands.
    assert _due(health, 3 * 24 * 60, last_message=0) == (True, False)
    health.note("connected", None, _at(after + 1))
    assert _due(health, after + 2) == (False, False)


def test_two_silent_days_are_a_repair_counted_from_the_last_message():
    health = stream_health.StreamHealth()
    hours = stream_health.NO_DATA_REPAIR_AFTER_S / 3600

    assert _due(health, hours * 60 - 1, last_message=0) == (False, False)
    assert _due(health, hours * 60, last_message=0) == (False, True)
    # A message since resets the clock.
    assert _due(health, hours * 60, last_message=60) == (False, False)


def test_a_stream_that_never_delivered_counts_from_its_start():
    health = stream_health.StreamHealth()
    hours = stream_health.NO_DATA_REPAIR_AFTER_S / 3600

    assert _due(health, hours * 60, started=0) == (False, True)
    assert _due(health, hours * 60, started=None) == (False, False)
