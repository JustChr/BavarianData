"""Unit tests for surviving a restart in the middle of a charge.

An in-progress session used to live only in memory, so a Home Assistant restart
(or an options reload) deleted it: measured on a live instance, two restarts
that happened to land mid-charge cost about 22 kWh in one week, and every total
built on the ledger read low by that much. The snapshot/restore path that fixes
it is exercised here -- the Home Assistant-free half of it, which is the half
that decides whether a number survives and what it is worth.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .conftest import load_module

sessions = load_module("history.sessions")
pricing = load_module("history.pricing")

SessionBuilder = sessions.SessionBuilder
CostAccumulator = pricing.CostAccumulator

START = datetime(2026, 9, 5, 12, 22, tzinfo=timezone.utc)


def _charging_builder() -> SessionBuilder:
    """A session mid-charge: ninety minutes in, SoC climbing, curve growing."""

    builder = SessionBuilder(
        "WBY1",
        START,
        soc_start=69.0,
        target_soc=100.0,
        location={"zone": "Home"},
    )
    for minute in range(0, 90, 5):
        builder.sample(START + timedelta(minutes=minute), 10.8)
    builder.note_soc(88.0)
    return builder


# --- the snapshot ----------------------------------------------------------


def test_snapshot_round_trips_through_json():
    """Everything needed to carry on must survive a real store write."""

    original = _charging_builder()
    restored = SessionBuilder.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored is not None
    assert restored.vin == original.vin
    assert restored.start == original.start
    assert restored.soc_start == 69.0
    assert restored.soc_end == 88.0
    assert restored.target_soc == 100.0
    assert restored.location == {"zone": "Home"}
    assert restored.peak_power_kw == original.peak_power_kw
    assert restored.last_sample_at == original.last_sample_at


def test_restored_session_keeps_its_curve_and_sampling_state():
    """A resumed half must not be denser than the half before the restart."""

    original = _charging_builder()
    # Force the downsampler past its first halving so the interval is not the
    # default any more -- restoring the points but not the interval would make
    # the rest of the session sample twice as often.
    original._decimate()
    restored = SessionBuilder.from_dict(original.to_dict())

    assert restored._curve == original._curve
    assert restored._interval == original._interval

    # And it keeps growing where it left off rather than restarting at zero.
    resumed_at = START + timedelta(minutes=95)
    restored.sample(resumed_at, 10.8)
    assert restored._curve[-1][0] == int((resumed_at - START).total_seconds())


def test_restored_session_is_flagged_interrupted():
    """The record has to admit there is a hole in it."""

    restored = SessionBuilder.from_dict(_charging_builder().to_dict())
    assert restored.interrupted is True

    record = restored.close(START + timedelta(minutes=95), energy_kwh=15.9)
    assert record.interrupted is True
    # ``late_start`` says something different (the beginning was missed) and must
    # not be invented by a restore.
    assert record.late_start is False


def test_interrupted_flag_survives_the_record_round_trip():
    models = load_module("history.models")
    record = SessionBuilder.from_dict(_charging_builder().to_dict()).close(
        START + timedelta(minutes=95), energy_kwh=15.9
    )
    back = models.ChargingSession.from_dict(json.loads(json.dumps(record.to_dict())))
    assert back is not None and back.interrupted is True


def test_unusable_snapshots_are_refused():
    """Junk in the store must not raise on the setup path."""

    assert SessionBuilder.from_dict(None) is None
    assert SessionBuilder.from_dict({}) is None
    assert SessionBuilder.from_dict({"vin": "WBY1"}) is None
    assert SessionBuilder.from_dict({"start": START.isoformat()}) is None


def test_a_closed_restored_session_ends_at_its_last_sample():
    """Not at the moment we found out, which can be hours later.

    The car stopped charging somewhere inside the gap; stretching the record to
    "now" would inflate its duration and its average power.
    """

    restored = SessionBuilder.from_dict(_charging_builder().to_dict())
    record = restored.close(restored.last_sample_at, energy_kwh=15.9)

    assert record.end == START + timedelta(minutes=85)
    assert record.duration_s == 85 * 60


# --- how stale is too stale ------------------------------------------------


def test_a_fresh_snapshot_is_resumable():
    saved = START + timedelta(minutes=85)
    assert sessions.open_session_is_resumable(saved, saved + timedelta(minutes=2))


def test_an_old_snapshot_is_not_resumed():
    """Beyond the window the car has likely been unplugged and driven."""

    saved = START
    assert not sessions.open_session_is_resumable(saved, saved + timedelta(days=2))


def test_a_snapshot_without_a_time_is_not_resumed():
    assert not sessions.open_session_is_resumable(None, START)


def test_a_snapshot_from_the_future_is_not_resumed():
    """A clock change must close the record, not keep accruing into it."""

    assert not sessions.open_session_is_resumable(START, START - timedelta(hours=1))


# --- the running cost ------------------------------------------------------


def test_cost_accumulator_round_trips():
    accumulator = CostAccumulator(currency="EUR")
    accumulator.add(5.0, 0.30)
    accumulator.add(2.0, None)
    restored = CostAccumulator.from_dict(
        json.loads(json.dumps(accumulator.to_dict())), currency="EUR"
    )

    assert restored.amount == accumulator.amount
    assert restored.priced_kwh == 5.0
    assert restored.unpriced_kwh == 2.0

    # And it goes on billing from there rather than from zero.
    restored.add(5.0, 0.30)
    assert restored.as_cost()["amount"] == 3.0


def test_a_snapshot_in_another_currency_is_not_summed():
    """Money in two currencies does not add up, so the priced half reverts.

    The energy is not lost -- it becomes unpriced, which the record reports as a
    partial cost instead of quietly understating a total in the wrong unit.
    """

    accumulator = CostAccumulator(currency="EUR")
    accumulator.add(10.0, 0.30)
    restored = CostAccumulator.from_dict(accumulator.to_dict(), currency="GBP")

    assert restored.currency == "GBP"
    assert restored.amount == 0.0
    assert restored.priced_kwh == 0.0
    assert restored.unpriced_kwh == 10.0
    assert restored.as_cost() is None


def test_a_corrupt_cost_snapshot_starts_clean():
    restored = CostAccumulator.from_dict(
        {"currency": "EUR", "amount": "lots"}, currency="EUR"
    )
    assert restored.amount == 0.0
    assert restored.total_kwh == 0.0
    assert CostAccumulator.from_dict(None, currency="EUR").currency == "EUR"


# --- what an interrupted record may not be used for ------------------------


def test_an_interrupted_session_is_not_a_capacity_sample():
    """Its SoC span is whole but its energy has a hole in it.

    And the hole is filled from a credit that already assumes a capacity, so
    dividing one by the other would measure the assumption rather than the pack.
    """

    models = load_module("history.models")
    health = load_module("history.health")

    clean = models.ChargingSession(
        vin="WBY1",
        start=START,
        end=START + timedelta(hours=3),
        soc_start=20.0,
        soc_end=80.0,
        energy_kwh=45.0,
    )
    assert health._capacity_sample(clean) is not None

    interrupted = models.ChargingSession(
        vin="WBY1",
        start=START,
        end=START + timedelta(hours=3),
        soc_start=20.0,
        soc_end=80.0,
        energy_kwh=45.0,
        interrupted=True,
    )
    assert health._capacity_sample(interrupted) is None
