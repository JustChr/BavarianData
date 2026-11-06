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
from .test_flow_snapshots import UPHOLSTERY, VIN_B, _account, _configure, _submit, _Walk


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


async def test_the_official_picture_is_saved_once_bmw_sends_it(hass: HomeAssistant, no_bmw) -> None:
    no_bmw["rest_bytes"].side_effect = None
    no_bmw["rest_bytes"].return_value = (b"\x89PNG-official", "image/png")
    entry = await _account(hass, VIN)
    hass.config_entries.async_update_entry(entry, options={"image_source": "configurator"})
    result = await _open(hass, entry, "action_fetch_image")
    with patch(f"{PACKAGE}.options_flow.async_upholstery") as upholstery:
        result = await _send(hass, result, {"image_source": "official"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["image_source"] == "official"
    # No upholstery to ask: the official picture is BMW's own photo.
    upholstery.assert_not_called()


async def test_a_car_the_configurator_cannot_draw_keeps_its_official_picture(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN, VIN_B)
    result = await _open(hass, entry, "action_fetch_image")
    render = AsyncMock(side_effect=lambda _hass, _runtime, vin, **_kw: vin == VIN_B and "unknown")
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


def _offers(*models: str):
    """BMW's configurator listing ``UPHOLSTERY`` for the cars of ``models`` only."""

    async def offer(_session, raw, _by_site, _languages):
        return UPHOLSTERY if raw.get("modelName") in models else []

    return patch(f"{PACKAGE}.options_flow.async_upholstery", side_effect=offer)


def _default(result: dict, key: str):
    """The value a form shows in ``key`` before anything is touched."""

    return next(marker.default() for marker in result["data_schema"].schema if marker == key)


async def test_the_picked_upholstery_is_drawn_and_kept_for_the_car(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    result = await _open(hass, entry, "action_fetch_image")
    render = AsyncMock(return_value=None)
    with (
        _offers("i5 eDrive40"),
        patch(f"{PACKAGE}.image.async_refresh_configurator_render", render),
        patch(f"{PACKAGE}.image.async_schedule_full_render") as full,
    ):
        result = await _send(hass, result, {"image_source": "configurator"})
        assert (result["step_id"], result["last_step"]) == ("image_upholstery", True)
        assert result["description_placeholders"] == {"vehicle": "i5 eDrive40"}
        assert _default(result, "configurator_upholstery") == "default"
        result = await _send(hass, result, {"configurator_upholstery": "FMAMU"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["image_source"] == "configurator"
    assert entry.options["vehicles"] == {VIN: {"configurator_upholstery": "FMAMU"}}
    # Drawn in it before it was saved, and drawn in full once saved.
    assert render.call_args.kwargs["options"]["vehicles"] == entry.options["vehicles"]
    assert [call.args[2] for call in full.call_args_list] == [VIN]


async def test_only_the_cars_bmw_lists_upholstery_for_are_asked(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN, VIN_B)
    result = await _open(hass, entry, "action_fetch_image")
    with (
        _offers("iX1 xDrive30"),
        patch(f"{PACKAGE}.image.async_refresh_configurator_render", AsyncMock(return_value=None)),
        patch(f"{PACKAGE}.image.async_schedule_full_render"),
    ):
        result = await _send(hass, result, {"image_source": "configurator"})
        assert result["description_placeholders"] == {"vehicle": "iX1 xDrive30"}
        assert result["last_step"] is True
        result = await _send(hass, result, {"configurator_upholstery": "FKHKC"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["vehicles"] == {VIN_B: {"configurator_upholstery": "FKHKC"}}


async def test_the_upholstery_screen_shows_the_one_in_use(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN, VIN_B)
    own = {"bridge_enabled": True}
    hass.config_entries.async_update_entry(
        entry,
        options={
            "vehicles": {
                VIN: {**own, "configurator_upholstery": "FKHKC"},
                VIN_B: {"configurator_upholstery": "FGONE"},  # BMW no longer lists it
            }
        },
    )
    result = await _open(hass, entry, "action_fetch_image")
    with (
        _offers("i5 eDrive40", "iX1 xDrive30"),
        patch(f"{PACKAGE}.image.async_refresh_configurator_render", AsyncMock(return_value=None)),
        patch(f"{PACKAGE}.image.async_schedule_full_render"),
    ):
        result = await _send(hass, result, {"image_source": "configurator"})
        assert _default(result, "configurator_upholstery") == "FKHKC"
        result = await _send(hass, result, {"configurator_upholstery": "default"})
        # A code BMW stopped listing would be drawn as BMW's default, so says so.
        assert _default(result, "configurator_upholstery") == "default"
        result = await _send(hass, result, {"configurator_upholstery": "default"})

    # BMW's default leaves no upholstery behind; the car's other settings stay.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["vehicles"] == {VIN: own}


async def test_an_account_unloaded_before_the_drawing_says_so(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    result = await _open(hass, entry, "action_fetch_image")
    with _offers("i5 eDrive40"):
        result = await _send(hass, result, {"image_source": "configurator"})
    await hass.config_entries.async_unload(entry.entry_id)
    result = await _send(hass, result, {"configurator_upholstery": "FMAMU"})

    assert (result["step_id"], result["errors"]) == (
        "action_fetch_image",
        {"base": "runtime_missing"},
    )
    assert "image_source" not in entry.options


async def test_cars_gone_before_the_drawing_need_a_car_again(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass, VIN)
    result = await _open(hass, entry, "action_fetch_image")
    with _offers("i5 eDrive40"):
        result = await _send(hass, result, {"image_source": "configurator"})
    entry.runtime_data.coordinator.data.clear()
    result = await _send(hass, result, {"configurator_upholstery": "FMAMU"})

    assert (result["step_id"], result["errors"]) == ("action_fetch_image", {"base": "no_vins"})
    assert "image_source" not in entry.options


async def test_a_picture_needs_a_car(hass: HomeAssistant, no_bmw) -> None:
    entry = await _account(hass)
    result = await _open(hass, entry, "action_fetch_image")
    result = await _send(hass, result, {"image_source": "official"})

    assert result["errors"] == {"base": "no_vins"}
    assert "image_source" not in entry.options


async def test_a_refused_container_reset_keeps_the_old_container(
    hass: HomeAssistant, no_bmw
) -> None:
    entry = await _account(hass, VIN)
    (full,) = KINDS
    hass.config_entries.async_update_entry(entry, data={**entry.data, full.id_key: "old"})
    result = await _open(hass, entry, "connection", "action_reset_container")
    with (
        patch.object(entry.runtime_data.tokens, "access_token", AsyncMock(return_value="a")),
        patch.object(
            CardataContainerManager,
            "async_reset_hv_container",
            AsyncMock(side_effect=CardataContainerError("BMW said no")),
        ),
    ):
        result = await _send(hass, result, {})

    assert result["errors"] == {"base": "reset_failed"}
    assert result["description_placeholders"] == {"error": "BMW said no"}
    assert entry.data[full.id_key] == "old"


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
