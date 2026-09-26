"""When a restart may spend a REST request on an unconfirmed charge.

The request is on by default, so what keeps it cheap is that it is *usually
not made*: only for a car whose restored charge the stream has not confirmed,
and never twice inside the spacing window however often Home Assistant restarts.
"""

from __future__ import annotations

from tests.conftest import load_module

refresh = load_module("startup_refresh")
pick = refresh.startup_refresh_vins
SPACING = refresh.STARTUP_REFRESH_MIN_SPACING_S
NOW = 1_800_000_000.0


def test_no_open_charge_costs_nothing() -> None:
    assert pick(enabled=True, unconfirmed=[], last_refresh_at=None, now=NOW) == []


def test_an_unconfirmed_charge_is_checked() -> None:
    assert pick(enabled=True, unconfirmed=["VIN1"], last_refresh_at=None, now=NOW) == ["VIN1"]


def test_every_unconfirmed_car_is_checked_once() -> None:
    got = pick(enabled=True, unconfirmed=["VIN1", "VIN2", "VIN1"], last_refresh_at=None, now=NOW)
    assert got == ["VIN1", "VIN2"]


def test_switched_off_means_never() -> None:
    assert pick(enabled=False, unconfirmed=["VIN1"], last_refresh_at=None, now=NOW) == []


def test_a_restart_loop_is_spaced_out() -> None:
    recent = NOW - SPACING + 60
    assert pick(enabled=True, unconfirmed=["VIN1"], last_refresh_at=recent, now=NOW) == []
    old = NOW - SPACING - 1
    assert pick(enabled=True, unconfirmed=["VIN1"], last_refresh_at=old, now=NOW) == ["VIN1"]


def test_the_answer_lands_before_the_restored_session_gives_up() -> None:
    """The refresh must beat the grace timer, or it only confirms a split."""

    coordinator_src = (load_module.__globals__["_BASE"] / "coordinator.py").read_text(
        encoding="utf-8"
    )
    grace = next(
        int(line.split("=")[1].split("#")[0])
        for line in coordinator_src.splitlines()
        if line.startswith("RESTORED_SESSION_GRACE_S =")
    )
    assert refresh.STARTUP_REFRESH_DELAY_S < grace
