"""The Configure paths the screen walks don't take: refusals and partial results.

``test_flow_snapshots.py`` pins what each screen shows; these assert what the
rarer outcomes do -- a picture only some cars get, a container reset BMW
breaks off half way, a fetch with no car to ask about, a sign-in BMW refuses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.container import (
    KINDS,
    CardataContainerError,
    CardataContainerManager,
)
from custom_components.bavariandata.device_flow import CardataAuthError

from .conftest import PACKAGE, VIN, make_entry
from .test_flow_snapshots import VIN_B, _account, _configure, _submit, _Walk


async def _open(hass: HomeAssistant, entry, *path: str) -> dict:
    return await _configure(hass, entry, _Walk(), *path)


async def _send(hass: HomeAssistant, result: dict, user_input=None) -> dict:
    return await _submit(hass, _Walk(), result, user_input)


async def test_the_configurator_picture_is_saved_and_drawn_in_full(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    result = await _open(hass, entry, "action_fetch_image")
    with (
        patch(f"{PACKAGE}.image.async_refresh_configurator_render", AsyncMock(return_value=None)),
        patch(f"{PACKAGE}.image.async_schedule_full_render") as full,
    ):
        result = await _send(hass, result, {"image_source": "configurator"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["image_source"] == "configurator"
    assert [call.args[2] for call in full.call_args_list] == [VIN]


async def test_a_car_the_configurator_cannot_draw_keeps_its_official_picture(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN, VIN_B)
    result = await _open(hass, entry, "action_fetch_image")
    render = AsyncMock(side_effect=lambda _hass, _runtime, vin, full: vin == VIN_B and "unknown")
    with (
        patch(f"{PACKAGE}.image.async_refresh_configurator_render", render),
        patch(f"{PACKAGE}.image.async_schedule_full_render") as full,
        patch(f"{PACKAGE}.image.async_official_fallback", AsyncMock()) as fallback,
    ):
        result = await _send(hass, result, {"image_source": "configurator"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "configurator_partial"
    assert result["description_placeholders"] == {"vehicles": "iX1 xDrive30"}
    assert entry.options["image_source"] == "configurator"
    assert [call.args[3] for call in fallback.call_args_list] == [VIN_B]
    assert [call.args[2] for call in full.call_args_list] == [VIN]


async def test_a_picture_needs_a_car(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass)
    result = await _open(hass, entry, "action_fetch_image")
    result = await _send(hass, result, {"image_source": "official"})

    assert result["errors"] == {"base": "no_vins"}
    assert "image_source" not in entry.options


async def test_a_container_reset_broken_off_keeps_what_was_done(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    first, second = KINDS
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, first.id_key: "old-1", second.id_key: "old-2"}
    )
    result = await _open(hass, entry, "connection", "action_reset_container")
    with (
        patch.object(entry.runtime_data.tokens, "access_token", AsyncMock(return_value="a")),
        patch.object(
            CardataContainerManager,
            "async_reset_hv_container",
            AsyncMock(side_effect=[None, CardataContainerError("BMW said no")]),
        ),
    ):
        result = await _send(hass, result, {})

    assert result["errors"] == {"base": "reset_failed"}
    assert result["description_placeholders"] == {"error": "BMW said no"}
    # The first container is gone at BMW, so its id goes too; the second stays.
    assert first.id_key not in entry.data
    assert entry.data[second.id_key] == "old-2"


async def test_fetches_need_a_car(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass)
    for path in (("fetch_now", "action_fetch_basic"), ("fetch_now", "action_fetch_tyre")):
        result = await _open(hass, entry, *path)
        assert result["errors"] == {"base": "no_vins"}, path


async def test_fetches_need_a_running_account(hass: HomeAssistant, no_bmw) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    for action in ("action_fetch_mappings", "action_fetch_telematic"):
        result = await _open(hass, entry, "fetch_now", action)
        assert result["errors"] == {"base": "runtime_missing"}, action


async def test_a_fetch_bmw_answers_closes_the_dialog(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN, VIN_B)
    result = await _open(hass, entry, "fetch_now", "action_fetch_tyre")
    asked: list[str] = []

    async def answered(call: ServiceCall) -> None:
        asked.append(call.data["vin"])

    hass.services.async_register(DOMAIN, "fetch_tyre_diagnosis", answered)
    result = await _send(hass, result, {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert asked == sorted([VIN, VIN_B])


async def test_a_sign_in_bmw_refuses_says_why(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    result = await _open(hass, entry, "connection", "action_reauth")
    with patch(
        f"{PACKAGE}.config_flow.request_device_code",
        AsyncMock(side_effect=CardataAuthError("client unknown")),
    ):
        result = await _send(hass, result, {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_device_code_failed"
    assert result["description_placeholders"] == {"error": "client unknown"}
    assert entry.data["client_id"] == "client-1"
    assert not entry.runtime_data.reauth.in_progress
