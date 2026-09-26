"""Charging, the SoC estimate and restarts, run through the real coordinator.

Each scenario states the behaviour it is about in plain assertions, and pins
its whole timeline to a snapshot: the assertions say what matters, the snapshot
catches everything else that moves. A change that alters a timeline fails with
a readable diff -- approve an intended one with
``python -m pytest tests/test_scenarios_charging.py --snapshot-update`` and read
the diff, as with the card snapshots.

Times are minutes after 08:00 UTC. The car is a 75 kWh pack charging at
3.48 kW, which is the 4.64 %/h the maintainer's i5 showed on 26 September 2026.
"""

from __future__ import annotations

import pytest

from .harness import CoordinatorHarness

RATE = 3.48 / 75 * 100  # %/h
ORDERS = ["descriptors-first", "derived-first"]


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def _start_charge(h: CoordinatorHarness, minute: float = 10, soc: float = 38) -> None:
    h.send(minute, target=100, step="target 100")
    h.send(minute, soc=soc, status="CHARGINGACTIVE", power=3480)


# --- the estimate -----------------------------------------------------------


def test_a_silent_charge_keeps_the_estimate_climbing(h, snapshot):
    """BMW went silent for two hours mid-charge; the estimate is the live SoC."""

    _start_charge(h)
    h.advance_to(142)

    expected = 38 + RATE * (142 - 10) / 60
    assert h.estimate() == pytest.approx(expected, abs=0.05)
    assert h.render() == snapshot


def test_a_real_reading_replaces_the_estimate(h, snapshot):
    _start_charge(h)
    h.advance_to(142)
    h.send(142.4, soc=47, status="CHARGINGACTIVE", power=3520, step="app wakes the car")

    assert h.estimate() == 47
    assert h.render() == snapshot


def test_the_estimate_stops_at_the_charge_target(h, snapshot):
    h.send(10, target=50, step="target 50")
    h.send(10, soc=45, status="CHARGINGACTIVE", power=3480)
    h.advance_to(300)

    assert h.estimate() == 50
    assert h.render() == snapshot


def test_the_estimate_never_passes_full(h):
    h.send(10, soc=97, status="CHARGINGACTIVE", power=11000)
    h.advance_to(300)

    assert h.estimate() == 100


def test_without_a_capacity_there_is_no_rate_to_extrapolate(snapshot):
    with CoordinatorHarness(capacity_kwh=None) as h:
        h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
        h.advance_to(100)

        assert h.rate() is None
        assert h.estimate() == 38
        assert h.render() == snapshot


def test_a_stopped_charge_freezes_the_estimate(h):
    _start_charge(h)
    h.send(40, status="NOCHARGING", power=0, step="unplugged")
    frozen = h.estimate()
    h.advance_to(200)

    assert h.estimate() == frozen
    assert h.rate() is None


# --- sessions -----------------------------------------------------------------


def test_an_unplug_files_the_session_after_the_debounce(h, snapshot):
    _start_charge(h)
    h.send(40, soc=40, status="CHARGINGACTIVE", power=3480)
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    assert h.has_open_session()  # the flap debounce holds it open...
    h.advance_to(63)

    sessions = h.sessions()
    assert len(sessions) == 1  # ...and then it is filed
    assert sessions[0].end_reason == "NOCHARGING"
    assert [name for name, _ in h.events()] == [
        "bavariandata_charging_started",
        "bavariandata_charging_stopped",
    ]
    assert h.render() == snapshot


def test_a_status_flap_inside_the_debounce_is_one_session(h, snapshot):
    _start_charge(h)
    h.send(30, status="NOCHARGING", step="flap off")
    h.send(31, status="CHARGINGACTIVE", step="flap on")
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(65)

    assert len(h.sessions()) == 1
    assert h.render() == snapshot


def test_the_same_batch_twice_changes_nothing(h):
    _start_charge(h)
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480, stamped=20)
    before = (h.estimate(), h.rate(), list(h.hass.events))
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480, stamped=20, step="duplicate")

    assert (h.estimate(), h.rate(), list(h.hass.events)) == before


