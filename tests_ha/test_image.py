"""The car's picture in a real Home Assistant: BMW's official image and the configurator's.

The official picture costs a quota slot, so it is fetched once and kept across
restarts; the configurator's turntable lives in files under ``.storage`` and is
served to the card through a view behind HA's login.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from custom_components.bavariandata import image
from custom_components.bavariandata.api import CardataApiError
from custom_components.bavariandata.configurator import OPTION_COLUMNS, ConfiguratorError, Render
from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.device_flow import CardataAuthError
from custom_components.bavariandata.rest import CardataQuotaError

from .conftest import PACKAGE, VIN
from .conftest import make_entry as _entry
from .conftest import set_up as _set_up

PNG = bytes((0x89, 0x50, 0x4E, 0x47)) + b"-official"
WEBP = b"RIFF-spin"
ODOMETER = "vehicle.vehicle.travelledDistance"
IMAGES = f"{DOMAIN}_vehicle_images"
RENDERS = f"{DOMAIN}_configurator_renders"


@pytest.fixture
def renders_dir(tmp_path):
    """The configurator's files in the test's own folder, not the shared config dir."""

    with patch(f"{PACKAGE}.image._render_dir", return_value=tmp_path):
        yield tmp_path


def _render(**extra) -> Render:
    return Render(
        sprite=WEBP,
        sprite_type="image/webp",
        columns=6,
        rows=6,
        frame_width=640,
        frame_height=360,
        frames=36,
        still=b"still",
        paint="#123456",
        brand="bmw",
        **extra,
    )


FULL = {
    "lights": WEBP + b"-lights",
    "night": WEBP + b"-night",
    "interior": PNG + b"-interior",
    "top": b"other-top",
    "options": PNG + b"-options",
    # One tile more than a row: the sheet's row count rounds up.
    "option_codes": tuple(f"S{n}" for n in range(OPTION_COLUMNS + 1)),
    "equipment": ({"code": "S0", "name": "Seats"},),
    "equipment_language": "en",
}


async def _car(hass: HomeAssistant, entry, vin: str = VIN) -> str:
    """Make ``vin`` known to the account; returns its picture's entity id."""

    await entry.runtime_data.coordinator.async_handle_message(
        {
            "vin": vin,
            "data": {
                ODOMETER: {"value": 1000, "unit": "km", "timestamp": dt_util.utcnow().isoformat()}
            },
        }
    )
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("image", DOMAIN, f"{vin}_vehicle_image")
    assert entity_id
    return entity_id


async def _picture(hass_client, entity_id: str) -> tuple[int, bytes]:
    client = await hass_client()
    response = await client.get(f"/api/image_proxy/{entity_id}")
    return response.status, await response.read()


def _official(no_bmw) -> None:
    no_bmw["rest_bytes"].side_effect = None
    no_bmw["rest_bytes"].return_value = (PNG, "image/png")


async def test_the_official_picture_is_fetched_once_and_kept(
    hass: HomeAssistant, no_bmw, hass_client, hass_storage
) -> None:
    _official(no_bmw)
    entry = _entry()
    await _set_up(hass, entry)
    entity_id = await _car(hass, entry)

    assert no_bmw["rest_bytes"].await_count == 1
    assert await _picture(hass_client, entity_id) == (200, PNG)
    state = hass.states.get(entity_id)
    assert "image_fetched_at" in state.attributes
    assert "configurator_spin" not in state.attributes
    stored = hass_storage[IMAGES]["data"][VIN]
    assert base64.b64decode(stored["data"]) == PNG
    # Shown from the cache: no second request.
    assert no_bmw["rest_bytes"].await_count == 1


async def test_a_kept_picture_comes_back_without_a_request(
    hass: HomeAssistant, no_bmw, hass_client, hass_storage
) -> None:
    hass_storage[IMAGES] = {
        "version": 1,
        "key": IMAGES,
        "data": {
            VIN: {"data": base64.b64encode(PNG).decode(), "updated": "2026-10-01T10:00:00+00:00"},
            "WBATEST0000000002": {"content_type": "image/png"},
            "WBATEST0000000003": {"data": "abc"},
        },
    }
    entry = _entry()
    await _set_up(hass, entry)
    entity_id = await _car(hass, entry)

    assert no_bmw["rest_bytes"].await_count == 0
    assert await _picture(hass_client, entity_id) == (200, PNG)
    assert hass.states.get(entity_id).attributes["image_fetched_at"] == "2026-10-01T10:00:00+00:00"
    cache = hass.data[DOMAIN]["_vehicle_image_cache"]
    assert set(cache) == {VIN}
    assert cache[VIN]["content_type"] == "image/png"


