"""BMW's "no reading" zero, run through the real coordinator.

Issue #100, a petrol MINI Countryman JCW: BMW sent the trip-end high-voltage
battery charge once, as 0, stamped six days earlier. The car has no such
battery. Shown as a battery sensor at 0 %, it read as a flat battery and raised
the user's low-battery alert. No car with that battery ends a trip at 0 %, so a
zero there is BMW's placeholder: it creates no entity, clears one that already
holds a value, and does not come back from a restart.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

placeholders = load_module("placeholders")

TRIP_END_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"


@pytest.fixture
def h():
    harness = CoordinatorHarness(capacity_kwh=None)
    yield harness
    harness.close()


def _state(h: CoordinatorHarness):
    return h.coordinator.get_state(h.vin, TRIP_END_SOC)


def test_a_zero_on_a_petrol_car_creates_no_entity(h):
    h.send(10, extra={TRIP_END_SOC: 0}, step="trip end: hvSoc 0")

    assert _state(h) is None
    assert TRIP_END_SOC not in h.coordinator.seen_descriptors(h.vin)


def test_a_zero_clears_a_reading_the_entity_already_holds(h):
    h.send(10, extra={TRIP_END_SOC: 64}, step="trip end: hvSoc 64")
    assert _state(h).value == 64

    h.send(90, extra={TRIP_END_SOC: 0}, step="trip end: hvSoc 0")

    assert _state(h) is not None and _state(h).value is None


def test_a_saved_zero_does_not_come_back_from_a_restart(h):
    # An install from before the rule saved the zero; HA hands it back as text.
    h.coordinator.restore_descriptor_state(
        h.vin, TRIP_END_SOC, "0", "%", "2026-10-06T16:08:58.089Z"
    )

    assert _state(h) is None


def test_a_real_reading_survives_a_restart(h):
    h.send(10, extra={TRIP_END_SOC: 64}, step="trip end: hvSoc 64")
    h.restart()

    assert _state(h).value == 64


@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0"])
def test_zero_in_any_spelling_is_a_placeholder(value):
    assert placeholders.is_placeholder(TRIP_END_SOC, value)


@pytest.mark.parametrize("value", [1, 64, "64", None, False, "", "n/a"])
def test_anything_else_is_a_reading(value):
    assert not placeholders.is_placeholder(TRIP_END_SOC, value)


def test_a_zero_elsewhere_is_a_reading():
    # The rule is this one descriptor's: a 0 % live charge is real on an empty car.
    assert not placeholders.is_placeholder("vehicle.drivetrain.batteryManagement.header", 0)
