"""``VehicleEvents`` on a bare host: the edges the harness cannot reach.

The scenarios in ``test_scenarios_triggers.py`` drive the events through the
coordinator, where every value carries a good timestamp and a logged error
fails the step. These cover the rest: a stamp missing or garbled, a host that
breaks mid-evaluation, a restored Check Control that isn't a list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .conftest import load_module

events = load_module("vehicle_events")
triggers = load_module("vehicle_triggers")
const = load_module("const")

VIN = "WBA00000000000001"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
ALARM = triggers.ALARM_DESCRIPTOR


class _Host:
    entry_id = "entry-1"

    def __init__(self, values=None, broken=False):
        self.values = dict(values or {})
        self.broken = broken
        self.fired = []

    def _value(self, vin, descriptor):
        if self.broken:
            raise RuntimeError("host gone")
        return self.values.get(descriptor)

    def _peek(self, vin):
        raise RuntimeError("host gone")

    def _current_zone(self, vin):
        return None

    def _fire(self, event_type, payload):
        self.fired.append((event_type, payload))


def test_a_stamp_missing_or_garbled_counts_as_now():
    host = _Host({ALARM: True})
    engine = events.VehicleEvents(host)
    engine.seed_edge(VIN, ALARM, False)

    engine.note_edges(VIN, {}, [ALARM], NOW, "stream")
    host.values[ALARM] = False
    engine.note_edges(VIN, {ALARM: {"timestamp": "not a time"}}, [ALARM], NOW, "stream")
    host.values[ALARM] = True
    engine.note_edges(VIN, {ALARM: {"timestamp": "2026-10-24T12:00:00"}}, [ALARM], NOW, "stream")

    assert [event for event, _ in host.fired] == [const.EVENT_ALARM] * 3


def test_a_breaking_host_is_logged_never_raised(caplog):
    engine = events.VehicleEvents(_Host(broken=True))

    with caplog.at_level(logging.ERROR):
        engine.evaluate_situations(VIN, NOW)
        engine.note_edges(VIN, {}, [ALARM], NOW, "stream")

    assert "Trigger evaluation failed" in caplog.text
    assert "Edge trigger evaluation failed" in caplog.text
    assert VIN not in caplog.text


def test_a_restored_check_control_that_is_no_list_seeds_nothing():
    engine = events.VehicleEvents(_Host())

    engine.seed_edge(VIN, triggers.CHECK_CONTROL_DESCRIPTOR, "garbage")
    engine.seed_edge(VIN, "vehicle.something.else", True)
