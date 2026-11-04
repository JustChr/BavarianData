"""The ABRP push and its Configure screen in a real Home Assistant.

``tests/test_abrp.py`` checks the point and the settings; this checks when
points go out (the debounce, the heartbeat, a refused token) against a fed
coordinator with the HTTP send patched, and what the screen saves.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bavariandata.abrp import AbrpConfig, SendResult
from custom_components.bavariandata.abrp_push import HEARTBEAT_S, SEND_DEBOUNCE_S, AbrpPush
from custom_components.bavariandata.debug import set_debug_enabled

from .conftest import PACKAGE, VIN
from .test_flow_snapshots import VIN_B, _account, _configure, _submit, _Walk

SEND = f"{PACKAGE}.abrp_push.async_send"
CHECK = f"{PACKAGE}.options_cars.async_check"
KEY = f"{PACKAGE}.abrp.PROJECT_API_KEY"

ON = {"abrp_enabled": True, "abrp_token": "tok", "abrp_position": False}


def _options(car=ON, **shared):
    return {"vehicles": {VIN: dict(car)}, **shared}


async def _push(hass: HomeAssistant, options=None) -> AbrpPush:
    entry = await _account(hass, VIN)
    config = AbrpConfig.from_options(options if options is not None else _options())
    return AbrpPush(hass, entry.runtime_data.coordinator, config=config)


# --------------------------------------------------------------------------
# The push
# --------------------------------------------------------------------------


async def test_a_switched_on_car_is_sent(hass: HomeAssistant, no_bmw, caplog) -> None:
    with patch(KEY, "project-key"):
        push = await _push(hass)
    set_debug_enabled(True, entry_id="abrp")
    try:
        with (
            patch(SEND, new_callable=AsyncMock, return_value=SendResult.OK) as send,
            caplog.at_level(logging.DEBUG, logger="custom_components.bavariandata"),
        ):
            await push.async_send_all()
    finally:
        set_debug_enabled(False, entry_id="abrp")
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert (kwargs["key"], kwargs["token"]) == ("project-key", "tok")
    assert kwargs["point"]["soc"] == 63.0
    assert "lat" not in kwargs["point"]
    assert "[abrp]" in caplog.text


async def test_nothing_without_a_key_or_a_switch(hass: HomeAssistant, no_bmw) -> None:
    with patch(KEY, ""):
        push = await _push(hass)
    with patch(SEND, new_callable=AsyncMock) as send:
        await push.async_send_all()
        push.async_schedule()
        push.apply_config(
            AbrpConfig.from_options(_options(dict(ON, abrp_enabled=False), abrp_api_key="k"))
        )
        await push.async_send(VIN)
    send.assert_not_awaited()


async def test_no_charge_level_sends_nothing(hass: HomeAssistant, no_bmw) -> None:
    with patch(KEY, "project-key"):
        push = await _push(hass)
    with (
        patch(SEND, new_callable=AsyncMock) as send,
        patch(f"{PACKAGE}.abrp_push.telemetry", return_value=None),
    ):
        await push.async_send(VIN)
    send.assert_not_awaited()


async def test_a_refused_token_is_logged_once(hass: HomeAssistant, no_bmw, caplog) -> None:
    with patch(KEY, "project-key"):
        push = await _push(hass)
    with patch(SEND, new_callable=AsyncMock, return_value=SendResult.REJECTED):
        await push.async_send(VIN)
        await push.async_send(VIN)
    assert caplog.text.count("ABRP refused") == 1
    with patch(SEND, new_callable=AsyncMock, return_value=SendResult.OK):
        await push.async_send(VIN)
    with patch(SEND, new_callable=AsyncMock, return_value=SendResult.REJECTED):
        await push.async_send(VIN)
    assert caplog.text.count("ABRP refused") == 2, "said again once it had worked in between"


async def test_a_burst_becomes_one_send(hass: HomeAssistant, no_bmw) -> None:
    with patch(KEY, "project-key"):
        push = await _push(hass)
    with patch(SEND, new_callable=AsyncMock, return_value=SendResult.OK) as send:
        push.async_schedule(VIN)
        push.async_schedule(VIN)
        push.async_schedule()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SEND_DEBOUNCE_S + 1))
        await hass.async_block_till_done()
    assert send.await_count == 1


async def test_shutdown_cancels_what_was_queued(hass: HomeAssistant, no_bmw) -> None:
    with patch(KEY, "project-key"):
        push = await _push(hass)
    with patch(SEND, new_callable=AsyncMock) as send:
        push.async_schedule(VIN)
        push.async_shutdown()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SEND_DEBOUNCE_S + 1))
        await hass.async_block_till_done()
        await push.async_send_all()
    send.assert_not_awaited()
    assert not push.enabled


async def test_setup_wires_the_signals_and_the_heartbeat(hass: HomeAssistant, no_bmw) -> None:
    with patch(KEY, "project-key"):
        entry = await _account(hass, VIN)
    runtime = entry.runtime_data
    push = runtime.abrp
    assert push is not None and not push.enabled
    with patch(KEY, "project-key"):
        push.apply_config(AbrpConfig.from_options(_options()))
    with patch(SEND, new_callable=AsyncMock, return_value=SendResult.OK) as send:
        async_dispatcher_send(hass, runtime.coordinator.signal_soc_estimate, VIN)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SEND_DEBOUNCE_S + 1))
        await hass.async_block_till_done()
        assert send.await_count == 1, "a change is sent"
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=HEARTBEAT_S * 2 + 5))
        await hass.async_block_till_done()
        assert send.await_count >= 2, "and repeated on the heartbeat"


# --------------------------------------------------------------------------
# The screen
# --------------------------------------------------------------------------


async def _abrp(hass, entry, user_input, *, vin=None):
    result = await _configure(hass, entry, _Walk(), "share_live_data", "action_abrp")
    if vin is not None:
        result = await _submit(hass, _Walk(), result, {"vehicle": vin})
    return await _submit(hass, _Walk(), result, user_input)


async def test_saving_sends_at_once_and_keeps_the_token_with_the_car(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    with (
        patch(KEY, "project-key"),
        patch(CHECK, new_callable=AsyncMock, return_value=SendResult.OK) as check,
        patch(f"{PACKAGE}.abrp_push.AbrpPush.async_schedule") as schedule,
    ):
        result = await _abrp(hass, entry, {**ON, "abrp_token": " tok ", "abrp_api_key": " "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert check.await_args.kwargs["token"] == "tok"
    assert entry.options["vehicles"][VIN] == ON
    assert "abrp_api_key" not in entry.options
    schedule.assert_called_once_with(VIN)
    assert entry.runtime_data.abrp.config.sends(VIN)


async def test_switching_off_needs_no_check(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    with patch(CHECK, new_callable=AsyncMock) as check:
        result = await _abrp(hass, entry, {"abrp_enabled": False, "abrp_position": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    check.assert_not_awaited()
    assert entry.options["vehicles"][VIN]["abrp_enabled"] is False


async def test_a_copy_without_a_key_asks_for_one(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    with patch(KEY, ""):
        result = await _abrp(hass, entry, ON)
    assert result["errors"] == {"abrp_api_key": "abrp_key_missing"}


async def test_abrp_unreachable_is_said_on_the_screen(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    with (
        patch(KEY, "project-key"),
        patch(CHECK, new_callable=AsyncMock, return_value=SendResult.UNREACHABLE),
    ):
        result = await _abrp(hass, entry, ON)
    assert result["errors"] == {"base": "abrp_unreachable"}


async def test_two_cars_each_their_own_token_and_one_key(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN, VIN_B)
    with (
        patch(KEY, ""),
        patch(CHECK, new_callable=AsyncMock, return_value=SendResult.OK) as check,
    ):
        result = await _abrp(
            hass,
            entry,
            {**ON, "abrp_token": "b", "all_vehicles": {"abrp_api_key": "mine"}},
            vin=VIN_B,
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert check.await_args.kwargs["key"] == "mine"
    assert entry.options["abrp_api_key"] == "mine"
    assert entry.options["vehicles"][VIN_B]["abrp_token"] == "b"
    assert VIN not in entry.options.get("vehicles", {})
