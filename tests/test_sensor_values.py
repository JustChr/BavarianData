"""What the derived sensors show, without Home Assistant.

The attribute names are read by the dashboard card and by users' automations,
so each set is pinned here by name.
"""

from __future__ import annotations

from types import SimpleNamespace

from .conftest import load_module

values = load_module("sensor_values")
health_module = load_module("history.health")


# -- enums --------------------------------------------------------------------


def test_an_enum_reading_becomes_its_lowercase_option() -> None:
    assert values.enum_state("CHARGING", ["charging", "idle"]) == ("charging", ["charging", "idle"])


def test_an_undocumented_enum_value_extends_the_options() -> None:
    options = ["idle"]
    value, extended = values.enum_state("NEW_STATE", options)
    assert (value, extended) == ("new_state", ["idle", "new_state"])
    assert options == ["idle"]


def test_a_non_string_enum_reading_is_left_alone() -> None:
    assert values.enum_state(3, ["idle"]) == (3, ["idle"])


# -- the account --------------------------------------------------------------


def _quota(next_reset="2026-10-25T00:00:00+00:00"):
    return SimpleNamespace(used=12, remaining=38, next_reset_iso=next_reset)


def test_quota_attributes_name_the_account_sensors_contract() -> None:
    assert values.quota_attributes(None) == {}
    assert values.quota_attributes(_quota()) == {
        "api_quota_used": 12,
        "api_quota_remaining": 38,
        "api_quota_next_reset": "2026-10-25T00:00:00+00:00",
    }
    assert values.quota_attributes(_quota(None), prefix="") == {"used": 12, "remaining": 38}


def test_connection_attributes_carry_the_stream_quota_and_units() -> None:
    attrs = values.connection_attributes(
        {"connected": True}, "broker closed", _quota(None), {"distance": "mi"}
    )
    assert attrs == {
        "connected": True,
        "last_disconnect_reason": "broker closed",
        "api_quota_used": 12,
        "api_quota_remaining": 38,
        "display_units": {"distance": "mi"},
        "login_refused": False,
    }
    assert "last_disconnect_reason" not in values.connection_attributes({}, None, None, {})
    assert values.connection_attributes({}, None, None, {}, True)["login_refused"] is True


# -- charging -----------------------------------------------------------------


def test_cost_month_attributes_default_to_no_sessions() -> None:
    assert values.cost_month_attributes({}) == {
        "sessions": 0,
        "energy_kwh": None,
        "partial": False,
    }


