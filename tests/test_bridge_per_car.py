"""The evcc bridge publishes only the cars switched on, and clears a car switched off.

A charge controller acts on what the bridge publishes, so the per-car switch
has two halves that both matter: a car switched off is never published, and
switching a car off removes everything the broker still holds for it -- also
what an earlier run retained, which this run never saw.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from .conftest import load_module
from .fake_ha import _stub_modules

A = "WBATEST0000000001"
B = "WBATEST0000000002"

EVCC = load_module("evcc")


class _Published(list):
    async def publish(self, hass, topic, payload, qos=0, retain=False) -> None:
        self.append((topic, payload, retain))


@pytest.fixture
def bridge_mod():
    """``bridge.py`` against the fake-HA stand-ins plus the three it adds."""

    sent = _Published()

    class HomeAssistantError(Exception):
        pass

    extra = {
        "homeassistant.components.mqtt": types.SimpleNamespace(async_publish=sent.publish),
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
        yield module, sent


def _bridge(bridge_mod, options):
    module, sent = bridge_mod
    mqtt_entry = types.SimpleNamespace(state="loaded")
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda domain: [mqtt_entry])
    )
    coordinator = types.SimpleNamespace(
        data={A: {}, B: {}},
        bridge_snapshot=lambda vin: EVCC.BridgeSnapshot(vin=vin, soc=80.0),
    )
    bridge = module.VehicleBridge(hass, coordinator, config=EVCC.BridgeConfig.from_options(options))
    return bridge, sent


def _vins(sent) -> set[str]:
    return {topic.split("/")[1] for topic, _payload, _retain in sent}


def test_a_car_switched_off_is_never_published(bridge_mod) -> None:
    bridge, sent = _bridge(
        bridge_mod, {"bridge_enabled": True, "vehicles": {B: {"bridge_enabled": False}}}
    )

    asyncio.run(bridge.async_publish_all(force=True))

    assert _vins(sent) == {A}


def test_only_the_car_switched_on_is_published(bridge_mod) -> None:
    bridge, sent = _bridge(
        bridge_mod, {"bridge_enabled": False, "vehicles": {B: {"bridge_enabled": True}}}
    )

    asyncio.run(bridge.async_publish_all(force=True))

    assert _vins(sent) == {B}


def test_switching_a_car_off_clears_every_topic_it_could_have_left(bridge_mod) -> None:
    """Including ones an earlier run retained: this run published nothing for B."""

    bridge, sent = _bridge(bridge_mod, {"bridge_enabled": True})

    asyncio.run(bridge.async_clear_vehicle(B))

    cleared = {topic for topic, payload, retain in sent if payload == "" and retain}
    assert cleared == {f"bavariandata/{B}/{suffix}" for suffix in EVCC.ALL_TOPICS}
