"""The car's picture on *Notify when charging completes*, in a real Home Assistant.

With *Car picture* on, the message carries the picture of the car that stopped
charging as the Companion app's attachment (``data.image``, a path the app
fetches from Home Assistant with its own login). Off, and for a car without a
picture, the call is exactly the one the blueprint made before the setting.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.bavariandata.const import DOMAIN

from .conftest import VIN

BLUEPRINT = "notify_charging_complete.yaml"
SOURCE = Path(__file__).parents[1] / "blueprints" / "automation" / "bavariandata" / BLUEPRINT
OTHER_VIN = "WBATEST0000000002"
PICTURE_TAKEN = "2026-11-06T20:54:32+00:00"


def _car(hass: HomeAssistant, vin: str, picture: str | None = PICTURE_TAKEN) -> str:
    """A car's device and its picture entity; returns the picture's entity id.

    ``integration_entities('bavariandata')`` finds a real car's entities by the
    platform that loaded them; these are registry entries only, so it finds
    them by its other route: a config entry titled with the name.
    """

    entry = MockConfigEntry(domain=DOMAIN, unique_id=vin, title=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vin)}, name=f"Car {vin[-1]}"
    )
    entity = er.async_get(hass).async_get_or_create(
        "image", DOMAIN, f"{vin}_vehicle_image", device_id=device.id, config_entry=entry
    )
    hass.states.async_set(entity.entity_id, picture or "unknown")
    return entity.entity_id


def _phone(hass: HomeAssistant) -> str:
    entry = MockConfigEntry(domain="mobile_app", title="Pixel 10")
    entry.add_to_hass(hass)
    return (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("mobile_app", "Pixel 10")},
            name="Pixel 10",
        )
        .id
    )


async def _charge_ends(hass: HomeAssistant, tmp_path: Path, **inputs) -> dict:
    """Run the blueprint for one finished charge of ``VIN``; the phone's one call."""

    # Blueprint files go to this test's own folder, not the plugin's shared one.
    hass.config.config_dir = str(tmp_path)
    calls = async_mock_service(hass, "notify", "mobile_app_pixel_10")
    target = Path(hass.config.path("blueprints", "automation", "bavariandata", BLUEPRINT))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE, target)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"bavariandata/{BLUEPRINT}",
                    "input": {"phones": [_phone(hass)], **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()
    hass.bus.async_fire(
        "bavariandata_charging_stopped",
        {"vin": VIN, "soc": 80.0, "target_soc": 80.0, "complete": True, "vehicle": "i5 eDrive40"},
    )
    await hass.async_block_till_done()
    (call,) = calls
    assert call.data["title"] == "i5 eDrive40: charging complete"
    assert call.data["message"] == "Battery at 80% (target 80%)."
    return dict(call.data)


async def test_picture_of_the_car_that_stopped(hass: HomeAssistant, tmp_path: Path) -> None:
    _car(hass, OTHER_VIN)
    picture = _car(hass, VIN)

    data = await _charge_ends(hass, tmp_path, picture=True)

    assert data["data"] == {"image": f"/api/image_proxy/{picture}"}


async def test_off_sends_the_call_as_before(hass: HomeAssistant, tmp_path: Path) -> None:
    _car(hass, VIN)

    data = await _charge_ends(hass, tmp_path)

    assert set(data) == {"title", "message"}


async def test_a_car_without_a_picture_gets_the_plain_message(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _car(hass, OTHER_VIN)
    _car(hass, VIN, picture=None)

    data = await _charge_ends(hass, tmp_path, picture=True)

    assert set(data) == {"title", "message"}
