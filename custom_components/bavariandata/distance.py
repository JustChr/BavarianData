"""Which unit one car's distances are entered and shown in, read off Home Assistant.

The rule itself is :func:`units.distance_unit` (HA-free and unit-tested); this
gathers its three inputs for a car: the units its distance sensors are displayed
in (odometer first), the car's own display setting, and Home Assistant's unit
system. The settings screens and the service calendar ask here, so they agree
with the card, which applies the same rule to the same entities.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import DESC_ODOMETER
from .units import DESC_DISPLAY_DISTANCE, distance_unit


def _sensor_units(hass: HomeAssistant, vin: str) -> list[Any]:
    """The displayed units of the car's distance sensors, odometer first."""

    prefix = f"{vin}_"
    odometer: dict[str, Any] = {}
    others: list[Any] = []
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or entry.domain != "sensor":
            continue
        if not str(entry.unique_id).startswith(prefix):
            continue
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        unit = state.attributes.get("unit_of_measurement")
        descriptor = str(entry.unique_id)[len(prefix) :]
        if descriptor in DESC_ODOMETER:
            odometer[descriptor] = unit
        elif state.attributes.get("device_class") == "distance":
            others.append(unit)
    return [*(odometer[d] for d in DESC_ODOMETER if d in odometer), *others]


def car_distance_unit(hass: HomeAssistant, coordinator: Any, vin: str | None) -> str:
    """``"mi"`` or ``"km"`` for ``vin``: see :func:`units.distance_unit`."""

    system = hass.config.units.length_unit
    if vin is None:
        return distance_unit((), None, system)
    setting = None
    for descriptor in DESC_DISPLAY_DISTANCE:
        state = coordinator.get_state(vin, descriptor) if coordinator is not None else None
        if state is not None and state.value is not None:
            setting = state.value
            break
    return distance_unit(_sensor_units(hass, vin), setting, system)
