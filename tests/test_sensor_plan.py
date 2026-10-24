"""Which sensors each car gets, without Home Assistant.

``sensor.py`` builds what ``SensorPlan`` names; these pin the rules: battery
sensors only for a car with a battery, cost sensors only with a tariff, no real
range on a plug-in hybrid, a registry entry restored as itself, tyre sensors
following BMW's diagnosis.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .conftest import load_module

plan_module = load_module("sensor_plan")
Want = plan_module.Want

EV = "WBAEXAMPLE0000001"
PETROL = "WBAEXAMPLE0000002"
ENTRY = "entry1"
BATTERY = "vehicle.drivetrain.batteryManagement.header"
ODOMETER = "vehicle.vehicle.travelledDistance"
MOVING = "vehicle.isMoving"
LATITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
HEADING = "vehicle.cabin.infotainment.navigation.currentLocation.heading"
SPEED = "vehicle.vehicle.speed"
DOORS = "vehicle.cabin.door.status"
SOC_TRACKING = list(plan_module.SOC_TRACKING)


class FakeCoordinator:
    def __init__(self) -> None:
        self.history: Any = object()
        self.pricing = SimpleNamespace(enabled=False)
        self.tyre_diagnosis: dict[str, Any] = {}
        self.states: dict[tuple[str, str], Any] = {}
        self.hybrids: set[str] = set()
        self.combustion: set[str] = set()

    def stream(self, vin: str, descriptor: str, value: Any = 1) -> None:
        self.states[(vin, descriptor)] = SimpleNamespace(value=value)

    def get_state(self, vin: str, descriptor: str) -> Any:
        return self.states.get((vin, descriptor))

    def is_plug_in_hybrid(self, vin: str) -> bool:
        return vin in self.hybrids

    def is_combustion_only(self, vin: str) -> bool:
        return vin in self.combustion


class Added:
    """``async_add_entities`` as the plan sees it: one entity per ``Want``."""

    def __init__(self) -> None:
        self.batches: list[tuple[list[Want], bool]] = []

    def __call__(self, wants: list[Want], update: bool) -> list[str]:
        self.batches.append((list(wants), update))
        return [f"entity:{want.kind}:{want.key}" for want in wants]

    def kinds(self, vin: str = EV) -> list[str]:
        return [want.kind for wants, _ in self.batches for want in wants if want.vin == vin]

    def keys(self, kind: str) -> list[str]:
        return [want.key for wants, _ in self.batches for want in wants if want.kind == kind]


def make(coordinator: FakeCoordinator | None = None):
    coordinator = coordinator or FakeCoordinator()
    added = Added()
    return plan_module.SensorPlan(coordinator, ENTRY, added), coordinator, added


# -- per car ------------------------------------------------------------------


def test_a_car_with_nothing_streamed_gets_only_its_location_sensors() -> None:
    plan, _, added = make()
    plan.car(EV)
    assert added.kinds() == [plan_module.LOCATION, plan_module.LOCATION]
    # Disabled in the registry, so not updated before they are added.
    assert added.batches == [(added.batches[0][0], False)]


def test_an_ev_with_an_odometer_gets_every_derived_sensor_once() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, BATTERY)
    coordinator.stream(EV, ODOMETER)
    plan.car(EV)
    plan.car(EV)
    assert added.kinds() == [
        *SOC_TRACKING,
        plan_module.LOCATION,
        plan_module.LOCATION,
        plan_module.VEHICLE_REPORT,
        plan_module.MILEAGE_ALLOWANCE,
        plan_module.CHARGING_ENERGY_MONTH,
        plan_module.BATTERY_HEALTH,
        plan_module.DRIVING_DISTANCE,
        plan_module.REAL_RANGE,
    ]


def test_a_petrol_car_gets_no_battery_sensors() -> None:
    plan, coordinator, added = make()
    coordinator.stream(PETROL, ODOMETER)
    plan.car(PETROL)
    kinds = added.kinds(PETROL)
    assert plan_module.DRIVING_DISTANCE in kinds
    assert plan_module.VEHICLE_REPORT in kinds
    for kind in (*SOC_TRACKING, plan_module.BATTERY_HEALTH, plan_module.REAL_RANGE):
        assert kind not in kinds
    assert plan_module.CHARGING_ENERGY_MONTH not in kinds


def test_moving_alone_makes_a_car_drivable() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, MOVING, False)
    plan.driving(EV)
    assert added.kinds() == [plan_module.DRIVING_DISTANCE]


def test_no_history_means_no_history_sensors_even_when_forced() -> None:
    coordinator = FakeCoordinator()
    coordinator.history = None
    plan, _, added = make(coordinator)
    for maker in (plan.driving, plan.battery_health, plan.real_range, plan.charging_summary):
        maker(EV, force=True)
    assert added.batches == []


def test_cost_sensors_need_a_tariff_and_distance_cost_an_odometer() -> None:
    plan, coordinator, added = make()
    coordinator.pricing.enabled = True
    coordinator.stream(EV, BATTERY)
    plan.charging_summary(EV)
    assert added.kinds() == [plan_module.CHARGING_ENERGY_MONTH, *plan_module.CHARGING_COST]

    plan, coordinator, added = make()
    coordinator.pricing.enabled = True
    coordinator.stream(EV, BATTERY)
    coordinator.stream(EV, ODOMETER)
    plan.charging_summary(EV)
    assert added.kinds()[-1] == plan_module.CHARGING_COST_PER_DISTANCE


def test_the_charging_summary_is_made_once_even_when_a_tariff_arrives_later() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, BATTERY)
    plan.charging_summary(EV)
    coordinator.pricing.enabled = True
    plan.charging_summary(EV)
    assert added.kinds() == [plan_module.CHARGING_ENERGY_MONTH]


def test_a_plug_in_hybrid_gets_no_range_and_no_distance_cost() -> None:
    plan, coordinator, added = make()
    coordinator.hybrids.add(EV)
    coordinator.pricing.enabled = True
    coordinator.stream(EV, BATTERY)
    coordinator.stream(EV, ODOMETER)
    plan.car(EV)
    plan.real_range(EV, force=True)
    kinds = added.kinds()
    assert plan_module.REAL_RANGE not in kinds
    assert plan_module.CHARGING_COST_PER_DISTANCE not in kinds
    assert "charging_cost_month" in kinds


def test_derived_sensors_are_updated_before_they_are_added() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, BATTERY)
    coordinator.stream(EV, ODOMETER)
    plan.car(EV)
    updated = {want.kind for wants, update in added.batches if update for want in wants}
    assert updated == {
        *SOC_TRACKING,
        plan_module.CHARGING_ENERGY_MONTH,
        plan_module.BATTERY_HEALTH,
        plan_module.DRIVING_DISTANCE,
        plan_module.REAL_RANGE,
    }


# -- descriptors --------------------------------------------------------------


def test_a_streamed_number_becomes_a_descriptor_sensor() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, SPEED, 50)
    plan.descriptor(EV, SPEED)
    plan.descriptor(EV, SPEED)
    assert added.keys(plan_module.DESCRIPTOR) == [SPEED]
    assert plan.has(Want(plan_module.DESCRIPTOR, EV, SPEED))


def test_a_boolean_belongs_to_the_binary_sensor_platform() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, DOORS, True)
    plan.descriptor(EV, DOORS)
    plan.descriptor(EV, DOORS, assume_sensor=True)
    assert added.keys(plan_module.DESCRIPTOR) == []


def test_a_descriptor_without_a_value_needs_the_registry_to_vouch_for_it() -> None:
    plan, _, added = make()
    plan.descriptor(EV, SPEED)
    assert added.keys(plan_module.DESCRIPTOR) == []
    plan.descriptor(EV, SPEED, assume_sensor=True)
    assert added.keys(plan_module.DESCRIPTOR) == [SPEED]


def test_the_position_is_never_a_descriptor_sensor() -> None:
    plan, coordinator, added = make()
    for descriptor in (LATITUDE, HEADING):
        coordinator.stream(EV, descriptor, 48.1)
        plan.descriptor(EV, descriptor, assume_sensor=True)
    assert added.keys(plan_module.DESCRIPTOR) == []
    assert LATITUDE in added.keys(plan_module.LOCATION)


def test_a_petrol_car_gets_no_sensor_for_a_battery_field() -> None:
    plan, coordinator, added = make()
    coordinator.combustion.add(PETROL)
    coordinator.stream(PETROL, BATTERY, 80)
    plan.descriptor(PETROL, BATTERY)
    assert added.keys(plan_module.DESCRIPTOR) == []


# -- the registry -------------------------------------------------------------


def test_each_derived_sensor_is_restored_as_itself() -> None:
    plan, _, added = make()
    for suffix in (
        "soc_rate",
        plan_module.BATTERY_HEALTH,
        plan_module.DRIVING_DISTANCE,
        plan_module.REAL_RANGE,
        plan_module.VEHICLE_REPORT,
        plan_module.MILEAGE_ALLOWANCE,
        "charging_cost_session",
    ):
        assert plan.restore(f"{EV}_{suffix}")
    kinds = added.kinds()
    assert plan_module.DESCRIPTOR not in kinds
    for kind in (*SOC_TRACKING, plan_module.CHARGING_ENERGY_MONTH, plan_module.MILEAGE_ALLOWANCE):
        assert kind in kinds


def test_a_restored_descriptor_comes_back_before_it_streams() -> None:
    plan, _, added = make()
    assert plan.restore(f"{EV}_{SPEED}")
    assert added.keys(plan_module.DESCRIPTOR) == [SPEED]


def test_ids_that_are_not_a_cars_are_left_alone() -> None:
    plan, _, added = make()
    for unique_id in (None, "", "nounderscore", f"{ENTRY}_diagnostics_last_message"):
        assert plan.restore(unique_id)
    assert added.batches == []


def test_tyre_sensors_come_back_from_the_registry_as_they_were() -> None:
    plan, _, added = make()
    assert plan.restore(f"{EV}_tyre_front_left")
    assert plan.restore(f"{EV}_tyre_status")
    assert added.kinds() == [plan_module.TYRE_STATUS, plan_module.TYRE_WHEEL]
    assert added.keys(plan_module.TYRE_WHEEL) == ["front_left"]


def test_tyre_sensors_of_a_car_bmw_keeps_no_tyre_data_for_are_removed() -> None:
    plan, coordinator, added = make()
    coordinator.tyre_diagnosis[EV] = {"wheels": {}}
    assert not plan.restore(f"{EV}_tyre_front_left")
    assert added.batches == []


# -- tyres --------------------------------------------------------------------


def test_only_the_wheels_bmw_reported_get_a_sensor() -> None:
    plan, coordinator, added = make()
    coordinator.tyre_diagnosis[EV] = {"wheels": {"rear_left": {}, "front_left": {}}}
    assert plan.tyre_diagnosis(EV) == []
    assert added.kinds() == [
        plan_module.TYRE_STATUS,
        plan_module.TYRE_WHEEL,
        plan_module.TYRE_WHEEL,
    ]
    assert added.keys(plan_module.TYRE_WHEEL) == ["front_left", "rear_left"]


def test_a_later_diagnosis_adds_the_wheel_the_first_one_missed() -> None:
    plan, coordinator, added = make()
    coordinator.tyre_diagnosis[EV] = {"wheels": {"front_left": {}}}
    plan.tyre_diagnosis(EV)
    coordinator.tyre_diagnosis[EV] = {"wheels": {"front_left": {}, "front_right": {}}}
    plan.tyre_diagnosis(EV)
    assert added.keys(plan_module.TYRE_WHEEL) == ["front_left", "front_right"]


def test_an_empty_diagnosis_prunes_the_cars_tyre_sensors_only() -> None:
    plan, coordinator, added = make()
    coordinator.tyre_diagnosis[EV] = {"wheels": {"front_left": {}}}
    coordinator.tyre_diagnosis[PETROL] = {"wheels": {"front_left": {}}}
    plan.known([], [], [EV, PETROL])
    coordinator.tyre_diagnosis[EV] = {"wheels": {}}
    assert sorted(plan.tyre_diagnosis(EV)) == [
        "entity:tyre:front_left",
        "entity:tyre_status:",
    ]
    assert not plan.has(Want(plan_module.TYRE_STATUS, EV))
    assert plan.has(Want(plan_module.TYRE_STATUS, PETROL))
    # Pruned once; and a diagnosis with an upstream error keeps them.
    assert plan.tyre_diagnosis(EV) == []
    coordinator.tyre_diagnosis[EV] = {"wheels": {}, "errors": ["upstream"]}
    assert plan.tyre_diagnosis(EV) == []


def test_no_diagnosis_yet_is_not_an_empty_one() -> None:
    plan, _, added = make()
    assert not plan.tyre_data_absent(EV)
    plan.tyres(EV)
    assert added.batches == []


# -- startup and the account --------------------------------------------------


def test_startup_walks_the_descriptors_the_cars_and_the_tyres() -> None:
    plan, coordinator, added = make()
    coordinator.stream(EV, SPEED, 50)
    coordinator.tyre_diagnosis[PETROL] = {"wheels": {"front_left": {}}}
    plan.known([(EV, SPEED)], [EV, PETROL], [PETROL])
    assert added.keys(plan_module.DESCRIPTOR) == [SPEED]
    assert plan_module.LOCATION in added.kinds(PETROL)
    assert plan_module.TYRE_WHEEL in added.kinds(PETROL)


def test_the_account_sensors_unique_ids() -> None:
    assert plan_module.legacy_unique_ids(ENTRY) == {
        f"{ENTRY}_connection_status": f"{ENTRY}_diagnostics_connection_status",
        f"{ENTRY}_last_message": f"{ENTRY}_diagnostics_last_message",
    }
    assert plan_module.retired_unique_ids(ENTRY) == [f"{ENTRY}_diagnostics_soc_rate"]


def test_a_diagnostic_is_added_unless_disabled_or_already_live() -> None:
    assert plan_module.add_diagnostic(disabled=False, live=False)
    assert not plan_module.add_diagnostic(disabled=True, live=False)
    assert not plan_module.add_diagnostic(disabled=False, live=True)
