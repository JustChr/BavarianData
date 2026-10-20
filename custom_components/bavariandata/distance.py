"""Which units one car's values are entered and shown in, read off Home Assistant.

The rules themselves are :func:`units.distance_unit` and :func:`units.fuel_unit`
(HA-free and unit-tested); this gathers their inputs for a car: the account's
Units setting, the units its distance sensors are displayed in (odometer first),
the car's own display setting, and Home Assistant's unit system. The settings
screens, the service calendar, the month report and Assist ask here, so they
agree with the card, which applies the same rules to the same entities.

It also applies an explicit choice to the sensors themselves
(:func:`async_apply_sensor_units`), so Home Assistant's own pages show the same
units as the card.
"""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import DESC_ODOMETER
from .units import DESC_DISPLAY_DISTANCE, distance_unit, fuel_unit, sensor_unit_update

# Marks a display unit this integration set (in the entity's registry options
# under our domain), so returning to Automatic can take back exactly what we
# set and never a unit the user picked on the entity themselves.
_APPLIED = "applied_display_unit"


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


def _choices(coordinator: Any) -> Mapping[str, str]:
    return getattr(coordinator, "display_units", None) or {}


def car_distance_unit(hass: HomeAssistant, coordinator: Any, vin: str | None) -> str:
    """``"mi"`` or ``"km"`` for ``vin``: see :func:`units.distance_unit`."""

    system = hass.config.units.length_unit
    choice = _choices(coordinator).get("distance")
    if vin is None:
        return distance_unit((), None, system, choice)
    setting = None
    for descriptor in DESC_DISPLAY_DISTANCE:
        state = coordinator.get_state(vin, descriptor) if coordinator is not None else None
        if state is not None and state.value is not None:
            setting = state.value
            break
    return distance_unit(_sensor_units(hass, vin), setting, system, choice)


def car_fuel_unit(hass: HomeAssistant, coordinator: Any, vin: str | None) -> str:
    """``"l"``, ``"l_mpg_uk"`` or ``"gal_us"`` for ``vin``: see :func:`units.fuel_unit`."""

    return fuel_unit(
        _choices(coordinator).get("fuel"),
        car_distance_unit(hass, coordinator, vin),
        hass.config.units.length_unit,
    )


def async_apply_sensor_units(hass: HomeAssistant, entry_id: str, coordinator: Any) -> None:
    """Show the account's sensors in the units the Units setting names.

    An explicit choice sets the display unit of every sensor it covers; Automatic
    takes back a unit we set earlier, unless the user has changed it since. The
    native unit never changes, so the recorder's statistics stay in one unit.
    """

    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(registry, entry_id):
        apply_sensor_unit(registry, entry, coordinator)


def _native_unit(coordinator: Any, unique_id: str) -> str | None:
    """The unit a descriptor sensor's value arrived in; ``None`` for derived sensors."""

    for vin, descriptors in getattr(coordinator, "data", {}).items():
        prefix = f"{vin}_"
        if unique_id.startswith(prefix):
            state = descriptors.get(unique_id[len(prefix) :])
            return state.unit if state is not None else None
    return None


def apply_sensor_unit(
    registry: er.EntityRegistry, entry: er.RegistryEntry, coordinator: Any
) -> None:
    """One registry entry's part of :func:`async_apply_sensor_units`."""

    if entry.platform != DOMAIN or entry.domain != "sensor":
        return
    sensor_options = dict(entry.options.get("sensor") or {})
    current = sensor_options.get("unit_of_measurement")
    ours = (entry.options.get(DOMAIN) or {}).get(_APPLIED)
    update = sensor_unit_update(
        entry.device_class or entry.original_device_class,
        _choices(coordinator),
        current,
        ours,
        _native_unit(coordinator, str(entry.unique_id)),
    )
    if update is None:
        return
    unit, mark = update
    if unit != current:
        if unit is None:
            sensor_options.pop("unit_of_measurement", None)
        else:
            sensor_options["unit_of_measurement"] = unit
        registry.async_update_entity_options(entry.entity_id, "sensor", sensor_options)
    if mark != ours:
        registry.async_update_entity_options(
            entry.entity_id, DOMAIN, {_APPLIED: mark} if mark else {}
        )
