"""The device triggers' events, run through the real coordinator.

Each scenario asserts when a trigger event fires -- and, as much to the point,
when it must *not*: on a restart, on a charge that merely flapped, on a driver
who unplugged early, on a car that is simply done charging. The rules
themselves are pinned in ``test_vehicle_triggers.py``; these check the wiring:
live values only, a trip opening ends "parked unlocked", arrival fires on the
trip close and never from the stop flush.

Home is a 150 m zone; positions step ~1.3 km per fix, as in the trip scenarios.
"""

from __future__ import annotations

import pytest

from .harness import CoordinatorHarness

HOME = (48.1000, 16.3000)
AWAY = (48.1500, 16.3500)
STEP = 0.01
DOOR = "vehicle.cabin.door.status"

SITUATION = "bavariandata_situation"


def _harness(at=HOME) -> CoordinatorHarness:
    harness = CoordinatorHarness(home=at)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    return harness


@pytest.fixture
def h():
    harness = _harness()
    yield harness
    harness.close()


def _situations(h: CoordinatorHarness) -> list[tuple[str, bool]]:
    return [(data["situation"], data["active"]) for _name, data in h.events(SITUATION)]


def _drive(h, start, points, every=2):
    for i, (lat, lon) in enumerate(points, 1):
        h.fix(start + i * every, lat, lon)


def _line(origin, target, fixes):
    return [
        (
            origin[0] + (target[0] - origin[0]) * i / fixes,
            origin[1] + (target[1] - origin[1]) * i / fixes,
        )
        for i in range(1, fixes + 1)
    ]


# -- zones ---------------------------------------------------------------------


def test_arriving_home_fires_once_the_car_has_parked(snapshot):
    with _harness(at=AWAY) as h:
        _drive(h, 5, _line(AWAY, HOME, 5))
        assert h.events("bavariandata_zone") == []  # still rolling in: not yet
        h.fix(20, *HOME)  # parked, still reporting
        h.advance_to(40)

        (arrived,) = h.events("bavariandata_zone_arrived")
        data = arrived[1]
        assert data["zone"] == "Home"
        assert data["zone_entity_id"] == "zone.home"
        assert data["distance_km"] > 0
        assert data["trip_id"] == h.trips()[0].id
        assert h.events("bavariandata_zone_left") == []  # it started in no zone
        assert h.render() == snapshot


def test_a_round_trip_leaves_on_the_way_out_and_arrives_back(h):
    out = [(HOME[0] + i * STEP, HOME[1] + i * STEP) for i in range(1, 4)]
    _drive(h, 5, out)
    # "Left" as soon as the car is out of the zone, not when the trip ends.
    (left,) = h.events("bavariandata_zone_left")
    assert left[1]["zone_entity_id"] == "zone.home"

    _drive(h, 11, list(reversed(out[:-1])) + [HOME])
    h.fix(25, *HOME)
    h.advance_to(45)

    assert [name for name, _ in h.events("bavariandata_zone")] == [
        "bavariandata_zone_left",
        "bavariandata_zone_arrived",
    ]


def test_the_stop_flush_announces_nothing(h):
    """HA stopping mid-drive closes the trip -- that is not an arrival."""

    with _harness(at=AWAY) as h:
        _drive(h, 5, _line(AWAY, HOME, 5))
        h.restart()
        assert h.events("bavariandata_zone_arrived") == []
        assert len(h.trips()) == 1  # the trip itself is still kept


# -- parked unlocked -------------------------------------------------------------


def test_unlocked_begins_and_ends_with_the_lock(h, snapshot):
    h.send(1, extra={DOOR: "UNLOCKED"})
    h.send(2, extra={DOOR: "SECURED"})
    h.send(3, extra={DOOR: "SELECTIVE_LOCKED"})
    h.advance_to(20)

    assert _situations(h) == [
        ("parked_unlocked", True),
        ("parked_unlocked", False),
        ("parked_unlocked", True),
    ]
    first = h.events(SITUATION)[0][1]
    assert first["zone"] == "Home"
    assert h.render() == snapshot


