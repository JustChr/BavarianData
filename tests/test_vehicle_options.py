"""Settings a car holds for itself: which apply to a car, and how a screen stores them.

The rule a user relies on is "what the screen shows is what applies": a car with
a group of settings of its own uses all of them, every other car uses the
account-wide ones, nothing is inherited key by key, and one screen's settings
never disturb another's.
"""

from __future__ import annotations

from .conftest import load_module

OPTIONS = load_module("vehicle_options")
TRIPS = load_module("trip_settings")
PRICING = load_module("history.pricing")
EVCC = load_module("evcc")
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
    "price_mode": "fixed",
    "price_fixed": 0.32,
    "grid_energy_entity": "sensor.wallbox_garage_total",
    "charging_loss_percent": 8,
    "bridge_enabled": True,
}

TRIP_FORM = {
    "trip_default_class": "private",
    "trip_commute_gap": 0,
    "trip_detection": "reports",
    "trip_track": False,
}
# The entity pickers left empty: stored as "none", never left unchanged.
CLEARED = {"trip_work_zone": None, "trip_phone_tracker": None, "trip_phone_in_car": None}


# --- the shared rules ---------------------------------------------------------


def test_a_car_without_its_own_group_uses_the_account_wide_values() -> None:
    options = {**ACCOUNT, "vehicles": {B: {"trip_detection": "reports"}}}

    assert OPTIONS.values_for(options, A, OPTIONS.TRIP_OPTIONS)["trip_default_class"] == "business"
    # B has trips of its own, but no charging settings: those are the account's.
    assert OPTIONS.values_for(options, B, OPTIONS.CHARGING_OPTIONS) == {
        "grid_energy_entity": "sensor.wallbox_garage_total",
        "charging_loss_percent": 8,
    }


def test_a_car_with_its_own_group_inherits_nothing_of_it() -> None:
    options = {**ACCOUNT, "vehicles": {B: {"trip_detection": "reports"}}}

    own = TRIPS.vehicle_trip_settings(options)[B]
    assert own.report_trips
    assert own.work_zone_entity is None  # not the account's office
    assert own.default_class == CONST.DEFAULT_TRIP_DEFAULT_CLASS
    assert not own.record_track
    assert OPTIONS.values_for(options, B, OPTIONS.TRIP_OPTIONS) == {"trip_detection": "reports"}


def test_a_multi_car_screen_stores_its_group_whole_under_that_car() -> None:
    stored = OPTIONS.store_values(ACCOUNT, TRIP_FORM, OPTIONS.TRIP_OPTIONS, vin=B, per_vehicle=True)

    assert stored["vehicles"][B] == {**TRIP_FORM, **CLEARED}
    assert {key: stored[key] for key in ACCOUNT} == ACCOUNT


def test_one_screen_never_disturbs_another_screens_settings() -> None:
    """Saving a car's charging settings keeps its trip settings, and back."""

    stored = OPTIONS.store_values(ACCOUNT, TRIP_FORM, OPTIONS.TRIP_OPTIONS, vin=B, per_vehicle=True)
    stored = OPTIONS.store_values(
        stored,
        {"grid_energy_entity": "sensor.wallbox_drive_total", "charging_loss_percent": 5},
        OPTIONS.CHARGING_OPTIONS,
        vin=B,
        per_vehicle=True,
    )

    assert OPTIONS.own_values(stored, B, OPTIONS.TRIP_OPTIONS) == {**TRIP_FORM, **CLEARED}
    assert OPTIONS.own_values(stored, B, OPTIONS.CHARGING_OPTIONS) == {
        "grid_energy_entity": "sensor.wallbox_drive_total",
        "charging_loss_percent": 5,
    }


def test_a_single_car_screen_keeps_editing_the_account_wide_keys() -> None:
    """And drops a leftover group of its own, which would outrank what was saved."""

    options = {
        **ACCOUNT,
        "vehicles": {A: {"trip_detection": "reports", "charging_loss_percent": 3}},
    }

    stored = OPTIONS.store_values(
        options,
        {"trip_work_zone": "zone.school", **TRIP_FORM},
        OPTIONS.TRIP_OPTIONS,
        vin=A,
        per_vehicle=False,
    )

    assert stored["trip_work_zone"] == "zone.school"
    assert stored["trip_geocode"] is True  # other account-wide settings carried over
    # The trip group is gone from the car; its charging group is untouched.
    assert stored["vehicles"] == {A: {"charging_loss_percent": 3}}


