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
restore_diagnosis = tyre.restore_diagnosis


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


def test_restore_round_trips_a_stored_diagnosis():
    # What tyre_store writes must come back in the shape the entities read, or a
    # restart shows "unknown" until the next daily fetch -- the bug this store
    # exists to fix.
    parsed = parse_tyre_diagnosis(_payload(frontLeft=_wheel(), rearRight=_wheel()))
    restored = restore_diagnosis({"fetched_at": "2026-07-27T06:00:00+00:00", "diagnosis": parsed})
    assert set(restored["wheels"]) == {"front_left", "rear_right"}
    assert restored["wheels"]["front_left"]["due_mileage_km"] == 30000.0
    assert restored["aggregated_status"] == "GOOD"
    assert restored["fetched_at"] == "2026-07-27T06:00:00+00:00"


def test_restore_rejects_records_with_nothing_in_them():
    for record in (None, {}, [], "nope", {"diagnosis": None}, {"diagnosis": {}}, {"fetched_at": "x"}):
        assert restore_diagnosis(record) is None


def test_restore_repairs_a_corrupt_store_instead_of_handing_on_junk():
    restored = restore_diagnosis(
        {
            "fetched_at": 1234,  # not a string: dropped rather than displayed
            "diagnosis": {
                "aggregated_status": "GOOD",
                "wheels": {"front_left": "not a dict", "rear_left": {"wear_status": "OK"}},
                "errors": "boom",
            },
        }
    )
    # The entities index into both containers, so both must survive as their
    # expected types no matter what was on disk.
    assert set(restored["wheels"]) == {"rear_left"}
    assert restored["errors"] == []
    assert "fetched_at" not in restored


def test_restore_tolerates_wheels_and_errors_being_absent():
    restored = restore_diagnosis({"diagnosis": {"aggregated_status": "GOOD"}})
    assert restored["wheels"] == {}
    assert restored["errors"] == []


def test_wheel_slugs_match_the_streamed_tire_attributes():
    # The card places diagnosis and streamed pressure on one diagram by matching
    # "<axle>_<side>", so these slugs must stay in that shape.
    for _bmw_key, slug in WHEEL_POSITIONS:
        axle, side = slug.split("_", 1)
        assert axle in {"front", "rear"}
        assert side in {"left", "right"}