def test_the_started_event_knows_the_target_sent_alongside(h):
    """Found by this harness: a target in the same batch as the status is lost.

    ``charging.status`` fires the event while the batch is still being read, so
    a target further down the same batch is not known yet. Pinned as current
    behaviour; flip the assertion when it is fixed.
    """

    h.send(10, soc=38, status="CHARGINGACTIVE", power=3480, target=80)

    (_name, data), *_ = h.events()
    assert data["target_soc"] is None


# --- restarts -----------------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS)
def test_a_restart_mid_charge_keeps_the_estimate_climbing(order, snapshot):
    """The 0.9.12 fix, end to end: entities restore in either order."""

    with CoordinatorHarness() as h:
        _start_charge(h)
        h.advance_to(27.7)
        before = h.estimate()
        h.restart(downtime_s=80, order=order)
        h.advance_to(60)

        assert h.estimate() > before + 1.5
        assert h.estimate() == pytest.approx(38 + RATE * 50 / 60, abs=0.1)
        assert h.render() == snapshot


def test_a_restart_mid_charge_that_the_catch_up_confirms_stays_one_session(h, snapshot):
    """What happened live on 0.9.13-beta.2: the REST answer resumes the charge."""

    _start_charge(h)
    h.send(12.8, power=3480)
    h.advance_to(27.7)
    h.restart(downtime_s=80)
    # Two minutes later the catch-up returns BMW's cached last message.
    h.send(31, stamped=12.8, soc=38, status="CHARGINGACTIVE", power=3480, step="catch-up")
    h.advance_to(120)
    h.send(120, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(125)

    sessions = h.sessions()
    assert len(sessions) == 1
    assert sessions[0].end_reason == "NOCHARGING"
    assert "bavariandata_charging_stopped" in [name for name, _ in h.events()][-1:]
    assert h.render() == snapshot


def test_a_charge_that_ended_during_the_restart_is_filed_where_it_was_last_seen(h, snapshot):
    _start_charge(h)
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480)
    h.advance_to(27.7)
    h.restart(downtime_s=600)
    h.send(40, stamped=33, status="NOCHARGING", power=0, step="catch-up: it ended")

    sessions = h.sessions()
    assert len(sessions) == 1
    assert sessions[0].end <= h.at(29)  # not stretched to when we found out
    assert h.render() == snapshot


def test_a_restart_the_stream_never_answers_splits_the_charge(h, snapshot):
    """26 September before the catch-up existed: filed as ended, then reopened."""

    _start_charge(h)
    h.advance_to(27.7)
    h.restart(downtime_s=80)
    h.advance_to(142)
    h.send(142.4, soc=47, status="CHARGINGACTIVE", power=3520, step="app wakes the car")

    sessions = h.sessions()
    assert [s.end_reason for s in sessions] == ["restart"]
    assert h.has_open_session()  # the rest of the charge is a new session
    assert h.render() == snapshot


def test_a_restart_loop_mid_charge_never_duplicates_the_session(h):
    _start_charge(h)
    for minute in (20, 22, 24, 26):
        h.advance_to(minute)
        h.restart(downtime_s=30)
    h.send(30, stamped=10, soc=38, status="CHARGINGACTIVE", power=3480, step="catch-up")
    h.send(90, status="NOCHARGING", power=0)
    h.advance_to(95)

    assert len(h.sessions()) == 1


def test_a_restart_while_parked_changes_nothing(h):
    h.send(10, soc=60, status="NOCHARGING", power=0)
    h.restart(downtime_s=3600)
    h.advance_to(300)

    assert h.estimate() == 60
    assert h.rate() is None
    assert h.sessions() == []


def test_without_history_a_restart_does_not_extrapolate():
    """No restored session to vouch for the charge, so the estimate waits."""

    with CoordinatorHarness(history=False) as h:
        _start_charge(h)
        h.advance_to(27.7)
        held = h.estimate()
        h.restart(downtime_s=80)
        h.advance_to(100)

        assert h.estimate() == pytest.approx(held, abs=0.01)
