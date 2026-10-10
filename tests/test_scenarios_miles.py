"""A range sent in miles reaches the sensor in kilometres (issue #62).

A UK iX1 streamed ``remainingElectricRange`` as ``miles`` while its neighbours
stayed ``km``. The sensor's unit is pinned to km by the catalogue, so the raw
number used to be shown as kilometres.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

units = load_module("units")

RANGE = "vehicle.drivetrain.electricEngine.remainingElectricRange"
LAST_RANGE = "vehicle.drivetrain.lastRemainingRange"


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def _stream(h: CoordinatorHarness, data: dict) -> None:
    stamp = h.at(h.minute).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = {path: {**body, "timestamp": stamp} for path, body in data.items()}
    h._run(h.coordinator.async_handle_message({"vin": h.vin, "data": payload}))


def test_a_range_in_miles_is_stored_in_kilometres(h) -> None:
    _stream(
        h,
        {RANGE: {"value": 100, "unit": "miles"}, LAST_RANGE: {"value": 160, "unit": "km"}},
    )
    miles = h.coordinator.get_state(h.vin, RANGE)
    assert (miles.value, miles.unit) == (160.934, "km")
    # A neighbour that already says km is left alone.
    km = h.coordinator.get_state(h.vin, LAST_RANGE)
    assert (km.value, km.unit) == (160, "km")


@pytest.mark.parametrize("unit", ["mi", "mile", "Miles", " MILES "])
def test_every_spelling_of_miles_converts(unit) -> None:
    assert units.miles_to_km(10, unit) == (16.093, "km")


@pytest.mark.parametrize(
    "value, unit",
    [(10, "km"), (10, None), ("n/a", "miles"), (True, "miles"), (None, "miles"), ([1], "miles")],
)
def test_anything_else_passes_through(value, unit) -> None:
    assert units.miles_to_km(value, unit) == (value, unit)