async def test_no_picture_when_bmw_does_not_answer(
    hass: HomeAssistant, no_bmw, hass_client
) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    entity_id = await _car(hass, entry)

    # The background fetch failed; showing the picture asks once more, and fails again.
    status, _ = await _picture(hass_client, entity_id)
    assert status == 500
    assert no_bmw["rest_bytes"].await_count == 2


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (CardataAuthError("refused"), "no_token"),
        (CardataQuotaError("spent"), "request_blocked"),
        (CardataApiError("down", status=503), "bmw_error"),
    ],
)
async def test_a_service_call_says_why_there_is_no_picture(
    hass: HomeAssistant, error: Exception, key: str
) -> None:
    runtime = SimpleNamespace(rest=SimpleNamespace(call=AsyncMock(side_effect=error)))
    entry = _entry()

    assert await image.async_refresh_vehicle_image(hass, entry, runtime, VIN) is None
    with pytest.raises(HomeAssistantError) as raised:
        await image.async_refresh_vehicle_image(hass, entry, runtime, VIN, strict=True)
    assert raised.value.translation_key == key


async def test_the_configurator_draws_a_car_and_the_card_gets_its_files(
    hass: HomeAssistant, no_bmw, hass_client, hass_client_no_auth, hass_storage, renders_dir
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"image_source": "configurator"})
    with patch(f"{PACKAGE}.image.async_render", AsyncMock(return_value=_render(**FULL))) as draw:
        await _set_up(hass, entry)
        entry.runtime_data.coordinator.device_metadata[VIN] = {"raw_data": {"vin": VIN}}
        entity_id = await _car(hass, entry)
    assert draw.await_args.kwargs["extras"] is True

    key = image._render_key(VIN)
    assert VIN not in key
    assert sorted(path.name for path in renders_dir.iterdir()) == sorted(
        [
            f"{key}.webp",
            f"{key}_lights.webp",
            f"{key}_night.webp",
            f"{key}_interior.png",
            f"{key}_top.webp",
            f"{key}_options.png",
            f"{key}_still.png",
        ]
    )
    record = hass_storage[RENDERS]["data"][VIN]
    assert record["schema"] == 3
    assert record["options"] == {
        "index": {code: n for n, code in enumerate(FULL["option_codes"])},
        "columns": OPTION_COLUMNS,
        "rows": 2,
    }

    attrs = hass.states.get(entity_id).attributes
    spin = attrs["configurator_spin"]
    assert spin["frames"] == 36
    assert spin["path"].startswith(f"{image.SPIN_PATH}/{key}/")
    assert spin["lights_path"].startswith(f"{image.SPIN_PATH}/{key}_lights/")
    assert spin["night_path"].startswith(f"{image.SPIN_PATH}/{key}_night/")
    assert attrs["configurator_interior"]["columns"] == 3
    assert attrs["configurator_top"]["path"].startswith(f"{image.SPIN_PATH}/{key}_top/")
    assert attrs["configurator_options"]["rows"] == 2
    assert attrs["paint_color"] == "#123456"
    assert attrs["equipment"] == [{"code": "S0", "name": "Seats"}]
    assert attrs["equipment_language"] == "en"

    # The still frame is the entity's picture; the official one was never asked for.
    assert await _picture(hass_client, entity_id) == (200, b"still")
    assert no_bmw["rest_bytes"].await_count == 0

    client = await hass_client()
    served = await client.get(spin["path"])
    assert served.status == 200
    assert await served.read() == WEBP
    assert (await client.get(f"{image.SPIN_PATH}/{key}_interior/1")).status == 200
    assert (await client.get(f"{image.SPIN_PATH}/not-a-render/1")).status == 404
    assert (await client.get(f"{image.SPIN_PATH}/{'0' * 24}/1")).status == 404
    # Behind HA's login: the card's signed path, never an open URL.
    assert (await (await hass_client_no_auth()).get(spin["path"])).status == 401

    # The settings screen's quick fetch replaces the plain spin only and keeps the schema.
    with patch(f"{PACKAGE}.image.async_render", AsyncMock(return_value=_render())):
        assert (
            await image.async_refresh_configurator_render(hass, entry.runtime_data, VIN, full=False)
            is None
        )
    assert (renders_dir / f"{key}_night.webp").exists()
    assert hass_storage[RENDERS]["data"][VIN]["schema"] == 3
    assert hass_storage[RENDERS]["data"][VIN]["parts"] == []

    # A removed car takes its pictures with it.
    await image.async_forget_vehicles(hass, [])
    await image.async_forget_vehicles(hass, [VIN])
    assert list(renders_dir.iterdir()) == []
    assert hass_storage[RENDERS]["data"] == {}