def test_driving_off_unlocked_ends_the_situation(h):
    h.send(1, extra={DOOR: "UNLOCKED"})
    h.fix(5, HOME[0] + STEP, HOME[1] + STEP)  # a trip opens

    assert _situations(h) == [("parked_unlocked", True), ("parked_unlocked", False)]


def test_a_lock_word_the_car_does_not_use_starts_nothing(h):
    h.send(1, extra={DOOR: "CLOSED"})  # a MINI's door.status
    h.advance_to(30)
    assert _situations(h) == []


def test_a_restored_unlock_starts_nothing(h):
    """A car locked while HA was down must not raise "left unlocked" on boot."""

    h.send(1, extra={DOOR: "UNLOCKED"})
    h.restart()
    h.send(5, soc=60)  # the car speaks -- but not about its lock
    h.advance_to(30)
    assert _situations(h) == [("parked_unlocked", True)]  # only the pre-restart one

    h.send(31, extra={DOOR: "UNLOCKED"})  # a live word does count
    assert _situations(h) == [("parked_unlocked", True), ("parked_unlocked", True)]


# -- plugged in but not charging -----------------------------------------------------


def test_plugged_in_and_waiting_is_a_situation(h, snapshot):
    h.send(1, soc=50, target=80, plug="CONNECTED", status="NOCHARGING")
    h.send(20, status="CHARGINGACTIVE", power=11000)

    assert _situations(h) == [
        ("plugged_not_charging", True),
        ("plugged_not_charging", False),
    ]
    begin = h.events(SITUATION)[0][1]
    assert begin["soc"] == 50
    assert begin["target_soc"] == 80
    assert h.render() == snapshot


def test_a_car_at_its_target_is_done_not_stuck(h):
    h.send(1, soc=80, target=80, plug="CONNECTED", status="NOCHARGING")
    h.advance_to(30)
    assert _situations(h) == []


def test_unplugging_ends_the_wait(h):
    h.send(1, soc=50, target=80, plug="CONNECTED", status="NOCHARGING")
    h.send(5, plug="DISCONNECTED")
    assert _situations(h) == [
        ("plugged_not_charging", True),
        ("plugged_not_charging", False),
    ]


# -- charging interrupted ------------------------------------------------------------


def _charging(h: CoordinatorHarness) -> None:
    h.send(1, soc=50, target=80, plug="CONNECTED", status="CHARGINGACTIVE", power=11000)


def test_a_charge_that_stops_short_with_the_cable_in_is_interrupted(h, snapshot):
    _charging(h)
    h.send(
        30,
        status="NOCHARGING",
        power=0,
        extra={"vehicle.drivetrain.electricEngine.charging.reasonChargingEnd": "CHARGING_PAUSED"},
    )
    assert h.events("bavariandata_charging_interrupted") == []  # the close debounce first
    h.advance_to(35)

    (interrupted,) = h.events("bavariandata_charging_interrupted")
    assert interrupted[1]["reason"] == "CHARGING_PAUSED"
    assert interrupted[1]["target_soc"] == 80
    assert h.render() == snapshot


def test_unplugging_early_is_not_an_interruption(h):
    _charging(h)
    h.send(30, status="NOCHARGING", power=0, plug="DISCONNECTED")
    h.advance_to(35)
    assert h.events("bavariandata_charging_interrupted") == []
    assert [name for name, _ in h.events()][-1] == "bavariandata_charging_stopped"


def test_a_flap_is_not_an_interruption(h):
    _charging(h)
    h.send(30, status="NOCHARGING", power=0)
    h.send(31, status="CHARGINGACTIVE", power=11000)
    h.advance_to(40)
    assert h.events("bavariandata_charging_interrupted") == []


def test_reaching_the_target_completes_rather_than_interrupts(h):
    h.send(1, soc=79, target=80, plug="CONNECTED", status="CHARGINGACTIVE", power=11000)
    h.send(10, soc=80, status="CHARGINGENDED", power=0)
    h.advance_to(15)
    assert h.events("bavariandata_charging_interrupted") == []
    assert h.events("bavariandata_charging_complete")


