"""The bridge shares a car's stream, and clears it by asking the broker.

``tests/test_stream_share.py`` checks what goes where; this checks the bridge
does it: only for a car that shares, the message never retained, the fields by
the setting, and a sweep that deletes what the broker replays -- and nothing
else.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from .conftest import load_module
from .fake_ha import _stub_modules

A = "WBATEST0000000001"
B = "WBATEST0000000002"
SOC = "vehicle.drivetrain.batteryManagement.header"
LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"

EVCC = load_module("evcc")


class _Broker:
    """Records publishes, and replays ``retained`` to a new subscription."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []
        self.retained: dict[str, str] = {}
        self.unsubscribed = 0

    async def publish(self, hass, topic, payload, qos=0, retain=False) -> None:
        self.sent.append((topic, payload, retain))

    async def subscribe(self, hass, topic, msg_callback, qos=0):
        for name, payload in self.retained.items():
            msg_callback(types.SimpleNamespace(topic=name, payload=payload, retain=True))

        def _unsubscribe() -> None:
            self.unsubscribed += 1

        return _unsubscribe


@pytest.fixture
def bridge_mod():
    broker = _Broker()

    class HomeAssistantError(Exception):
        pass

    extra = {
        "homeassistant.components.mqtt": types.SimpleNamespace(
            async_publish=broker.publish, async_subscribe=broker.subscribe
        ),
        "homeassistant.config_entries": types.SimpleNamespace(
            ConfigEntryState=types.SimpleNamespace(LOADED="loaded")
        ),
        "homeassistant.exceptions": types.SimpleNamespace(HomeAssistantError=HomeAssistantError),
    }
    with pytest.MonkeyPatch.context() as patch:
        for name, module in {**_stub_modules(), **extra}.items():
            patch.setitem(__import__("sys").modules, name, module)
        patch.delitem(
            __import__("sys").modules, "custom_components.bavariandata.bridge", raising=False
        )
        module = load_module("bridge")
        patch.setattr(module, "SWEEP_S", 0)
        yield module, broker, HomeAssistantError


def _bridge(bridge_mod, options, *, broker_loaded: bool = True):
    module, broker, _error = bridge_mod
    entries = [types.SimpleNamespace(state="loaded")] if broker_loaded else []
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda domain: entries)
    )
    coordinator = types.SimpleNamespace(data={A: {}, B: {}})
    bridge = module.VehicleBridge(hass, coordinator, config=EVCC.BridgeConfig.from_options(options))
    return bridge, broker


def _message(vin: str = A, **data) -> dict:
    return {"vin": vin, "data": {name: {"value": value} for name, value in data.items()}}


def test_a_shared_car_puts_its_message_and_fields_on_the_broker(bridge_mod) -> None:
    bridge, broker = _bridge(bridge_mod, {"bridge_stream_enabled": True})

    asyncio.run(bridge.async_share(_message(**{SOC: 72})))

    assert broker.sent == [
        (f"bavariandata/{A}/stream", f'{{"vin":"{A}","data":{{"{SOC}":{{"value":72}}}}}}', False),
        (f"bavariandata/{A}/stream/{SOC}", '{"value":72}', True),
    ]


def test_the_fields_follow_the_retain_setting(bridge_mod) -> None:
    bridge, broker = _bridge(bridge_mod, {"bridge_stream_enabled": True, "bridge_retain": False})

    asyncio.run(bridge.async_share(_message(**{SOC: 72})))

    assert [retain for _topic, _payload, retain in broker.sent] == [False, False]


def test_a_car_that_does_not_share_puts_nothing_out(bridge_mod) -> None:
    bridge, broker = _bridge(
        bridge_mod,
        {"bridge_enabled": True, "vehicles": {B: {"bridge_stream_enabled": True}}},
    )

    asyncio.run(bridge.async_share(_message(A, **{SOC: 72})))
    asyncio.run(bridge.async_share(_message(B, **{SOC: 50})))

    assert {topic.split("/")[1] for topic, _payload, _retain in broker.sent} == {B}


def test_the_position_is_left_out_by_default(bridge_mod) -> None:
    bridge, broker = _bridge(bridge_mod, {"bridge_stream_enabled": True})

    asyncio.run(bridge.async_share(_message(**{SOC: 72, LAT: 48.1})))

    assert not any(LAT in topic or LAT in payload for topic, payload, _retain in broker.sent)


def test_without_a_broker_it_shares_nothing(bridge_mod) -> None:
    bridge, broker = _bridge(bridge_mod, {"bridge_stream_enabled": True}, broker_loaded=False)

    asyncio.run(bridge.async_share(_message(**{SOC: 72})))

    assert broker.sent == []


def test_after_shutdown_it_shares_nothing(bridge_mod) -> None:
    bridge, broker = _bridge(bridge_mod, {"bridge_stream_enabled": True})
    bridge.async_shutdown()

    asyncio.run(bridge.async_share(_message(**{SOC: 72})))

    assert broker.sent == []
    assert not bridge.sharing


def test_a_sweep_deletes_what_the_broker_still_holds(bridge_mod) -> None:
    module, broker, _error = bridge_mod
    broker.retained = {
        f"bavariandata/{A}/stream/{SOC}": '{"value":72}',
        f"bavariandata/{B}/stream/{SOC}": '{"value":50}',
        f"bavariandata/{A}/soc": "72",
    }

    asyncio.run(module.async_clear_shared(None, prefix="bavariandata", vins=[A]))

    assert broker.sent == [(f"bavariandata/{A}/stream/{SOC}", "", True)]
    assert broker.unsubscribed == 1


def test_a_position_sweep_deletes_only_the_position(bridge_mod) -> None:
    module, broker, _error = bridge_mod
    broker.retained = {
        f"bavariandata/{A}/stream/{SOC}": '{"value":72}',
        f"bavariandata/{A}/stream/{LAT}": '{"value":48.1}',
    }

    asyncio.run(module.async_clear_shared(None, prefix="bavariandata", position_only=True))

    assert broker.sent == [(f"bavariandata/{A}/stream/{LAT}", "", True)]


def test_a_sweep_without_mqtt_gives_up_quietly(bridge_mod) -> None:
    module, broker, error = bridge_mod

    async def _refuse(*_args, **_kwargs):
        raise error("MQTT is not set up")

    broker.subscribe = _refuse
    module.mqtt.async_subscribe = _refuse

    asyncio.run(module.async_clear_shared(None, prefix="bavariandata"))

    assert broker.sent == []


def test_a_broker_that_fails_a_clear_does_not_stop_the_sweep(bridge_mod) -> None:
    module, broker, error = bridge_mod
    broker.retained = {
        f"bavariandata/{A}/stream/a": "1",
        f"bavariandata/{A}/stream/b": "2",
    }
    calls: list[str] = []

    async def _flaky(hass, topic, payload, qos=0, retain=False):
        calls.append(topic)
        if topic.endswith("/a"):
            raise error("broker went away")
        if topic.endswith("/b"):
            raise RuntimeError("something else")

    module.mqtt.async_publish = _flaky

    asyncio.run(module.async_clear_shared(None, prefix="bavariandata"))

    assert calls == [f"bavariandata/{A}/stream/a", f"bavariandata/{A}/stream/b"]
