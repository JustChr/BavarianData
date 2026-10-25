"""``VehicleTrips`` on a bare host: a breaking one is logged, never raised.

The scenarios in ``test_scenarios_trips.py`` drive the engine through the
coordinator, where a logged error fails the step -- so the guards that keep a
fault in trip detection from breaking the stream cannot be reached there. Each
entry the coordinator calls per batch is tried here with a host that breaks
inside it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .conftest import load_module
from .fake_ha import load_with_fake_ha

car_state = load_module("car_state")
trips = load_with_fake_ha("vehicle_trips")

VIN = "WBA00000000000001"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


class _Host:
    """Just enough of the coordinator to reach each entry's guard."""

    history = object()  # a store is there: the engine is switched on
    trip_debug = False

    def __init__(self, *, peek_breaks: bool) -> None:
        self.peek_breaks = peek_breaks
        self.car = car_state.Vehicle(VIN)

    def _car(self, vin):
        return self.car

    def _peek(self, vin):
        if self.peek_breaks:
            raise RuntimeError("host gone")
        return self.car

    def get_state(self, vin, descriptor):
        return None  # an unstamped fix

    def _coordinate(self, vin, axis):
        return "garbled"  # a tracker step on this raises


def _run(coro) -> None:
    asyncio.run(coro)


def test_a_breaking_host_never_breaks_the_stream(caplog):
    engine = trips.VehicleTrips(_Host(peek_breaks=True))

    with caplog.at_level(logging.ERROR):
        _run(engine.process_trip_signals(VIN, NOW, True, None, False))
        _run(engine.process_door_signal(VIN, NOW, True))

    assert "Trip detection failed" in caplog.text
    assert "Door trip detection failed" in caplog.text
    assert VIN not in caplog.text


def test_a_garbled_fix_never_breaks_the_stream(caplog):
    engine = trips.VehicleTrips(_Host(peek_breaks=False))

    with caplog.at_level(logging.ERROR):
        _run(engine.process_gps_signal(VIN, NOW))

    assert "GPS trip detection failed" in caplog.text
    assert VIN not in caplog.text
