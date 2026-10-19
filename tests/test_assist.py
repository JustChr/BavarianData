"""The Assist tools' answers: what they say, and what they never say (``assist.py``)."""

from __future__ import annotations

import json
import pathlib

import yaml

from .conftest import load_module

assist = load_module("assist")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_VIN = "WBA00000000000001"


def test_scrub_drops_identifiers_and_positions_at_any_depth():
    raw = {
        "vin": _VIN,
        "summary": {
            "best_trip": {"id": f"{_VIN}-123", "label": "Work", "distance_km": 34.0},
            "top_destinations": [{"label": "Home", "count": 3, "latitude": 48.1}],
        },
        "sessions": [{"power_curve": [1, 2, 3], "location": {"zone": "Home", "lon": 11.5}}],
    }
    clean = assist.scrub(raw)
    text = json.dumps(clean)
    assert _VIN not in text
    for key in ("vin", '"id"', "latitude", "power_curve", '"lon"'):
        assert key not in text
    assert clean["summary"]["best_trip"] == {"label": "Work", "distance_km": 34.0}
    assert clean["sessions"][0]["location"] == {"zone": "Home"}


def test_scrub_masks_a_vin_inside_any_string():
    assert assist.scrub({"note": f"trip {_VIN} done"}) == {"note": "trip (hidden) done"}


def test_in_unit_renames_and_converts_distance_fields_only():
    value = {
        "range_km": 160.9344,
        "km_per_day_left": 16.09344,
        "kwh_per_100km": 10.0,
        "cost_per_100km": 1.0,
        "this_month": {"km": 1609.344, "partial": True},
        "battery_percent": 80,
        "round_trip": True,
    }
    miles = assist.in_unit(value, "mi")
    assert miles == {
        "range_mi": 100.0,
        "mi_per_day_left": 10.0,
        "kwh_per_100mi": 16.1,
        "cost_per_100mi": 1.6,
        "this_month": {"mi": 1000.0, "partial": True},
        "battery_percent": 80,
        "round_trip": True,
    }
    assert assist.in_unit(value, "km") is value


def test_open_items_names_what_is_open_in_plain_words():
    readings = {
        "vehicle.cabin.door.status": "SELECTIVE-LOCKED",
        "vehicle.cabin.door.row1.driver.isOpen": True,
        "vehicle.cabin.door.row2.passenger.isOpen": False,
        "vehicle.cabin.window.row1.passenger.status": "INTERMEDIATE",
        "vehicle.cabin.window.row2.driver.status": "CLOSED",
        "vehicle.cabin.sunroof.overallStatus": "OPEN_TILT",
        "vehicle.body.trunk.window.isOpen": "true",
        "vehicle.body.hood.isOpen": False,
        "vehicle.body.trunk.isLocked": False,
    }
    assert assist.open_items(readings) == [
        "doors partly locked",
        "tailgate unlocked",
        "tailgate window open",
        "front driver door open",
        "sunroof tilted or partly open",
        "front passenger window partly open",
    ]


def test_open_items_leaves_out_what_is_open_on_every_charging_night():
    readings = {
        "vehicle.body.flap.isLocked": False,
        "vehicle.body.chargingPort.lockedStatus": "CHARGING_CABLE_NOT_LOCKED",
        "vehicle.powertrain.tractionBattery.charging.port.anyPosition.flap.isOpen": True,
        "vehicle.cabin.door.status": "SECURED",
        "vehicle.cabin.window.row1.driver.status": "unknown",
    }
    assert assist.open_items(readings) == []


def test_open_items_follows_the_blueprint_rule():
    """The tools and the "Car left open at night" blueprint check the same set."""

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_constructor("!input", lambda loader, node: node.value)
    path = _ROOT / "blueprints" / "automation" / "bavariandata" / "car_left_open.yaml"
    blueprint = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    assert blueprint["variables"]["checked"].strip() == assist.CHECKED.pattern


def test_status_answer_drops_unknowns_and_never_gives_a_position():
    answer = assist.status_answer(
        name="i5",
        soc=61.4,
        charging=False,
        plugged=None,
        target_soc=80,
        charge_power_kw=0.0,
        minutes_to_target=0,
        range_km=268.6,
        bmw_range_km=None,
        odometer_km=12345.6,
        zone=None,
        has_position=True,
        trip_in_progress=False,
        open_now=[],
        soc_updated=None,
    )
    assert answer == {
        "car": "i5",
        "battery_percent": 61,
        "charging": False,
        "charge_target_percent": 80,
        "range_km": 269,
        "odometer_km": 12346,
        "location": "not in any zone",
        "driving": False,
        "open_or_unlocked": [],
    }


def test_status_answer_reports_charging_details_only_while_charging():
    answer = assist.status_answer(
        name="i5",
        soc=40,
        charging=True,
        plugged=True,
        target_soc=80,
        charge_power_kw=10.96,
        minutes_to_target=95,
        range_km=None,
        bmw_range_km=None,
        odometer_km=None,
        zone="Home",
        has_position=True,
        trip_in_progress=False,
        open_now=["front driver window open"],
        soc_updated="2026-10-19T21:00:00+02:00",
    )
    assert answer["charging_power_kw"] == 11.0
    assert answer["minutes_to_charge_target"] == 95
    assert answer["location"] == "Home"
    assert answer["open_or_unlocked"] == ["front driver window open"]


def test_range_check_verdicts():
    fits = assist.range_check(100, round_trip=False, range_km=250, full_range_km=400, soc=60)
    assert fits["verdict"] == "fits"
    assert fits["reserve_km"] == 40
    assert "charge_needed_percent" not in fits

    tight = assist.range_check(110, round_trip=True, range_km=250, full_range_km=400, soc=60)
    assert tight["verdict"] == "tight"
    assert tight["range_left_on_arrival_km"] == 30
    # 220 needed + 40 reserve - 250 left = 10 km short: 2.5 % of a full battery.
    assert tight["charge_needed_percent"] == 2

    short = assist.range_check(300, round_trip=True, range_km=100, full_range_km=400, soc=25)
    assert short["verdict"] == "does_not_fit"
    # More than a full battery's worth short: capped at what is left to fill.
    assert short["charge_needed_percent"] == 75


def test_range_check_without_a_range_says_so():
    answer = assist.range_check(50, round_trip=False, range_km=None, full_range_km=None, soc=None)
    assert answer["verdict"] == "unknown"


def test_charging_answer_totals_everything_and_lists_the_newest():
    sessions = [
        {
            "start": f"2026-10-{day:02d}T20:00:00+00:00",
            "energy_kwh": 10.0,
            "grid_kwh": 11.0,
            "cost": {"amount": 2.5, "currency": "EUR"},
            "location": {"zone": "Home"},
            "power_curve": [1, 2],
        }
        for day in range(1, 26)
    ]
    sessions.append({"start": "2026-10-26T09:00:00+00:00", "energy_kwh": 30.0, "cost": None})
    answer = assist.charging_answer(sessions, days=30)
    assert answer["session_count"] == 26
    assert answer["kwh_into_battery"] == 280.0
    assert answer["kwh_from_grid"] == 275.0
    assert answer["cost"] == {"EUR": 62.5}
    assert len(answer["sessions"]) == assist.MAX_SESSIONS
    assert answer["sessions_not_listed"] == 6
    assert answer["sessions"][0]["start"].startswith("2026-10-26")
    assert "power_curve" not in json.dumps(answer)
