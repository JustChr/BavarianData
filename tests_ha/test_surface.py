"""Everything Home Assistant shows of an account, pinned.

The real-HA half of the refactoring gate (``scripts/golden.py`` covers the
coordinator): an account with three cars -- electric, plug-in hybrid,
petrol, each sending what its real counterpart in ``tests/cars`` sends --
set up by a real Home Assistant, and every device, every entity registry
entry with its state, and every service snapshotted, as Home Assistant's own
tests do (``snapshot_platform``, which wants a single platform with every
entity enabled; this account has six platforms and sensors off by default).

A change that renames, adds, drops or reshapes anything a user sees fails
here with a diff naming the entity. An intended change is approved with
``bash scripts/test_ha.sh -k surface --snapshot-update`` and the diff read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from syrupy.filters import props

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.descriptor_metadata import DESCRIPTOR_META

from .conftest import make_entry

CARS = Path(__file__).parents[1] / "tests" / "cars"
NOW = "2026-10-24T10:00:00+00:00"
# Three drivetrains, each with its real counterpart's descriptor list.
ACCOUNT = {
    "WBATEST0000000001": ("i4-m60", {"modelName": "i4 M60", "driveTrain": "BEV"}),
    "WBATEST0000000002": ("x3-30e-phev", {"modelName": "X3 30e", "driveTrain": "PHEV"}),
    "WBATEST0000000003": ("m240i-xdrive", {"modelName": "M240i xDrive", "driveTrain": "CONV"}),
}
# Readings by unit; everything else measured gets 42.
# Home Assistant gives an image entity a fresh access token on every run.
RANDOM = props("access_token", "entity_picture")
NUMBERS = {"%": 63, "km": 12345, "kPa": 250, "°C": 20, "kWh": 58.5, "l": 31, "V": 230, "A": 16}


def _value(descriptor: str, unit: str | None) -> Any:
    """A reading of the kind ``descriptor`` carries, the same on every run.

    ``unit`` is the one the car sends, which the catalogue leaves out for some.
    """

    meta = DESCRIPTOR_META[descriptor]
    leaf = descriptor.rsplit(".", 1)[-1]
    if meta["options"]:
        return meta["options"][0].upper()  # BMW sends ALL_CAPS tokens
    if leaf == "latitude":
        return 48.0
    if leaf == "longitude":
        return 11.0
    if (unit := meta["unit"] or unit) is not None:
        return NUMBERS.get(unit, 42)
    if leaf.startswith(("is", "has")):
        return True
    return "OK"


def _batch(vin: str, car: str) -> dict:
    descriptors = json.loads((CARS / f"{car}.json").read_text(encoding="utf-8"))["descriptors"]
    return {
        "vin": vin,
        "data": {
            d["descriptor"]: {
                "value": _value(d["descriptor"], d["unit"]),
                "unit": d["unit"],
                "timestamp": NOW,
            }
            for d in descriptors
        },
    }


async def test_the_account_as_home_assistant_shows_it(
    hass: HomeAssistant, no_bmw, freezer, snapshot
) -> None:
    freezer.move_to(NOW)
    entry = make_entry(
        entry_id="01SURFACE0000000000000000A",
        vehicle_metadata={vin: {"brand": "BMW", **basic} for vin, (_car, basic) in ACCOUNT.items()},
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={"price_mode": "fixed", "price_fixed": 0.3, "price_currency": "EUR"}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for vin, (car, _basic) in ACCOUNT.items():
        await entry.runtime_data.coordinator.async_handle_message(_batch(vin, car))
    await hass.async_block_till_done()

    devices = sorted(
        dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id),
        key=lambda device: sorted(device.identifiers),
    )
    assert devices == snapshot(name="devices")

    entries = sorted(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id),
        key=lambda e: e.entity_id,
    )
    assert [e.entity_id for e in entries] == snapshot(name="entity_ids")
    for entity in entries:
        assert entity == snapshot(name=f"{entity.entity_id}-entry")
        assert hass.states.get(entity.entity_id) == snapshot(
            name=f"{entity.entity_id}-state", exclude=RANDOM
        )

    services = hass.services.async_services_for_domain(DOMAIN)
    assert {
        name: service.supports_response.value for name, service in sorted(services.items())
    } == snapshot(name="services")
