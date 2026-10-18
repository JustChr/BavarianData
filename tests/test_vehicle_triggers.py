"""The rules behind the device triggers (``vehicle_triggers.py``), HA-free.

What runs them through the real coordinator is ``test_scenarios_triggers.py``;
this file pins the rules themselves: which words mean locked, when a plugged-in
car counts as stuck, when a stopped charge counts as interrupted, and which
triggers a car gets offered at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .conftest import load_module

vt = load_module("vehicle_triggers")

NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
DOOR = "vehicle.cabin.door.status"
LOCK = "vehicle.cabin.door.lock.status"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("LOCKED", True),
        ("SECURED", True),
        ("secured", True),  # restored as the entity's lower-case slug
        ("UNLOCKED", False),
        ("SELECTIVE_LOCKED", False),  # only some doors: the car can be opened
        ("selective-Locked", False),
        ("selectivelocked", False),
        ("ASN_unlocked", False),
        ("CLOSED", None),  # a MINI's door.status speaks of doors, not locks
        ("INVALID", None),
        ("", None),
        (None, None),
        (3, None),
    ],
)
def test_lock_words(value, expected):
    assert vt.lock_state({DOOR: value}) is expected


def test_lock_falls_back_to_the_rest_only_descriptor():
    assert vt.lock_state({DOOR: "CLOSED", LOCK: "SECURED"}) is True
    assert vt.lock_state({LOCK: "UNLOCKED"}) is False
    assert vt.lock_state({}) is None


def test_lock_reads_the_structured_form():
    assert vt.lock_state({DOOR: {"allDoorsLocked": "ASN_isTrue"}}) is True
    assert vt.lock_state({DOOR: {"allDoorsLocked": "ASN_isFalse"}}) is False
    assert vt.lock_state({DOOR: {"newDoorStatus": "ASN_locked"}}) is True
    assert vt.lock_state({DOOR: {"allDoorsLocked": "ASN_isUnknown"}}) is None


def test_parked_unlocked():
    assert vt.parked_unlocked(False, trip_open=False) is True
    assert vt.parked_unlocked(False, trip_open=True) is False  # driving away
    assert vt.parked_unlocked(True, trip_open=False) is False
    assert vt.parked_unlocked(None, trip_open=False) is None


def test_plugged_not_charging():
    assert vt.plugged_not_charging(True, False, 50, 80) is True
    assert vt.plugged_not_charging(True, False, None, None) is True  # nothing says it's done
    assert vt.plugged_not_charging(True, True, 50, 80) is False
    assert vt.plugged_not_charging(False, False, 50, 80) is False
    # At (or within the tolerance of) the target, stopped means done, not stuck.
    assert vt.plugged_not_charging(True, False, 80, 80) is False
    assert vt.plugged_not_charging(True, False, 79.2, 80) is False
    assert vt.plugged_not_charging(None, False, 50, 80) is None
    assert vt.plugged_not_charging(True, None, 50, 80) is None


def test_charging_interrupted_is_strict():
    assert vt.charging_interrupted(60, 80, True) is True
    assert vt.charging_interrupted(60, 80, False) is False  # the driver unplugged
    assert vt.charging_interrupted(60, 80, None) is False  # can't tell
    assert vt.charging_interrupted(79.5, 80, True) is False  # reached it
    assert vt.charging_interrupted(None, 80, True) is False
    assert vt.charging_interrupted(60, None, True) is False


def test_available_triggers_follow_what_the_car_streams():
    gps = vt.GPS_DESCRIPTORS[0]
    charge = vt.CHARGE_DESCRIPTORS[0]
    plug = "vehicle.body.chargingPort.status"

    assert vt.available_triggers([], lock_known=False, trips_enabled=True) == []
    assert vt.available_triggers([gps], lock_known=False, trips_enabled=True) == [
        "zone_arrived",
        "zone_left",
        "trip_ended",
    ]
    # Trips (history) off: the arrival logic never runs.
    assert vt.available_triggers([gps], lock_known=False, trips_enabled=False) == []
    # A car that only reports once parked makes trips from its odometer: no zones.
    assert vt.available_triggers(
        ["vehicle.vehicle.mileage"], lock_known=False, trips_enabled=True
    ) == ["trip_ended"]
    assert vt.available_triggers([], lock_known=True, trips_enabled=True) == ["parked_unlocked"]
    assert vt.available_triggers([charge], lock_known=False, trips_enabled=True) == [
        "charging_started",
        "charging_complete",
    ]
    assert vt.available_triggers(
        [vt.PRECONDITIONING_DESCRIPTOR], lock_known=False, trips_enabled=True
    ) == ["preconditioning_started", "preconditioning_finished"]
    everything = vt.available_triggers(
        [
            gps,
            charge,
            plug,
            "vehicle.cabin.sunroof.status",
            vt.PRECONDITIONING_DESCRIPTOR,
            vt.ALARM_DESCRIPTOR,
            vt.CHECK_CONTROL_DESCRIPTOR,
        ],
        lock_known=True,
        trips_enabled=True,
    )
    assert set(everything) == set(vt.TRIGGER_TYPES)


def test_event_matches():
    data = {"vin": "V1", "situation": "parked_unlocked", "zone_entity_id": "zone.home"}
    assert vt.event_matches(data, vin="V1")
    assert not vt.event_matches(data, vin="V2")
    assert vt.event_matches(data, vin="V1", situation="parked_unlocked")
    assert not vt.event_matches(data, vin="V1", situation="plugged_not_charging")
    assert vt.event_matches(data, vin="V1", zone="zone.home")
    assert not vt.event_matches(data, vin="V1", zone="zone.work")
    # ``active`` picks the start or the end of a two-sided event.
    edge = {"vin": "V1", "active": True}
    assert vt.event_matches(edge, vin="V1", active=True)
    assert not vt.event_matches(edge, vin="V1", active=False)
    assert not vt.event_matches({"vin": "V1"}, vin="V1", active=False)  # missing is not False


# -- windows ---------------------------------------------------------------------

FRONT = "vehicle.cabin.window.row1.driver.status"
SUNROOF = "vehicle.cabin.sunroof.status"
REAR_GLASS = "vehicle.body.trunk.window.isOpen"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, None),  # the car says nothing about any window
        ({FRONT: "CLOSED"}, False),
        ({FRONT: "OPEN"}, True),
        ({FRONT: "INTERMEDIATE"}, True),  # partly open lets the rain in too
        ({FRONT: "intermediate"}, True),  # the restored slug
        ({FRONT: "CLOSED", SUNROOF: "OPEN"}, True),
        ({FRONT: "CLOSED", REAR_GLASS: True}, True),
        ({FRONT: "INVALID"}, None),  # a word we don't know says nothing
        ({FRONT: "INVALID", SUNROOF: "CLOSED"}, False),
    ],
)
def test_window_open(values, expected):
    assert vt.window_open(values) is expected


def test_open_windows_names_each_opening():
    values = {FRONT: "INTERMEDIATE", SUNROOF: "CLOSED", REAR_GLASS: True}
    assert vt.open_windows(values) == ["front_driver", "rear_window"]


def test_a_window_open_while_driving_is_not_parked_open():
    assert vt.parked_window_open(True, trip_open=False) is True
    assert vt.parked_window_open(True, trip_open=True) is False
    assert vt.parked_window_open(False, trip_open=False) is False
    assert vt.parked_window_open(None, trip_open=False) is None


# -- edges -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("on", True), ("OFF", False), ("maybe", None), (None, None)],
)
def test_alarm_on(value, expected):
    assert vt.alarm_on(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HEATING", True),
        ("heating", True),  # the restored slug
        ("COOLING", True),
        ("VENTILATION", True),
        ("INACTIVE", False),
        ("STANDBY", False),
        ("UNKNOWN", None),
        (None, None),
    ],
)
def test_preconditioning_active(value, expected):
    assert vt.preconditioning_active(value) is expected


def test_fresh_means_stamped_within_ten_minutes():
    assert vt.fresh(None, NOW)
    assert vt.fresh(NOW - timedelta(minutes=10), NOW)
    assert not vt.fresh(NOW - timedelta(minutes=11), NOW)


def test_edge_tracker_seeds_fill_gaps_and_the_stream_moves():
    edges = vt.EdgeTracker()
    edges.seed("V", "alarm", False)  # restored
    edges.seed("V", "alarm", True)  # a later seed (a REST cache) changes nothing
    assert edges.move("V", "alarm", True) is False
    assert edges.move("V", "alarm", False) is True
    assert edges.move("V", "other", True) is None  # never known before
    assert edges.move("V", "alarm", None) is False  # no reading moves nothing
    assert edges.move("V", "alarm", True) is False


def test_message_changes():
    washer = {"messageType": "CCM", "id": 164, "text": "Washer fluid low"}
    tyre = {"messageType": "CCM", "id": 52, "text": "Tyre pressure"}
    assert vt.message_changes(None, [washer]) is None  # the first list only seeds
    assert vt.message_changes([], [washer]) == ([washer], [])
    assert vt.message_changes([washer], [washer, tyre]) == ([tyre], [])
    assert vt.message_changes([washer, tyre], [tyre]) == ([], [washer])
    # The same message with a new text or timestamp is still the same message.
    assert vt.message_changes([washer], [{**washer, "text": "Washer fluid very low"}]) == ([], [])


def test_tracker_reports_only_changes():
    tracker = vt.SituationTracker()
    assert tracker.update("V", "parked_unlocked", True, NOW) is True
    assert tracker.update("V", "parked_unlocked", True, NOW + timedelta(minutes=1)) is None
    assert tracker.since("V", "parked_unlocked") == NOW  # the start, not the latest sample
    assert tracker.active("V") == ["parked_unlocked"]
    assert tracker.update("V", "parked_unlocked", False, NOW) is False
    assert tracker.update("V", "parked_unlocked", False, NOW) is None
    assert tracker.since("V", "parked_unlocked") is None


def test_unknown_ends_a_running_situation():
    tracker = vt.SituationTracker()
    tracker.update("V", "parked_unlocked", True, NOW)
    assert tracker.update("V", "parked_unlocked", None, NOW) is False
    assert tracker.update("V", "parked_unlocked", None, NOW) is None  # and starts nothing


def test_tracker_keeps_vehicles_apart():
    tracker = vt.SituationTracker()
    tracker.update("A", "parked_unlocked", True, NOW)
    assert tracker.update("B", "parked_unlocked", False, NOW) is None
    assert tracker.active("B") == []


class _Timers:
    def __init__(self) -> None:
        self.pending: list[list] = []

    def call_later(self, seconds, callback):
        entry = [seconds, callback, False]
        self.pending.append(entry)

        def cancel() -> None:
            entry[2] = True

        return cancel

    def run(self) -> None:
        for entry in list(self.pending):
            if not entry[2]:
                entry[2] = True
                entry[1](None)


def test_delay_fires_once_if_the_situation_lasts():
    timers, fired = _Timers(), []
    delay = vt.SituationDelay(600, timers.call_later, fired.append)
    delay.update(True, {"since": "t0"})
    delay.update(True, {"since": "t1"})  # the same situation: no second timer
    assert len(timers.pending) == 1
    timers.run()
    assert fired == [{"since": "t0"}]
    assert not delay.pending


def test_delay_is_cancelled_when_the_situation_ends():
    timers, fired = _Timers(), []
    delay = vt.SituationDelay(600, timers.call_later, fired.append)
    delay.update(True, {})
    delay.update(False, {})
    timers.run()
    assert fired == []
    # A fresh start afterwards gets a fresh timer.
    delay.update(True, {"n": 2})
    timers.run()
    assert fired == [{"n": 2}]