# -- trip ended ----------------------------------------------------------------------


def test_a_trip_ending_outside_any_zone_still_ends(h, snapshot):
    """``zone_arrived`` needs a zone; ``trip_ended`` fires for every recorded trip."""

    _drive(h, 5, _line(HOME, AWAY, 5))
    h.fix(20, *AWAY)
    h.advance_to(40)

    assert h.events("bavariandata_zone_arrived") == []
    (ended,) = h.events("bavariandata_trip_ended")
    data = ended[1]
    assert data["zone"] is None
    assert data["zone_entity_id"] is None
    assert data["trip_id"] == h.trips()[0].id
    assert data["distance_km"] > 0
    assert data["trip_end"] is not None
    assert h.render() == snapshot


def test_arriving_in_a_zone_fires_both_with_one_trip(h):
    with _harness(at=AWAY) as h:
        _drive(h, 5, _line(AWAY, HOME, 5))
        h.fix(20, *HOME)
        h.advance_to(40)
        (arrived,) = h.events("bavariandata_zone_arrived")
        (ended,) = h.events("bavariandata_trip_ended")
        assert ended[1]["zone_entity_id"] == "zone.home"
        assert ended[1]["trip_id"] == arrived[1]["trip_id"]


def test_the_stop_flush_ends_no_trip(h):
    _drive(h, 5, _line(HOME, AWAY, 5))
    h.restart()
    assert h.events("bavariandata_trip_ended") == []


# -- parked with a window open -------------------------------------------------------

WINDOW = "vehicle.cabin.window.row1.driver.status"
SUNROOF = "vehicle.cabin.sunroof.status"


def test_a_window_left_open_is_a_situation(h, snapshot):
    h.send(1, extra={WINDOW: "INTERMEDIATE", SUNROOF: "CLOSED"})
    h.send(30, extra={WINDOW: "CLOSED"})

    assert _situations(h) == [("parked_window_open", True), ("parked_window_open", False)]
    begin = h.events(SITUATION)[0][1]
    assert begin["windows"] == ["front_driver"]
    assert begin["zone"] == "Home"
    assert h.render() == snapshot


def test_driving_off_with_a_window_open_ends_the_situation(h):
    h.send(1, extra={WINDOW: "OPEN"})
    h.fix(5, HOME[0] + STEP, HOME[1] + STEP)  # a trip opens
    assert _situations(h) == [("parked_window_open", True), ("parked_window_open", False)]


def test_a_restored_open_window_starts_nothing(h):
    h.send(1, extra={WINDOW: "OPEN"})
    h.restart()
    h.send(5, soc=60)  # the car speaks -- but not about its windows
    h.advance_to(30)
    assert _situations(h) == [("parked_window_open", True)]  # only the pre-restart one


# -- alarm ---------------------------------------------------------------------------

ALARM = "vehicle.vehicle.antiTheftAlarmSystem.alarm.isOn"


def _alarms(h) -> list[bool]:
    return [data["active"] for _name, data in h.events("bavariandata_alarm")]


def test_the_alarm_fires_when_it_goes_off_and_when_it_stops(h, snapshot):
    h.send(1, extra={ALARM: False})
    h.send(5, extra={ALARM: True})
    h.send(6, extra={ALARM: True})  # still sounding: the same alarm
    h.send(9, extra={ALARM: False})

    assert _alarms(h) == [True, False]
    assert h.events("bavariandata_alarm")[0][1]["zone_entity_id"] == "zone.home"
    assert h.render() == snapshot


def test_an_alarm_the_stream_replays_from_the_past_is_not_one_now(h):
    h.send(1, extra={ALARM: False})
    h.send(60, stamped=10, extra={ALARM: True})  # stamped 50 minutes ago
    assert _alarms(h) == []


def test_a_rest_answer_never_sounds_the_alarm(h):
    """REST may answer from BMW's cache: it can say what was, not what happens."""

    h.send(1, rest=True, extra={ALARM: True})
    h.send(2, rest=True, extra={ALARM: False})
    assert _alarms(h) == []