async def test_a_car_the_configurator_cannot_draw_gets_the_official_picture(
    hass: HomeAssistant, no_bmw, renders_dir
) -> None:
    _official(no_bmw)
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"image_source": "configurator"})
    draw = AsyncMock(side_effect=ConfiguratorError("unsupported", "no such model"))
    with patch(f"{PACKAGE}.image.async_render", draw):
        await _set_up(hass, entry)
        # No basic data yet: nothing to draw, and no official picture either.
        await _car(hass, entry)
        assert draw.await_count == 0
        assert no_bmw["rest_bytes"].await_count == 0

        # The car's name comes with its basic data: now it is drawn, and fails.
        entry.runtime_data.coordinator.device_metadata[VIN] = {"raw_data": {"vin": VIN}}
        async_dispatcher_send(hass, f"{DOMAIN}_{entry.entry_id}_name", "WBATEST0000000009", "x")
        async_dispatcher_send(hass, f"{DOMAIN}_{entry.entry_id}_name", VIN, "i5")
        await hass.async_block_till_done()
    assert draw.await_count == 1
    assert no_bmw["rest_bytes"].await_count == 1

    # Once it has one, the fallback leaves it alone.
    await image.async_official_fallback(hass, entry, entry.runtime_data, VIN)
    assert no_bmw["rest_bytes"].await_count == 1


async def test_an_older_render_is_redrawn_and_a_lost_still_falls_back(
    hass: HomeAssistant, no_bmw, hass_client, hass_storage, renders_dir
) -> None:
    _official(no_bmw)
    hass_storage[RENDERS] = {
        "version": 1,
        "key": RENDERS,
        "data": {
            VIN: {
                "key": image._render_key(VIN),
                "columns": 6,
                "rows": 6,
                "frames": 36,
                "frame_width": 640,
                "frame_height": 360,
                "updated": "2026-09-01T08:00:00+00:00",
                "schema": 1,
            },
            "garbled": "not a record",
        },
    }
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"image_source": "configurator"})
    with patch(f"{PACKAGE}.image.async_schedule_full_render") as full:
        await _set_up(hass, entry)
        entity_id = await _car(hass, entry)
    full.assert_called_once()
    assert set(hass.data[DOMAIN]["_configurator_render_cache"]) == {VIN}

    # The still frame's file is gone: the official picture stands in.
    assert await _picture(hass_client, entity_id) == (200, PNG)


async def test_the_full_fetch_runs_in_the_background(hass: HomeAssistant) -> None:
    with patch(
        f"{PACKAGE}.image.async_refresh_configurator_render", AsyncMock(return_value=None)
    ) as draw:
        image.async_schedule_full_render(hass, SimpleNamespace(), VIN)
        await hass.async_block_till_done()
    assert draw.await_args.kwargs == {"full": True}


async def test_a_car_without_basic_data_or_a_drawing_says_why(hass: HomeAssistant) -> None:
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(device_metadata={VIN: {"raw_data": {"vin": VIN}}}),
        session=None,
    )
    assert await image.async_refresh_configurator_render(hass, runtime, "WBANODATA") == "no_data"
    with patch(
        f"{PACKAGE}.image.async_render", AsyncMock(side_effect=ConfiguratorError("unavailable"))
    ):
        assert await image.async_refresh_configurator_render(hass, runtime, VIN) == "unavailable"


def test_a_file_keeps_the_format_it_was_saved_in() -> None:
    assert image._ext_of(b"RIFFxxxx", ".png") == ".webp"
    assert image._ext_of(PNG, ".webp") == ".png"
    assert image._ext_of(b"GIF8", ".webp") == ".webp"
