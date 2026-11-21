"""BMW's "no reading" zero, run through the real coordinator.

Issue #100, a petrol MINI Countryman JCW: BMW sent the trip-end high-voltage
battery charge once, as 0, stamped six days earlier. The car has no such
battery. Shown as a battery sensor at 0 %, it read as a flat battery and raised
the user's low-battery alert. No car with that battery ends a trip at 0 %, so a
zero there is BMW's placeholder: it creates no entity, clears one that already
holds a value, and does not come back from a restart.

The battery's as-new size is the second: an i5 eDrive40 sends
``batterySizeMax`` as 0.0, shown as a battery of 0.0 kWh. Every rule below
runs for both.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

placeholders = load_module("placeholders")

TRIP_END_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"
BATTERY_SIZE = "vehicle.drivetrain.batteryManagement.batterySizeMax"
# (descriptor, a real reading, its unit)
ZEROS = pytest.mark.parametrize(
    ("descriptor", "real", "unit"),
    [(TRIP_END_SOC, 64, "%"), (BATTERY_SIZE, 84.3, "kWh")],
    ids=["trip-end hvSoc", "batterySizeMax"],
)


@pytest.fixture
def h():
    harness = CoordinatorHarness(capacity_kwh=None)
    yield harness
    harness.close()


def _state(h: CoordinatorHarness, descriptor: str):
    return h.coordinator.get_state(h.vin, descriptor)


@ZEROS
def test_a_zero_creates_no_entity(h, descriptor, real, unit):
    h.send(10, extra={descriptor: 0}, step="a zero")

    assert _state(h, descriptor) is None
    assert descriptor not in h.coordinator.seen_descriptors(h.vin)


@ZEROS
def test_a_zero_clears_a_reading_the_entity_already_holds(h, descriptor, real, unit):
    h.send(10, extra={descriptor: real}, step="a real reading")
    assert _state(h, descriptor).value == real

    h.send(90, extra={descriptor: 0}, step="a zero")

    assert _state(h, descriptor) is not None and _state(h, descriptor).value is None


@ZEROS
def test_a_saved_zero_does_not_come_back_from_a_restart(h, descriptor, real, unit):
    # An install from before the rule saved the zero; HA hands it back as text.
    h.coordinator.restore_descriptor_state(
        h.vin, descriptor, "0.0", unit, "2026-10-06T16:08:58.089Z"
    )

    assert _state(h, descriptor) is None


@ZEROS
def test_a_real_reading_survives_a_restart(h, descriptor, real, unit):
    h.send(10, extra={descriptor: real}, step="a real reading")
    h.restart()

    assert _state(h, descriptor).value == real


def test_a_battery_without_its_size_has_no_as_new_figure(h):
    h.send(10, extra={BATTERY_SIZE: 0.0}, step="batterySizeMax 0.0")
    assert h.coordinator.battery_nominal_kwh(h.vin) is None

    h.send(20, extra={BATTERY_SIZE: 84.3}, step="batterySizeMax 84.3")
    assert h.coordinator.battery_nominal_kwh(h.vin) == 84.3


@pytest.mark.parametrize("descriptor", [TRIP_END_SOC, BATTERY_SIZE])
@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0"])
def test_zero_in_any_spelling_is_a_placeholder(descriptor, value):
    assert placeholders.is_placeholder(descriptor, value)


@pytest.mark.parametrize("descriptor", [TRIP_END_SOC, BATTERY_SIZE])
@pytest.mark.parametrize("value", [1, 64, "64", None, False, "", "n/a"])
def test_anything_else_is_a_reading(descriptor, value):
    assert not placeholders.is_placeholder(descriptor, value)


def test_a_zero_elsewhere_is_a_reading():
    # The rule is this one descriptor's: a 0 % live charge is real on an empty car.
    assert not placeholders.is_placeholder("vehicle.drivetrain.batteryManagement.header", 0)
