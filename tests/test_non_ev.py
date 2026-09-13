"""Petrol, diesel, plug-in hybrid and motorcycle handling outside the card.

BavarianData grew up on one electric i5, so every derived sensor, the trip
consumption figure and the setup assumed a high-voltage battery. The only real
combustion data this project has is the F87 M2's diagnostics from issue #8
(fuel in ``l``, the EV charge *target* streamed anyway, no battery signal); the
fixtures below use its descriptors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from .conftest import load_module

coverage = load_module("coverage")
repair = load_module("registry_repair")
trips = load_module("history.trips")
summary = load_module("history.summary")
trip_builder = load_module("history.trip_builder")
units = load_module("units")
support = load_module("vehicle_support")

HV_SOC = "vehicle.drivetrain.batteryManagement.header"
FUEL = "vehicle.drivetrain.fuelSystem.remainingFuel"
FUEL_LEVEL = "vehicle.drivetrain.fuelSystem.level"
EV_TARGET = "vehicle.powertrain.electric.battery.stateOfCharge.target"
DOOR = "vehicle.cabin.door.status"

M2_SEEN = {FUEL, EV_TARGET, DOOR}
I5_SEEN = {HV_SOC, EV_TARGET, DOOR}
PHEV_SEEN = {HV_SOC, FUEL_LEVEL, DOOR}

START = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
PETROL_VIN = "WBSPETROL00000001"
EV_VIN = "WBYELECTRIC000001"


# --- drivetrain evidence ---------------------------------------------------


def test_drivetrain_needs_positive_evidence():
    assert coverage.is_combustion_only(M2_SEEN)
    assert not coverage.is_plug_in_hybrid(M2_SEEN)

    assert not coverage.is_combustion_only(I5_SEEN)
    assert not coverage.is_plug_in_hybrid(I5_SEEN)

    assert coverage.is_plug_in_hybrid(PHEV_SEEN)
    assert not coverage.is_combustion_only(PHEV_SEEN)

    # Nothing seen yet is nothing -- neither a petrol car nor a hybrid.
    assert not coverage.is_combustion_only({DOOR})
    assert not coverage.is_plug_in_hybrid(set())


def test_the_m2s_ev_charge_target_is_not_a_battery():
    assert not coverage.has_high_voltage({EV_TARGET})


# --- registry: battery-only entities on a petrol car ----------------------


def _row(entity_id: str, unique_id: str) -> SimpleNamespace:
    return SimpleNamespace(entity_id=entity_id, unique_id=unique_id, disabled_by=None)


def test_petrol_car_loses_exactly_its_battery_only_entities():
    rows = [
        _row("sensor.m2_soc_estimate", f"{PETROL_VIN}_soc_estimate"),
        _row("sensor.m2_soc_rate", f"{PETROL_VIN}_soc_rate"),
        _row("sensor.m2_charged", f"{PETROL_VIN}_charged_energy_total"),
        _row("sensor.m2_month", f"{PETROL_VIN}_charging_energy_month"),
        # Kept: a real descriptor, the monthly distance, the diagnostics.
        _row("sensor.m2_fuel", f"{PETROL_VIN}_{FUEL}"),
        _row("sensor.m2_distance", f"{PETROL_VIN}_driving_distance_month"),
        _row("sensor.quota", "01ENTRY_diagnostics_api_quota_remaining"),
        # Another car on the same account, which does have a battery.
        _row("sensor.i5_soc_estimate", f"{EV_VIN}_soc_estimate"),
    ]
    removed = repair.ev_entities_to_remove(rows, lambda vin: vin == PETROL_VIN)
    assert removed == [
        "sensor.m2_soc_estimate",
        "sensor.m2_soc_rate",
        "sensor.m2_charged",
        "sensor.m2_month",
    ]


def test_nothing_is_removed_without_proof_of_a_petrol_car():
    rows = [_row("sensor.soc_estimate", f"{EV_VIN}_soc_estimate")]
    assert repair.ev_entities_to_remove(rows, lambda vin: False) == []


def test_every_battery_only_suffix_is_a_real_unique_id_suffix():
    # sensor.py passes each suffix as the descriptor its entity.py unique id is
    # built from (f"{vin}_{descriptor}"); a typo here would leave the stale
    # entity in place without anything failing.
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components"
        / "bavariandata"
        / "sensor.py"
    ).read_text(encoding="utf-8")
    for suffix in repair.EV_ONLY_SUFFIXES:
        assert f'"{suffix}"' in source, suffix


# --- plug-in hybrid trips --------------------------------------------------


def _trip(**overrides):
    data = {
        "vin": EV_VIN,
        "start": START,
        "end": START + timedelta(minutes=40),
        "distance_km": 40.0,
        "soc_start": 60.0,
        "soc_end": 50.0,
        "energy_kwh": 1.8,
    }
    data.update(overrides)
    return trips.Trip(**data)


def test_hybrid_trip_keeps_its_energy_but_withholds_the_ratio():
    electric = _trip()
    hybrid = _trip(hybrid=True)
    assert electric.consumption_kwh_per_100km == 4.5
    assert hybrid.consumption_kwh_per_100km is None
    assert hybrid.energy_kwh == 1.8
    assert hybrid.to_dict()["consumption_kwh_per_100km"] is None


def test_hybrid_flag_survives_the_store():
    restored = trips.Trip.from_dict(_trip(hybrid=True).to_dict())
    assert restored.hybrid is True
    assert restored.consumption_kwh_per_100km is None
    # Records written before the flag existed read as not hybrid.
    legacy = _trip().to_dict()
    legacy.pop("hybrid")
    assert trips.Trip.from_dict(legacy).hybrid is False


def test_hybrid_trips_stay_out_of_the_average():
    electric = _trip(distance_km=100.0, energy_kwh=18.0, soc_start=80.0, soc_end=57.0)
    hybrid = _trip(distance_km=100.0, energy_kwh=2.0, soc_start=40.0, soc_end=37.0, hybrid=True)
    assert summary.fleet_consumption_kwh_per_100km([electric, hybrid]) == 18.0


def test_builder_carries_the_flag_onto_the_record():
    builder = trip_builder.TripBuilder(EV_VIN, START, soc_start=60.0, mileage_start=1000.0)
    trip = builder.close(
        START + timedelta(minutes=40),
        soc_end=50.0,
        mileage_end=1040.0,
        energy_kwh=1.8,
        hybrid=True,
    )
    assert trip.hybrid is True
    assert trip.consumption_kwh_per_100km is None


# --- fuel volume units -----------------------------------------------------


def test_gallons_are_a_known_unit():
    assert units.normalize_unit("gal") == "gal"
    assert units.normalize_unit("gallons") == "gal"
    assert units.normalize_unit("GALLON") == "gal"
    assert units.normalize_unit("l") == "L"
    assert units.is_known("gallons")


# --- motorcycles -----------------------------------------------------------


def test_motorrad_is_recognised_whatever_the_spelling():
    for brand in ("BMW Motorrad", "BMW_MOTORRAD", "MOTORRAD", "bmw motorrad"):
        assert support.is_motorcycle(brand), brand
    for brand in ("BMW", "MINI", "BMW_I", "", None, 42):
        assert not support.is_motorcycle(brand), brand


def test_motorcycle_issue_id_carries_no_vin():
    issue_id = support.motorcycle_issue_id("01ENTRY", PETROL_VIN)
    assert PETROL_VIN not in issue_id
    assert issue_id.startswith("motorcycle_unsupported_01ENTRY_")
    assert issue_id == support.motorcycle_issue_id("01ENTRY", PETROL_VIN)
    assert issue_id != support.motorcycle_issue_id("01ENTRY", EV_VIN)
