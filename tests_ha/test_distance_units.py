"""The Units setting applied to the registry, and the unit a car counts in.

The rules are ``units.py``'s, tested in ``tests/``; this checks what
``distance.py`` reads from and writes to Home Assistant's entity registry.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.distance import (
    async_apply_sensor_units,
    car_distance_unit,
    car_fuel_unit,
)
from custom_components.bavariandata.units import DESC_DISPLAY_DISTANCE, unit_choices

from .conftest import VIN
from .test_services import ODOMETER, _account


def _odometer(hass: HomeAssistant) -> er.RegistryEntry:
    registry = er.async_get(hass)
    return registry.async_get(registry.async_get_entity_id("sensor", DOMAIN, f"{VIN}_{ODOMETER}"))


async def test_a_unit_choice_is_set_and_taken_back(hass: HomeAssistant, no_bmw) -> None:
    entry, runtime, _trip = await _account(hass)
    coordinator = runtime.coordinator

    coordinator.display_units = unit_choices({"unit_distance": "mi"})
    async_apply_sensor_units(hass, entry.entry_id, coordinator)
    assert _odometer(hass).options["sensor"]["unit_of_measurement"] == "mi"
    assert car_distance_unit(hass, coordinator, VIN) == "mi"

    coordinator.display_units = unit_choices({})
    async_apply_sensor_units(hass, entry.entry_id, coordinator)
    assert "unit_of_measurement" not in _odometer(hass).options["sensor"]
    assert not _odometer(hass).options.get(DOMAIN)


async def test_a_unit_the_user_picked_since_is_left_alone(hass: HomeAssistant, no_bmw) -> None:
    entry, runtime, _trip = await _account(hass)
    coordinator = runtime.coordinator
    registry = er.async_get(hass)

    coordinator.display_units = unit_choices({"unit_distance": "mi"})
    async_apply_sensor_units(hass, entry.entry_id, coordinator)
    registry.async_update_entity_options(
        _odometer(hass).entity_id, "sensor", {"unit_of_measurement": "km"}
    )

    coordinator.display_units = unit_choices({})
    async_apply_sensor_units(hass, entry.entry_id, coordinator)
    assert _odometer(hass).options["sensor"]["unit_of_measurement"] == "km"
    assert not _odometer(hass).options.get(DOMAIN)


async def test_the_car_s_own_display_setting_is_read(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    coordinator = runtime.coordinator
    await coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                DESC_DISPLAY_DISTANCE[0]: {
                    "value": "mi",
                    "unit": None,
                    "timestamp": dt_util.utcnow().isoformat(),
                }
            },
        }
    )
    await hass.async_block_till_done()

    # The odometer sensor's own unit (km) comes first.
    assert car_distance_unit(hass, coordinator, VIN) == "km"
    assert car_fuel_unit(hass, coordinator, VIN) == "l"
    assert car_distance_unit(hass, coordinator, None) == "km"
