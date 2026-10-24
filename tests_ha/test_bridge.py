"""The evcc bridge's lifecycle in a real Home Assistant.

``tests/test_bridge_per_car.py`` checks which cars are published; this checks
the rest against a fed coordinator, with MQTT's publish patched: the missing
broker, the debounce, a prefix change, the clears, and a broker that fails.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.bavariandata.bridge import (
    PUBLISH_DEBOUNCE_S,
    VehicleBridge,
    async_clear_published,
)
from custom_components.bavariandata.debug import set_debug_enabled
from custom_components.bavariandata.evcc import ALL_TOPICS, BridgeConfig

from .conftest import VIN
from .test_services import _account

ON = {"bridge_enabled": True}


@pytest.fixture
def published():
    with patch(
        "custom_components.bavariandata.bridge.mqtt.async_publish", new_callable=AsyncMock
    ) as mock:
        yield mock


def _topics(published) -> list[tuple[str, str]]:
    return [(call.args[1], call.args[2]) for call in published.await_args_list]


async def _bridge(hass: HomeAssistant, options=ON, *, broker: bool = True) -> VehicleBridge:
    if broker:
        MockConfigEntry(domain="mqtt", state=ConfigEntryState.LOADED).add_to_hass(hass)
    _entry, runtime, _trip = await _account(hass)
    return VehicleBridge(hass, runtime.coordinator, config=BridgeConfig.from_options(options))


async def test_without_a_broker_it_says_so_once(
    hass: HomeAssistant, no_bmw, published, caplog
) -> None:
    bridge = await _bridge(hass, broker=False)

    await bridge.async_publish(VIN)
    await bridge.async_publish(VIN)

    assert caplog.text.count("no MQTT integration") == 1
    published.assert_not_awaited()


async def test_only_what_changed_goes_out(hass: HomeAssistant, no_bmw, published, caplog) -> None:
    bridge = await _bridge(hass)
    set_debug_enabled(True, entry_id="bridge")
    try:
        with caplog.at_level(logging.DEBUG, logger="custom_components.bavariandata"):
            await bridge.async_publish_all()
    finally:
        set_debug_enabled(False, entry_id="bridge")
    first = _topics(published)
    assert (f"bavariandata/{VIN}/soc", "63") in first
    assert "[bridge]" in caplog.text

    published.reset_mock()
    await bridge.async_publish(VIN)
    published.assert_not_awaited()

    await bridge.async_publish(VIN, force=True)
    assert _topics(published) == first


async def test_a_new_prefix_clears_the_old_tree(hass: HomeAssistant, no_bmw, published) -> None:
    bridge = await _bridge(hass)
    await bridge.async_publish(VIN)
    published.reset_mock()

    bridge.apply_config(BridgeConfig.from_options({**ON, "bridge_topic_prefix": "evcc"}))
    await bridge.async_publish(VIN)

    sent = _topics(published)
    assert (f"bavariandata/{VIN}/soc", "") in sent
    assert (f"evcc/{VIN}/soc", "63") in sent


async def test_a_burst_is_published_once_after_the_debounce(
    hass: HomeAssistant, no_bmw, published
) -> None:
    bridge = await _bridge(hass)

    bridge.async_schedule()
    bridge.async_schedule(VIN)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=PUBLISH_DEBOUNCE_S + 1))
    await hass.async_block_till_done()
    assert (f"bavariandata/{VIN}/soc", "63") in _topics(published)

    published.reset_mock()
    bridge.async_schedule(VIN)
    bridge.async_shutdown()
    bridge.async_schedule(VIN)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2 * PUBLISH_DEBOUNCE_S + 2))
    await hass.async_block_till_done()
    published.assert_not_awaited()
    await bridge.async_publish_all()
    published.assert_not_awaited()


async def test_switching_off_clears_everything_it_published(
    hass: HomeAssistant, no_bmw, published
) -> None:
    bridge = await _bridge(hass)
    await bridge.async_publish(VIN)
    published.reset_mock()

    await bridge.async_clear_all()

    assert {topic for topic, payload in _topics(published) if payload == ""} == {
        f"bavariandata/{VIN}/{suffix}" for suffix in ALL_TOPICS
    }


async def test_removing_the_integration_clears_without_a_bridge(
    hass: HomeAssistant, published
) -> None:
    await async_clear_published(hass, prefix="bavariandata/", vins=["", VIN])

    assert {topic for topic, _payload in _topics(published)} == {
        f"bavariandata/{VIN}/{suffix}" for suffix in ALL_TOPICS
    }


@pytest.mark.parametrize("error", [HomeAssistantError("broker gone"), RuntimeError("odd")])
async def test_a_failing_broker_never_breaks_anything(
    hass: HomeAssistant, no_bmw, published, error
) -> None:
    bridge = await _bridge(hass)
    published.side_effect = error

    await bridge.async_publish(VIN)
    await bridge.async_clear_vehicle(VIN)
    await async_clear_published(hass, prefix="bavariandata", vins=[VIN])
