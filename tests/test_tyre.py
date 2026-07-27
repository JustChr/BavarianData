"""Tests for the smart-maintenance tyre-diagnosis parser.

The payload is deeply nested and every branch is optional, so these pin the two
things that actually bite: unit handling on ``dueMileage`` (a mile figure read as
km understates remaining tread life by 60%) and never inventing a wheel BMW did
not report.
"""

from __future__ import annotations

import pytest

from .conftest import load_module

tyre = load_module("tyre")

WHEEL_POSITIONS = tyre.WHEEL_POSITIONS
parse_tyre_diagnosis = tyre.parse_tyre_diagnosis
parse_wheel = tyre.parse_wheel


def _wheel(**overrides):
    data = {
        "tyreWear": {
            "label": "Reifenverschleiß",
            "value": "5.8 mm",
            "status": "OK",
            "statusColor": "GREEN",
            "dueMileage": 30000,
            "unit": "KILOMETER",
        },
        "tyreDefect": {"status": "NO_DEFECT", "statusColor": "GREEN"},
        "qualityStatus": {"qualityStatus": "GOOD"},
        "season": {"season": "SUMMER"},
        "tread": {"treadDesign": "P Zero", "manufacturer": "Pirelli"},
        "dimension": {"value": "245/45 R19"},
        "mountingDate": {"mountingDate": "2024-03-11"},
        "tyreProductionDate": {"value": "2023-W42", "statusColor": "GREEN"},
        "partNumber": {"partNumber": "36112459594"},
        "runFlat": {"runFlat": True},
    }
    data.update(overrides)
    return data


def _payload(**wheels):
    mounted = {"aggregatedQualityStatus": {"qualityStatus": "GOOD", "label": "Zustand"}}
    mounted.update(wheels)
    return {"passengerCar": {"mountedTyres": mounted}}


def test_parse_wheel_flattens_every_branch():
    parsed = parse_wheel(_wheel())
    assert parsed["wear_status"] == "OK"
    assert parsed["wear_status_color"] == "green"  # lower-cased for the state
    assert parsed["wear_value"] == "5.8 mm"
    assert parsed["due_mileage_km"] == 30000.0
    assert parsed["defect_status"] == "NO_DEFECT"
    assert parsed["season"] == "SUMMER"
    assert parsed["tread"] == "P Zero"
    assert parsed["tread_manufacturer"] == "Pirelli"
    assert parsed["dimension"] == "245/45 R19"
    assert parsed["mounting_date"] == "2024-03-11"
    assert parsed["production_date"] == "2023-W42"
    assert parsed["part_number"] == "36112459594"
    assert parsed["run_flat"] is True


def test_due_mileage_converts_miles_to_km():
    wheel = _wheel(
        tyreWear={"dueMileage": 10000, "unit": "MILE", "statusColor": "YELLOW"}
    )
    assert parse_wheel(wheel)["due_mileage_km"] == pytest.approx(16093.4, abs=0.1)


def test_due_mileage_defaults_to_km_and_tolerates_junk():
    assert parse_wheel(_wheel(tyreWear={"dueMileage": 500}))["due_mileage_km"] == 500.0
    # A bool is an int in Python; it must not become a mileage.
    assert parse_wheel(_wheel(tyreWear={"dueMileage": True}))["due_mileage_km"] is None
    assert parse_wheel(_wheel(tyreWear={"dueMileage": "lots"}))["due_mileage_km"] is None


def test_parse_reports_only_wheels_bmw_sent():
    result = parse_tyre_diagnosis(_payload(frontLeft=_wheel(), rearRight=_wheel()))
    assert set(result["wheels"]) == {"front_left", "rear_right"}
    assert result["aggregated_status"] == "GOOD"
    assert result["aggregated_label"] == "Zustand"


def test_empty_wheel_is_dropped_rather_than_shown_as_unknown():
    # An all-empty branch would otherwise create an entity that can only ever
    # read "unknown", which looks like a bug to the user.
    result = parse_tyre_diagnosis(_payload(frontLeft={"tyreWear": {}}, rearLeft=_wheel()))
    assert set(result["wheels"]) == {"rear_left"}


def test_missing_and_malformed_payloads_never_raise():
    for payload in (None, {}, [], "nope", {"passengerCar": None}, {"passengerCar": {}}):
        result = parse_tyre_diagnosis(payload)
        assert result["wheels"] == {}
        assert result["aggregated_status"] is None
        assert result["errors"] == []


def test_errors_branch_is_surfaced():
    result = parse_tyre_diagnosis(
        {"errors": [{"type": "UPSTREAM", "message": {"value": "service down"}}]}
    )
    assert result["errors"] == ["service down"]
    assert result["wheels"] == {}


def test_wheel_slugs_match_the_streamed_tire_attributes():
    # The card places diagnosis and streamed pressure on one diagram by matching
    # "<axle>_<side>", so these slugs must stay in that shape.
    for _bmw_key, slug in WHEEL_POSITIONS:
        axle, side = slug.split("_", 1)
        assert axle in {"front", "rear"}
        assert side in {"left", "right"}