def test_the_last_group_leaving_a_car_removes_the_car_entry() -> None:
    options = {**ACCOUNT, "vehicles": {A: {"trip_detection": "reports"}}}

    stored = OPTIONS.store_values(
        options, TRIP_FORM, OPTIONS.TRIP_OPTIONS, vin=A, per_vehicle=False
    )

    assert "vehicles" not in stored


def test_clearing_an_entity_selector_clears_it() -> None:
    """HA leaves a cleared entity selector out of the submitted data altogether."""

    stored = OPTIONS.store_values(
        ACCOUNT, {"charging_loss_percent": 0}, OPTIONS.CHARGING_OPTIONS, vin=B, per_vehicle=True
    )
    assert stored["vehicles"][B]["grid_energy_entity"] is None
    assert PRICING.vehicle_pricing(stored)[B].grid_energy_entity is None
    assert (
        PRICING.PricingConfig.from_options(stored).grid_energy_entity
        == "sensor.wallbox_garage_total"
    )


# --- each group's settings ----------------------------------------------------------


def test_unrecognised_trip_values_fall_back_to_the_defaults() -> None:
    settings = TRIPS.TripSettings.from_values(
        {"trip_detection": "teleport", "trip_commute_gap": "soon", "trip_default_class": "x"}
    )
    assert settings.detection == CONST.TRIP_DETECTION_LIVE
    assert settings.commute_gap_s == CONST.DEFAULT_TRIP_COMMUTE_GAP_MIN * 60
    assert settings.default_class is None  # "don't label it", as before


def test_a_cars_own_meter_and_losses_keep_the_houses_tariff() -> None:
    options = {
        **ACCOUNT,
        "vehicles": {
            B: {"grid_energy_entity": "sensor.wallbox_drive_total", "charging_loss_percent": 5}
        },
    }

    own = PRICING.vehicle_pricing(options)[B]
    assert (own.grid_energy_entity, own.loss_percent) == ("sensor.wallbox_drive_total", 5.0)
    assert (own.mode, own.fixed_price) == ("fixed", 0.32)
    assert A not in PRICING.vehicle_pricing(options)


def test_a_car_switched_off_the_bridge_is_not_published() -> None:
    options = {**ACCOUNT, "vehicles": {B: {"bridge_enabled": False}}}

    config = EVCC.BridgeConfig.from_options(options)
    assert config.publishes(A)
    assert not config.publishes(B)
    assert config.any_enabled


def test_the_bridge_is_on_while_any_one_car_is_published() -> None:
    options = {**ACCOUNT, "bridge_enabled": False, "vehicles": {B: {"bridge_enabled": True}}}

    config = EVCC.BridgeConfig.from_options(options)
    assert config.any_enabled
    assert config.publishes(B)
    assert not config.publishes(A)
    assert not EVCC.BridgeConfig.from_options({"bridge_enabled": False}).any_enabled


# --- the mileage allowance: always the car's own -----------------------------

LEASE_VALUES = {
    "mileage_kind": "lease",
    "mileage_start": "2025-09-01",
    "mileage_end": "2028-08-31",
    "mileage_allowance_km": 15000,
    "mileage_start_km": 12,
}


def test_a_single_car_account_stores_its_lease_under_the_car():
    stored = OPTIONS.store_mileage({"trip_work_zone": "zone.work"}, "VIN1", LEASE_VALUES)
    assert stored["vehicles"]["VIN1"] == LEASE_VALUES
    assert "mileage_kind" not in stored
    # A second car starts without one, rather than inheriting the first car's.
    assert OPTIONS.mileage_values(stored, "VIN2") == {}
    assert OPTIONS.mileage_values(stored, "VIN1") == LEASE_VALUES


def test_switching_kind_drops_the_keys_the_new_kind_does_not_use():
    stored = OPTIONS.store_mileage({}, "VIN1", LEASE_VALUES)
    yearly = {**LEASE_VALUES, "mileage_kind": "yearly"}
    del yearly["mileage_end"]
    stored = OPTIONS.store_mileage(stored, "VIN1", yearly)
    assert stored["vehicles"]["VIN1"]["mileage_end"] is None


def test_none_removes_the_allowance_and_keeps_the_cars_other_settings():
    options = {"vehicles": {"VIN1": {"bridge_enabled": True}}}
    stored = OPTIONS.store_mileage(options, "VIN1", LEASE_VALUES)
    stored = OPTIONS.store_mileage(stored, "VIN1", {"mileage_kind": "none"})
    assert stored == options
    assert (
        OPTIONS.store_mileage(
            OPTIONS.store_mileage({}, "VIN1", LEASE_VALUES),
            "VIN1",
            {"mileage_kind": "none"},
        )
        == {}
    )
