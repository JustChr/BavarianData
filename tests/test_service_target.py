"""Which account and which car a service call means (``service_target.py``).

With one car in one account every call lands right whatever it passes. These
pin what happens once there are two: a call that could mean either is refused
with the choices named, instead of acting on whichever car streamed first.
"""

from __future__ import annotations

import pytest

from .conftest import load_module

target = load_module("service_target")
TargetError = target.TargetError

I5 = "WBAEXAMPLE0000001"
MINI = "WMWEXAMPLE0000002"


# --- the car ----------------------------------------------------------------


def test_a_lone_car_needs_no_naming() -> None:
    assert target.choose_vin(None, [I5]) == I5


def test_two_cars_and_none_named_is_refused_with_both_listed() -> None:
    with pytest.raises(TargetError) as err:
        target.choose_vin(None, [I5, MINI], {I5: "i5 eDrive40"})
    message = str(err.value)
    assert "2 cars" in message
    assert "i5 eDrive40" in message
    # The unnamed car is listed by its masked VIN, never the full one.
    assert "***0002" in message and MINI not in message


def test_a_service_that_reads_every_car_reads_them_all() -> None:
    assert target.choose_vin(None, [I5, MINI], required=False) is None


def test_a_named_car_has_to_be_the_accounts() -> None:
    assert target.choose_vin(MINI, [I5, MINI]) == MINI
    with pytest.raises(TargetError, match="not a car of this account"):
        target.choose_vin("WBA/../x?y=1", [I5])
    # Also when every car would have been read: a filter on a stranger is a typo.
    with pytest.raises(TargetError):
        target.choose_vin("WBATYPO", [I5], required=False)


def test_an_account_without_a_car_says_so() -> None:
    with pytest.raises(TargetError, match="no car yet"):
        target.choose_vin(None, [])


# --- vin and device together -------------------------------------------------


def test_vin_and_device_may_agree_or_stand_alone() -> None:
    assert target.requested_vin(I5, None) == I5
    assert target.requested_vin(None, I5) == I5
    assert target.requested_vin(f" {I5} ", I5) == I5
    assert target.requested_vin("", None) is None


def test_vin_and_device_naming_different_cars_is_refused() -> None:
    with pytest.raises(TargetError, match="different cars"):
        target.requested_vin(I5, MINI)


# --- the account -------------------------------------------------------------


ACCOUNTS = {"entry_bmw": "i5 eDrive40", "entry_mini": "MINI Cooper SE"}


def test_a_lone_account_needs_no_naming() -> None:
    assert target.choose_account({"entry_bmw": "i5"}) == "entry_bmw"


def test_two_accounts_and_nothing_named_lists_them() -> None:
    with pytest.raises(TargetError) as err:
        target.choose_account(ACCOUNTS)
    assert "i5 eDrive40" in str(err.value) and "MINI Cooper SE" in str(err.value)


def test_explicit_entry_wins_then_device_then_vin() -> None:
    assert (
        target.choose_account(
            ACCOUNTS, entry_id="entry_mini", device_entry="entry_bmw", vin=I5, vin_entry="entry_bmw"
        )
        == "entry_mini"
    )
    assert target.choose_account(ACCOUNTS, device_entry="entry_mini") == "entry_mini"
    assert target.choose_account(ACCOUNTS, vin=MINI, vin_entry="entry_mini") == "entry_mini"


def test_an_unknown_entry_or_vin_is_refused() -> None:
    with pytest.raises(TargetError, match="no_such"):
        target.choose_account(ACCOUNTS, entry_id="no_such")
    with pytest.raises(TargetError, match="No BavarianData account has a car"):
        target.choose_account(ACCOUNTS, vin="WBATYPO")


def test_a_lone_account_still_gets_the_vin_checked_later() -> None:
    """One account and a VIN no device carries: the account is the only
    candidate, and ``choose_vin`` then refuses the stranger with the clearer
    "not a car of this account"."""

    assert target.choose_account({"entry_bmw": "i5"}, vin="WBATYPO") == "entry_bmw"


def test_nothing_loaded_is_refused() -> None:
    with pytest.raises(TargetError, match="No BavarianData account"):
        target.choose_account({})
