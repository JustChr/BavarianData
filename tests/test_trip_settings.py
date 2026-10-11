"""Per-car trip settings: which values apply to a car, and how a form stores them.

The rule a user relies on is "what the form shows is what applies": a car with
settings of its own uses all of them, every other car uses the account-wide
ones, and nothing is inherited key by key.
"""

from __future__ import annotations

from .conftest import load_module

SETTINGS = load_module("trip_settings")
CONST = load_module("const")

A = "WBATEST0000000001"
B = "WBATEST0000000002"

ACCOUNT = {
    "trip_work_zone": "zone.office",
    "trip_default_class": "business",
    "trip_commute_gap": 45,
    "trip_detection": "live",
    "trip_track": True,
    "trip_geocode": True,
}


def test_a_car_without_its_own_settings_uses_the_account_wide_ones() -> None:
    options = {**ACCOUNT, "vehicles": {B: {"trip_detection": "reports"}}}

    assert SETTINGS.vehicle_trip_values(options, A)["trip_default_class"] == "business"
    account = SETTINGS.account_trip_settings(options)
    assert account.work_zone_entity == "zone.office"
    assert account.commute_gap_s == 45 * 60
    assert account.record_track


def test_a_car_with_its_own_settings_inherits_nothing() -> None:
    options = {**ACCOUNT, "vehicles": {B: {"trip_detection": "reports"}}}

    own = SETTINGS.vehicle_trip_settings(options)[B]
    assert own.report_trips
    # Not the account's office zone, business class or route recording.
    assert own.work_zone_entity is None
    assert own.default_class == CONST.DEFAULT_TRIP_DEFAULT_CLASS
    assert not own.record_track
    assert SETTINGS.vehicle_trip_values(options, B) == {"trip_detection": "reports"}


def test_unrecognised_values_fall_back_to_the_defaults() -> None:
    settings = SETTINGS.TripSettings.from_values(
        {"trip_detection": "teleport", "trip_commute_gap": "soon", "trip_default_class": "x"}
    )
    assert settings.detection == CONST.TRIP_DETECTION_LIVE
    assert settings.commute_gap_s == CONST.DEFAULT_TRIP_COMMUTE_GAP_MIN * 60
    assert settings.default_class is None  # "don't label it", as before


def test_a_multi_car_form_stores_a_complete_set_under_that_car() -> None:
    submitted = {
        "trip_default_class": "private",
        "trip_commute_gap": 0,
        "trip_detection": "reports",
        "trip_track": False,
    }

    stored = SETTINGS.store_trip_values(ACCOUNT, submitted, vin=B, per_vehicle=True)

    assert stored["vehicles"][B] == {**submitted, "trip_work_zone": None}
    # The other car's (account-wide) settings are untouched.
    assert {key: stored[key] for key in ACCOUNT} == ACCOUNT


def test_a_single_car_form_keeps_editing_the_account_wide_keys() -> None:
    """And drops a leftover entry of its own, which would outrank what was saved."""

    options = {**ACCOUNT, "vehicles": {A: {"trip_detection": "reports"}}}
    submitted = {
        "trip_work_zone": "zone.school",
        "trip_default_class": "private",
        "trip_commute_gap": 30,
        "trip_detection": "live",
        "trip_track": False,
    }

    stored = SETTINGS.store_trip_values(options, submitted, vin=A, per_vehicle=False)

    assert "vehicles" not in stored
    assert stored["trip_work_zone"] == "zone.school"
    assert stored["trip_geocode"] is True  # account-wide settings are carried over
    assert not SETTINGS.account_trip_settings(stored).report_trips


def test_clearing_the_work_zone_clears_it() -> None:
    """HA leaves a cleared entity selector out of the submitted data altogether."""

    stored = SETTINGS.store_trip_values(
        ACCOUNT, {"trip_detection": "live"}, vin=A, per_vehicle=False
    )
    assert SETTINGS.account_trip_settings(stored).work_zone_entity is None