def test_an_alarm_after_a_restart_with_nothing_before_still_fires(h):
    """Home Assistant restores no flag, so after a restart the alarm has no "before"."""

    h.restart()
    h.send(5, extra={ALARM: True})
    assert _alarms(h) == [True]


def test_an_alarm_already_on_before_a_restart_does_not_fire_again(h):
    h.send(1, extra={ALARM: False})
    h.send(2, extra={ALARM: True})
    h.restart()
    h.send(5, extra={ALARM: True})
    assert _alarms(h) == [True]


# -- preconditioning -----------------------------------------------------------------

PRECON = "vehicle.vehicle.preConditioning.activity"
PRECON_ERROR = "vehicle.vehicle.preConditioning.error"


def _preconditioning(h) -> list[bool]:
    return [data["active"] for _name, data in h.events("bavariandata_preconditioning")]


def test_preconditioning_starts_and_finishes(h, snapshot):
    h.send(1, extra={PRECON: "INACTIVE", PRECON_ERROR: "OK"})
    h.send(5, soc=70, extra={PRECON: "HEATING"})
    h.send(25, extra={PRECON: "INACTIVE"})

    assert _preconditioning(h) == [True, False]
    started, finished = (data for _name, data in h.events("bavariandata_preconditioning"))
    assert started["activity"] == "heating"
    assert started["soc"] == 70
    assert finished["error"] == "ok"
    assert h.render() == snapshot


def test_a_finish_with_no_start_we_saw_is_nothing(h):
    h.send(1, extra={PRECON: "INACTIVE"})
    assert _preconditioning(h) == []


def test_preconditioning_restored_running_does_not_start_again(h):
    h.send(1, extra={PRECON: "INACTIVE"})
    h.send(5, extra={PRECON: "HEATING"})
    h.restart()  # comes back as the slug "heating"
    h.send(10, extra={PRECON: "HEATING"})
    h.send(20, extra={PRECON: "INACTIVE"})
    assert _preconditioning(h) == [True, False]


def test_a_word_we_do_not_know_moves_nothing(h):
    h.send(1, extra={PRECON: "INACTIVE"})
    h.send(5, extra={PRECON: "SOMETHING_NEW"})
    h.send(6, extra={PRECON: "INACTIVE"})
    assert _preconditioning(h) == []


# -- Check Control ---------------------------------------------------------------------

CCM = "vehicle.status.checkControlMessages"
WASHER = {"messageType": "CCM", "id": 164, "status": "NULL", "text": "Washer fluid low"}
TYRE = {"messageType": "CCM", "id": 52, "status": "NULL", "text": "Tyre pressure low"}


def _messages(h) -> list[tuple[bool, object]]:
    return [(data["active"], data["id"]) for _name, data in h.events("bavariandata_check_control")]


def test_a_new_message_fires_and_its_resolution_too(h, snapshot):
    h.send(1, rest=True, extra={CCM: []})
    h.send(60, rest=True, extra={CCM: [WASHER]})
    h.send(120, rest=True, extra={CCM: [WASHER, TYRE]})
    h.send(180, rest=True, extra={CCM: None})  # BMW's null: no messages

    assert _messages(h) == [(True, 164), (True, 52), (False, 164), (False, 52)]
    first = h.events("bavariandata_check_control")[0][1]
    assert first["text"] == "Washer fluid low"
    assert first["message"] == WASHER
    assert h.render() == snapshot


def test_the_first_list_only_sets_the_starting_point(h):
    h.send(1, rest=True, extra={CCM: [WASHER]})
    assert _messages(h) == []


def test_a_restart_does_not_announce_the_messages_again(h):
    h.send(1, rest=True, extra={CCM: []})
    h.send(2, rest=True, extra={CCM: [WASHER]})
    h.restart()
    h.send(5, rest=True, extra={CCM: [WASHER]})  # the startup fetch
    assert _messages(h) == [(True, 164)]
