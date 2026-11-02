"""What a shared stream puts on the broker, and what switching it off takes away.

The position stays home unless the user says otherwise, BMW's message keeps
its shape, and a clean-up only ever touches the shared stream's own topics --
never the evcc topics beside them, never another car's.
"""

from __future__ import annotations

import json

from .conftest import load_module

SHARE = load_module("stream_share")
EVCC = load_module("evcc")

A = "WBATEST0000000001"
B = "WBATEST0000000002"
SOC = "vehicle.drivetrain.batteryManagement.header"
LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
DEST = "vehicle.cabin.infotainment.navigation.destinationSet.latitude"


def _message(**data) -> dict:
    return {
        "vin": A,
        "entityId": "x",
        "timestamp": "2026-11-02T10:00:00Z",
        "data": {name: {"value": value, "unit": "%"} for name, value in data.items()},
    }


def _payload(**data) -> dict:
    payload = _message()
    payload["data"] = data
    return payload


def test_the_message_goes_out_as_bmw_sent_it() -> None:
    payload = _payload(**{SOC: {"value": 72, "unit": "%", "timestamp": "t"}})

    shared = SHARE.shared_message("bavariandata", payload, hide_position=True)

    topic, message = shared.message
    assert topic == f"bavariandata/{A}/stream"
    assert json.loads(message) == payload


def test_each_field_gets_its_own_topic_in_bmws_shape() -> None:
    value = {"value": 72, "unit": "%", "timestamp": "t"}

    shared = SHARE.shared_message("bavariandata", _payload(**{SOC: value}), hide_position=True)

    assert {topic: json.loads(body) for topic, body in shared.fields.items()} == {
        f"bavariandata/{A}/stream/{SOC}": value
    }


def test_the_position_stays_out_unless_asked_for() -> None:
    payload = _payload(**{SOC: {"value": 72}, LAT: {"value": 48.1}, DEST: {"value": 48.2}})

    hidden = SHARE.shared_message("bavariandata", payload, hide_position=True)
    shown = SHARE.shared_message("bavariandata", payload, hide_position=False)

    assert set(json.loads(hidden.message[1])["data"]) == {SOC}
    assert all(LAT not in topic and DEST not in topic for topic in hidden.fields)
    assert set(json.loads(shown.message[1])["data"]) == {SOC, LAT, DEST}
    assert len(shown.fields) == 3


def test_a_message_with_only_the_position_publishes_nothing() -> None:
    shared = SHARE.shared_message(
        "bavariandata", _payload(**{LAT: {"value": 48.1}}), hide_position=True
    )

    assert shared.message is None
    assert shared.fields == {}


def test_a_new_position_field_is_kept_out_too() -> None:
    assert SHARE.is_position("vehicle.cabin.infotainment.navigation.currentLocation.somethingNew")
    assert not SHARE.is_position(SOC)


def test_malformed_messages_publish_nothing() -> None:
    for payload in ({}, {"vin": A}, {"vin": A, "data": []}, {"vin": "", "data": {SOC: {}}}):
        assert SHARE.shared_message("p", payload, hide_position=True).message is None


def test_a_field_that_would_not_make_a_topic_stays_in_the_message_only() -> None:
    payload = _payload(**{SOC: {"value": 1}, "odd/name": {"value": 2}, "a+b": {"value": 3}})

    shared = SHARE.shared_message("p", payload, hide_position=True)

    assert list(shared.fields) == [f"p/{A}/stream/{SOC}"]
    assert set(json.loads(shared.message[1])["data"]) == {SOC, "odd/name", "a+b"}


def test_the_prefix_is_normalized_like_the_evcc_topics() -> None:
    assert SHARE.stream_topic("/home/bmw/", A) == f"home/bmw/{A}/stream"
    assert SHARE.sweep_filter("#") == "bavariandata/+/stream/#"


def test_a_sweep_clears_only_shared_stream_fields() -> None:
    prefix = "home/bmw"

    assert SHARE.clears(f"{prefix}/{A}/stream/{SOC}", prefix)
    assert not SHARE.clears(f"{prefix}/{A}/soc", prefix)
    assert not SHARE.clears(f"{prefix}/{A}/stream", prefix)
    assert not SHARE.clears(f"other/{A}/stream/{SOC}", prefix)


def test_a_sweep_for_one_car_leaves_the_other_alone() -> None:
    assert SHARE.clears(f"p/{A}/stream/{SOC}", "p", vins=[A])
    assert not SHARE.clears(f"p/{B}/stream/{SOC}", "p", vins=[A])


def test_a_position_sweep_leaves_the_rest() -> None:
    assert SHARE.clears(f"p/{A}/stream/{LAT}", "p", position_only=True)
    assert not SHARE.clears(f"p/{A}/stream/{SOC}", "p", position_only=True)


def _config(options: dict):
    return EVCC.BridgeConfig.from_options(options)


def test_sharing_is_per_car_and_independent_of_evcc() -> None:
    config = _config(
        {
            "bridge_enabled": True,
            "vehicles": {
                B: {"bridge_enabled": False, "bridge_stream_enabled": True},
            },
        }
    )

    assert config.publishes(A) and not config.shares(A)
    assert config.shares(B) and not config.publishes(B)
    assert config.any_sharing
    assert config.hide_position


def test_a_car_saved_before_sharing_existed_does_not_share() -> None:
    config = _config({"bridge_stream_enabled": True, "vehicles": {B: {"bridge_enabled": True}}})

    assert config.shares(A)
    assert not config.shares(B)


def test_nothing_to_sweep_when_nothing_was_shared() -> None:
    assert SHARE.sweeps(_config({}), _config({"bridge_stream_enabled": True}), [A]) == []


def test_switching_sharing_off_sweeps_everything() -> None:
    before = _config({"bridge_stream_enabled": True, "bridge_topic_prefix": "x"})

    assert SHARE.sweeps(before, _config({"bridge_topic_prefix": "x"}), [A]) == [SHARE.Sweep("x")]


def test_a_new_prefix_sweeps_the_old_one() -> None:
    before = _config({"bridge_stream_enabled": True, "bridge_topic_prefix": "old"})
    after = _config({"bridge_stream_enabled": True, "bridge_topic_prefix": "new"})

    assert SHARE.sweeps(before, after, [A]) == [SHARE.Sweep("old")]


def test_one_car_switched_off_sweeps_that_car() -> None:
    before = _config(
        {"vehicles": {A: {"bridge_stream_enabled": True}, B: {"bridge_stream_enabled": True}}}
    )
    after = _config(
        {"vehicles": {A: {"bridge_stream_enabled": True}, B: {"bridge_stream_enabled": False}}}
    )

    assert SHARE.sweeps(before, after, [A]) == [SHARE.Sweep("bavariandata", vins=(B,))]


def test_hiding_the_position_again_sweeps_the_position() -> None:
    before = _config({"bridge_stream_enabled": True, "bridge_stream_hide_position": False})
    after = _config({"bridge_stream_enabled": True, "bridge_stream_hide_position": True})

    assert SHARE.sweeps(before, after, [A]) == [SHARE.Sweep("bavariandata", position_only=True)]
