"""A cleared Check Control list, run through the real coordinator.

The i5, 2026-09-26/27: the washer fluid was topped up, the car started three
drives without the warning, and BMW's REST reply then carried
``checkControlMessages`` with value, unit and timestamp all null. The coordinator
skipped the null like any other, so the washer-fluid warning stayed on the
sensor and the card for good. A null here is BMW's "no messages": the count
drops to zero and the list empties.
"""

from __future__ import annotations

import pytest

from .harness import CoordinatorHarness

CC = "vehicle.status.checkControlMessages"

# What the i5's REST reply held before the refill, and after it.
WASHER = [
    {
        "date": None,
        "description": None,
        "id": 164,
        "messageType": "CCM",
        "status": "NULL",
        "title": None,
        "text": "The washer fluid level is low in the window washer reservoir.",
        "unitOfLengthRemaining": "20039",
    }
]
CLEARED = {"value": None, "unit": None, "timestamp": None}


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def _rest(h: CoordinatorHarness, entry: dict, step: str) -> None:
    h._run(h.coordinator.async_handle_message({"vin": h.vin, "data": {CC: entry}}))
    h._observe(step)


def _value(h: CoordinatorHarness):
    state = h.coordinator.get_state(h.vin, CC)
    return None if state is None else state.value


def test_a_null_check_control_list_clears_the_warning(h):
    h.send(10, extra={CC: WASHER}, step="poll: washer fluid low")
    assert _value(h) == WASHER

    h.advance_to(60 * 24)
    _rest(h, CLEARED, "poll: BMW answers null")

    assert _value(h) == []


def test_a_null_check_control_list_on_a_car_that_never_had_one_is_empty(h):
    _rest(h, CLEARED, "poll: BMW answers null")
    assert _value(h) == []


def test_other_null_descriptors_still_keep_their_last_value(h):
    # The rule is Check Control's alone: a null elsewhere stays "no news".
    h.send(10, soc=55, step="soc 55")
    h._run(
        h.coordinator.async_handle_message(
            {
                "vin": h.vin,
                "data": {
                    "vehicle.drivetrain.batteryManagement.header": CLEARED,
                },
            }
        )
    )
    state = h.coordinator.get_state(h.vin, "vehicle.drivetrain.batteryManagement.header")
    assert state is not None and state.value == 55
