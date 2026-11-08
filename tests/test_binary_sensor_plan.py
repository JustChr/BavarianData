"""Which binary sensors each car gets, and what they show, without Home Assistant."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from .conftest import load_module

plan_module = load_module("binary_sensor_plan")

EV = "WBAEXAMPLE0000001"
PETROL = "WBAEXAMPLE0000002"
DOOR = "vehicle.cabin.door.row1.driver.isOpen"
PLUG = "vehicle.body.chargingPort.isPlugged"


@pytest.mark.parametrize(
    ("descriptor", "expected"),
    [
        ("vehicle.cabin.door.status.isLocked", ("lock", True)),
        (PLUG, ("plug", False)),
        ("vehicle.isMoving", ("moving", False)),
        ("vehicle.drivetrain.engine.isActive", ("running", False)),
        ("vehicle.vehicle.isIgnitionOn", ("running", False)),
        ("vehicle.cabin.infotainment.isMobilePhoneConnected", ("connectivity", False)),
        ("vehicle.cabin.window.row1.driver.isOpen", ("window", False)),
        (DOOR, ("opening", False)),
        ("vehicle.cabin.sunroof.isTilted", (None, False)),
    ],
)
def test_device_class_and_polarity(descriptor: str, expected) -> None:
    assert plan_module.device_class(descriptor) == expected


def test_every_device_class_is_one_home_assistant_knows() -> None:
    # BinarySensorDeviceClass values; the platform builds the enum from them.
    known = {"lock", "plug", "moving", "running", "connectivity", "window", "opening"}
    for descriptor in ("x.isLocked", "x.isPlugged", "x.isMoving", "x.isOpen", "x.window.isOpen"):
        assert plan_module.device_class(descriptor)[0] in known


def test_a_parked_car_carries_no_trip_figures() -> None:
    assert plan_module.trip_attributes(None) == {}


def test_the_drive_so_far() -> None:
    attrs = plan_module.trip_attributes(
        {
            "start": "2026-10-24T08:00:00+00:00",
            "start_place": {"label": "Home"},
            "distance_km": 12.3,
            "soc_end": 70,
            "held": True,
        }
    )
    assert attrs["started"] == "2026-10-24T08:00:00+00:00"
    assert attrs["start_location"] == "Home"
    assert attrs["soc_now"] == 70
    assert attrs["held"] is True
    assert attrs["duration_s"] is None
    assert plan_module.trip_attributes({})["start_location"] is None


def test_the_trip_sensor_writes_edges_at_once_and_figures_once_a_minute() -> None:
    interval = plan_module.TRIP_PROGRESS_WRITE_INTERVAL_S
    assert plan_module.trip_write_due(True, False, 0)
    assert plan_module.trip_write_due(False, True, 0)
    assert not plan_module.trip_write_due(False, False, 10_000)
    assert not plan_module.trip_write_due(True, True, interval - 1)
    assert plan_module.trip_write_due(True, True, interval)


class FakeCoordinator:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], Any] = {}
        self.combustion: set[str] = set()

    def get_state(self, vin: str, descriptor: str) -> Any:
        return self.states.get((vin, descriptor))

    def is_combustion_only(self, vin: str) -> bool:
        return vin in self.combustion

    def seen_descriptors(self, vin: str) -> set[str]:
        return {descriptor for (v, descriptor) in self.states if v == vin}


def make():
    coordinator = FakeCoordinator()
    made: list[tuple[str, ...]] = []
    plan = plan_module.BinarySensorPlan(
        coordinator,
        lambda vin, descriptor: made.append((vin, descriptor)),
        lambda vin: made.append((vin, plan_module.TRIP_IN_PROGRESS)),
        lambda vin: made.append((vin, plan_module.CHARGING_CABLE)),
    )
    return plan, coordinator, made


def test_a_streamed_boolean_becomes_a_binary_sensor_once() -> None:
    plan, coordinator, made = make()
    coordinator.states[(EV, DOOR)] = SimpleNamespace(value=True)
    plan.descriptor(EV, DOOR)
    plan.descriptor(EV, DOOR)
    assert made == [(EV, DOOR)]


def test_a_number_is_not_a_binary_sensor_and_silence_needs_the_registry() -> None:
    plan, coordinator, made = make()
    coordinator.states[(EV, DOOR)] = SimpleNamespace(value=3)
    plan.descriptor(EV, DOOR, assume_binary=True)
    plan.descriptor(EV, PLUG)
    assert made == []
    plan.restore(f"{EV}_{PLUG}")
    assert made == [(EV, PLUG)]


def test_a_petrol_car_gets_no_plug() -> None:
    plan, coordinator, made = make()
    coordinator.combustion.add(PETROL)
    plan.restore(f"{PETROL}_{PLUG}")
    assert made == []


def test_the_trip_flag_comes_back_as_itself() -> None:
    plan, _, made = make()
    plan.restore(f"{EV}_trip_in_progress")
    plan.restore(None)
    plan.restore("nounderscore")
    assert made == [(EV, plan_module.TRIP_IN_PROGRESS)]


def test_every_known_car_and_a_late_one_get_a_trip_flag() -> None:
    plan, coordinator, made = make()
    coordinator.states[(EV, DOOR)] = SimpleNamespace(value=False)
    plan.known([(EV, DOOR)], [EV])
    plan.new_descriptor(PETROL, DOOR)
    assert made == [
        (EV, DOOR),
        (EV, plan_module.TRIP_IN_PROGRESS),
        (PETROL, plan_module.TRIP_IN_PROGRESS),
    ]


def test_only_an_opening_trip_edge_adds_the_flag() -> None:
    plan, _, made = make()
    plan.trip_edge(EV, False)
    assert made == []
    plan.trip_edge(EV, True)
    plan.trip_edge(EV, True)
    assert made == [(EV, plan_module.TRIP_IN_PROGRESS)]


# --- the charging cable (SpotNav#2) -------------------------------------------

PORT = "vehicle.body.chargingPort.status"
IS_PLUGGED = "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged"
CABLE = plan_module.CHARGING_CABLE


def test_a_car_reporting_its_port_gets_one_cable_sensor() -> None:
    plan, coordinator, made = make()
    coordinator.states[(EV, PORT)] = SimpleNamespace(value="connected")
    plan.new_sensor(EV, "vehicle.drivetrain.batteryManagement.header")
    assert made == []
    plan.new_sensor(EV, PORT)
    plan.new_sensor(EV, PORT)
    assert made == [(EV, CABLE)]


def test_a_car_seen_before_the_update_gets_it_at_the_first_start() -> None:
    # The coverage store remembers the port across restarts, so ``known`` finds it
    # even before the port sensor has restored or the stream has spoken.
    plan, coordinator, made = make()
    coordinator.states[(EV, PORT)] = SimpleNamespace(value="disconnected")
    plan.known([], [EV, PETROL])
    assert made == [
        (EV, plan_module.TRIP_IN_PROGRESS),
        (EV, CABLE),
        (PETROL, plan_module.TRIP_IN_PROGRESS),
    ]


def test_a_streamed_is_plugged_also_gives_the_cable_sensor() -> None:
    plan, coordinator, made = make()
    coordinator.states[(EV, IS_PLUGGED)] = SimpleNamespace(value=True)
    plan.new_descriptor(EV, IS_PLUGGED)
    assert (EV, IS_PLUGGED) in made and (EV, CABLE) in made


def test_the_cable_sensor_comes_back_as_itself_but_never_on_a_petrol_car() -> None:
    plan, coordinator, made = make()
    coordinator.combustion.add(PETROL)
    plan.restore(f"{EV}_{CABLE}")
    plan.restore(f"{PETROL}_{CABLE}")
    assert made == [(EV, CABLE)]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({PORT: "CONNECTED"}, True),
        ({PORT: "connected"}, True),  # restored as the entity's lowercase state
        ({PORT: "DISCONNECTED"}, False),
        ({PORT: "INVALID"}, None),
        ({}, None),
        ({IS_PLUGGED: False, PORT: "CONNECTED"}, False),  # the boolean answers first
        ({PORT: "INVALID", "vehicle.body.chargingPort.statusClearText": "CONNECTED"}, True),
    ],
)
def test_the_cable_reads_the_same_rule_as_evcc(values: dict[str, Any], expected) -> None:
    coordinator = FakeCoordinator()
    for descriptor, value in values.items():
        coordinator.states[(EV, descriptor)] = SimpleNamespace(value=value)
    assert plan_module.plugged(coordinator, EV) is expected


def test_the_restored_state_holds_only_until_the_car_speaks() -> None:
    shown = plan_module.cable_shown
    assert shown(None, True, heard=False) is True  # just restarted
    assert shown(False, True, heard=False) is False  # the port sensor restored first
    assert shown(None, True, heard=True) is None  # the car said INVALID: unknown, not stale
    assert shown(True, None, heard=True) is True
