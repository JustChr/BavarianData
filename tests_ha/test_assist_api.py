"""The LLM API as a conversation agent sees it.

What an answer may hold is ``assist.py``'s, tested in ``tests/test_assist.py``;
this calls every tool through Home Assistant's ``llm`` helper, on a fed
account, so the inputs the adapter gathers are the real ones.
"""

from __future__ import annotations

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.bavariandata.assist_api import API_ID

from .conftest import VIN
from .test_services import _account

LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
OTHER = "WBATEST0000000002"
CONTEXT = llm.LLMContext(
    platform="test", context=Context(), language="en", assistant="conversation", device_id=None
)


async def _call(hass: HomeAssistant, name: str, **args):
    """What ``APIInstance.async_call_tool`` does, without the conversation
    stack it imports (``hassil``, not in this venv)."""

    api = await llm.async_get_api(hass, API_ID, CONTEXT)
    tool = next(tool for tool in api.tools if tool.name == name)
    tool_input = llm.ToolInput(tool_name=name, tool_args=tool.parameters(args))
    return await tool.async_call(hass, tool_input, CONTEXT)


async def test_without_a_car_there_are_no_tools(hass: HomeAssistant, no_bmw) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.bavariandata.const import DOMAIN

    assert await async_setup_component(hass, DOMAIN, {})
    api = await llm.async_get_api(hass, API_ID, CONTEXT)

    assert api.tools == []
    assert "none set up yet" in api.api_prompt


async def test_the_status_names_the_car_and_never_its_vin(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    stamp = dt_util.utcnow().isoformat()
    await runtime.coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                LAT: {"value": 48.0, "unit": None, "timestamp": stamp},
                LON: {"value": 11.0, "unit": None, "timestamp": stamp},
            },
        }
    )
    await hass.async_block_till_done()

    answer = await _call(hass, "get_car_status")

    assert answer["car"]
    assert VIN not in str(answer)
    assert "48.0" not in str(answer)


async def test_every_history_tool_answers(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)

    for tool, args in (
        ("check_range", {"distance_km": 30, "round_trip": True}),
        ("get_charging_history", {"days": 7}),
        ("get_driving_month", {"month": dt_util.now().strftime("%Y-%m")}),
        ("get_driving_month", {}),
        ("get_mileage", {}),
        ("get_efficiency", {}),
    ):
        answer = await _call(hass, tool, **args)
        assert answer["car"], tool
        assert VIN not in str(answer), tool


async def test_two_cars_of_one_name_are_told_apart(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    coordinator = runtime.coordinator
    stamp = dt_util.utcnow().isoformat()
    await coordinator.async_handle_message(
        {
            "vin": OTHER,
            "data": {
                "vehicle.drivetrain.batteryManagement.header": {
                    "value": 40,
                    "unit": "%",
                    "timestamp": stamp,
                }
            },
        }
    )
    await hass.async_block_till_done()
    coordinator.names[VIN] = coordinator.names[OTHER] = "i5"

    both = await _call(hass, "get_car_status")
    assert [car["car"] for car in both["cars"]] == ["i5", "i5 (2)"]

    one = await _call(hass, "get_car_status", car="i5 (2)")
    assert one["car"] == "i5 (2)"


async def test_an_unknown_car_is_refused_by_name(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    api = await llm.async_get_api(hass, API_ID, CONTEXT)
    tool = next(tool for tool in api.tools if tool.name == "get_car_status")

    with pytest.raises(HomeAssistantError):
        await tool.async_call(hass, llm.ToolInput("get_car_status", {"car": "Tesla"}), CONTEXT)
