"""Which cars a start may spend REST requests on.

The catch-up is on by default and fetches every car, so what keeps it from
eating the 50-a-day quota is when it is *not* made: not again within the hour,
not into the reserve, and a charge the restart left unconfirmed first in line.
"""

from __future__ import annotations

from tests.conftest import load_module

refresh = load_module("startup_refresh")
FRESH = refresh.STARTUP_REFRESH_FRESH_S
CHARGE = refresh.STARTUP_CHARGE_REFRESH_SPACING_S
RESERVE = refresh.STARTUP_REFRESH_RESERVE
NOW = 1_800_000_000.0
CARS = ["VIN1", "VIN2"]


def pick(*, enabled=True, vins=CARS, unconfirmed=(), last_fetch_at=None, remaining=50):
    return refresh.startup_refresh_vins(
        enabled=enabled,
        vins=vins,
        unconfirmed=unconfirmed,
        last_fetch_at=last_fetch_at,
        now=NOW,
        remaining=remaining,
    )


def test_every_car_catches_up_after_a_start() -> None:
    assert pick() == CARS


def test_switched_off_means_never() -> None:
    assert pick(enabled=False, unconfirmed=["VIN1"]) == []


def test_an_unconfirmed_charge_goes_first() -> None:
    assert pick(unconfirmed=["VIN2"]) == ["VIN2", "VIN1"]


def test_a_recent_fetch_skips_the_catch_up() -> None:
    """A restart loop costs nothing after the first start in an hour."""

    assert pick(last_fetch_at=NOW - FRESH + 60) == []
    assert pick(last_fetch_at=NOW - FRESH) == CARS


def test_an_unconfirmed_charge_is_asked_about_sooner() -> None:
    between = NOW - CHARGE - 1  # older than the charge spacing, inside the hour
    assert pick(unconfirmed=["VIN1"], last_fetch_at=between) == ["VIN1"]
    assert pick(unconfirmed=["VIN1"], last_fetch_at=NOW - CHARGE + 60) == []


def test_the_reserve_is_never_spent() -> None:
    assert pick(remaining=RESERVE + 1) == ["VIN1"]
    assert pick(remaining=RESERVE) == []
    assert pick(unconfirmed=["VIN2"], remaining=RESERVE + 1) == ["VIN2"]


def test_a_car_seen_only_as_unconfirmed_is_still_fetched() -> None:
    assert pick(vins=[], unconfirmed=["VIN1"]) == ["VIN1"]


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


def test_a_long_outage_earns_the_same_catch_up_as_a_start():
    needs = refresh.outage_needs_catch_up
    after = refresh.OUTAGE_CATCH_UP_AFTER_S

    assert needs(after)
    assert needs(9 * 60 * 60)  # the DNS outage that motivated the stream fixes
    assert not needs(after - 1)  # the stream's own reconnects
    assert not needs(None)  # the first connect after setup