def _session(**overrides):
    data = {
        "energy_kwh": 30.0,
        "duration_s": 7200,
        "soc_start": 20,
        "soc_end": 80,
        "peak_power_kw": 11.0,
        "location": {"zone": "home", "address": "Somewhere 1"},
        "location_assumed": False,
        "cost": {"amount": 9.0, "source": "tariff", "partial": 0},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_the_latest_session_cost_and_its_attributes() -> None:
    session = _session()
    assert values.session_cost(session) == 9.0
    assert values.session_cost_attributes(session) == {
        "energy_kwh": 30.0,
        "duration_s": 7200,
        "soc_start": 20,
        "soc_end": 80,
        "peak_power_kw": 11.0,
        "zone": "home",
        "address": "Somewhere 1",
        "location_assumed": False,
        "cost_source": "tariff",
        "partial": False,
    }


def test_a_session_without_cost_or_place_says_less() -> None:
    session = _session(cost=None, location={"zone": None})
    assert values.session_cost(session) is None
    attrs = values.session_cost_attributes(session)
    assert attrs["zone"] is None
    assert "address" not in attrs and "cost_source" not in attrs
    assert "zone" not in values.session_cost_attributes(_session(location=None))
    assert values.session_cost(None) is None
    assert values.session_cost_attributes(None) == {}


def test_the_energy_mix_is_absent_until_something_was_attributed() -> None:
    assert values.energy_month_attributes({}) == {}
    assert values.energy_month_attributes({"energy_mix": {"solar_kwh": 0.0}}) == {
        "energy_mix": {"solar_kwh": 0.0}
    }
    mix = {"solar_percent": 40.0}
    assert values.energy_month_attributes({"energy_mix": mix}) == {
        "energy_mix": mix,
        "solar_percent": 40.0,
    }


# -- battery health and range -------------------------------------------------


def _health(**overrides):
    data = {"usable_kwh": 79.5, "samples": 12, "confident": True}
    data.update(overrides)
    return health_module.BatteryHealth(**data)


def test_battery_health_shows_learning_until_confident() -> None:
    needed = health_module.MIN_SAMPLES
    assert values.battery_health_state(_health()) == 79.5
    assert values.battery_health_state(_health(confident=False, samples=3)) == (
        f"Learning (3/{needed})"
    )
    assert values.battery_health_state(_health(confident=False, samples=99)) == (
        f"Learning ({needed}/{needed})"
    )


def test_battery_health_attributes_and_the_trend() -> None:
    attrs = values.battery_health_attributes(_health(), None)
    assert attrs["samples_needed"] == health_module.MIN_SAMPLES
    assert attrs["usable_capacity_kwh"] == 79.5
    assert "trend" not in attrs
    assert values.battery_health_attributes(_health(), [1])["trend"] == [1]
    assert set(attrs) == {
        "samples",
        "samples_needed",
        "confident",
        "usable_capacity_kwh",
        "nominal_capacity_kwh",
        "vs_new_percent",
        "suspicious",
    }


def test_real_range_reads_the_profile() -> None:
    profile = {
        "status": "ok",
        "range": {"now_km": 310, "full_km": 420, "bmw_km": 330},
        "consumption": {"kwh_per_100km": 18.1, "source": "battery"},
        "grid_consumption": {"kwh_per_100km": 20.0},
        "capacity_kwh": 81.2,
    }
    assert values.real_range_state(profile) == 310
    attrs = values.real_range_attributes(profile)
    assert attrs["range_now_km"] == 310
    assert attrs["bmw_range_km"] == 330
    assert attrs["consumption_source"] == "battery"
    assert attrs["grid_consumption_kwh_per_100km"] == 20.0
    assert values.real_range_state({}) is None
    empty = values.real_range_attributes({})
    assert len(empty) == 15 and set(empty.values()) == {None}


# -- driving and mileage ------------------------------------------------------


def test_driving_attributes_split_the_month() -> None:
    assert values.driving_month_attributes({"trip_count": 4, "split": {"private_km": 30}}) == {
        "trip_count": 4,
        "business_km": None,
        "private_km": 30,
        "commute_km": None,
        "unclassified_km": None,
    }
    assert values.driving_month_attributes({})["trip_count"] == 0


def test_mileage_attributes_carry_every_allowance_field() -> None:
    attrs = values.mileage_attributes({"kind": "lease", "used_km": 1200, "other": 1})
    assert list(attrs) == list(values.MILEAGE_ATTRIBUTES)
    assert attrs["kind"] == "lease" and attrs["used_km"] == 1200


# -- tyres --------------------------------------------------------------------


def test_every_tyre_sensor_declares_the_tire_cluster_and_its_fetch_time() -> None:
    attrs = values.tyre_attributes(
        {"descriptor": "tyre_status"}, {"fetched_at": "2026-10-24"}, "Tire data"
    )
    assert attrs == {
        "descriptor": "tyre_status",
        "cluster": "tire",
        "cluster_name": "Tire data",
        "fetched_at": "2026-10-24",
        "timestamp": "2026-10-24",
    }
    kept = values.tyre_attributes({"timestamp": "earlier"}, {"fetched_at": "2026-10-24"}, "x")
    assert kept["timestamp"] == "earlier"
    assert "fetched_at" not in values.tyre_attributes({}, {}, "x")


def test_a_wheel_lands_on_the_streamed_pressures_slot() -> None:
    diagnosis = {"wheels": {"rear_right": {"wear_status": "OK", "tread_mm": 6}}}
    wheel = values.wheel(diagnosis, "rear_right")
    assert values.wheel_state(wheel) == "OK"
    assert values.wheel_state({"wear_status_color": "green", "wear_status": "OK"}) == "green"
    assert values.wheel_attributes("rear_right", wheel) == {
        "tire_axle": "row2",
        "tire_side": "right",
        "tire_metric": "diagnosis",
        "wear_status": "OK",
        "tread_mm": 6,
    }
    assert values.wheel({}, "front_left") == {}


def test_the_tyre_status_surfaces_upstream_errors() -> None:
    assert values.tyre_status_attributes({"wheels": {"rear_left": {}, "front_left": {}}}) == {
        "label": None,
        "wheels_reported": ["front_left", "rear_left"],
    }
    assert values.tyre_status_attributes({"errors": ["outage"]})["errors"] == ["outage"]
