"""Settings a car can hold for itself, stored in the config entry's options.

An account can hold several cars, and some settings describe a car rather than
the house: how its trips are detected and filed, which wallbox meter measures
its charging and how lossy its charger is, whether it is published to evcc.
Those live per VIN (see ``OPTION_VEHICLES``)::

    {"vehicles": {VIN: {key: value, ...}}, <account-wide keys>...}

The keys come in groups -- one per Configure screen -- and a group is stored
whole: a car either has all of a group's settings of its own or none of them,
in which case the account-wide keys apply. Nothing is inherited key by key, so
what a screen shows for a car is exactly what applies to it. Only an account
with more than one car stores anything per car; a single-car account keeps
editing the account-wide keys, so a car added later starts from the settings
already in use.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .const import (
    OPTION_BRIDGE_ENABLED,
    OPTION_CHARGING_LOSS_PERCENT,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_TRIP_COMMUTE_GAP,
    OPTION_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_PHONE_IN_CAR,
    OPTION_TRIP_PHONE_TRACKER,
    OPTION_TRIP_TRACK,
    OPTION_TRIP_WORK_ZONE,
    OPTION_VEHICLES,
)

# The groups, in the order their screen shows them.
TRIP_OPTIONS = (
    OPTION_TRIP_WORK_ZONE,
    OPTION_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_COMMUTE_GAP,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_TRACK,
    # On a follow-up screen of Trips's own, shown only to a car that records
    # its route from end-of-trip reports; still one group with the rest.
    OPTION_TRIP_PHONE_TRACKER,
    OPTION_TRIP_PHONE_IN_CAR,
)
CHARGING_OPTIONS = (OPTION_GRID_ENERGY_ENTITY, OPTION_CHARGING_LOSS_PERCENT)
BRIDGE_OPTIONS = (OPTION_BRIDGE_ENABLED,)

# Entity selectors: Home Assistant leaves a cleared one out of the submitted
# data altogether, so its absence means "none", not "unchanged".
_CLEARABLE = frozenset(
    {
        OPTION_TRIP_WORK_ZONE,
        OPTION_TRIP_PHONE_TRACKER,
        OPTION_TRIP_PHONE_IN_CAR,
        OPTION_GRID_ENERGY_ENTITY,
    }
)


def _vehicles(options: Mapping[str, Any]) -> Mapping[str, Any]:
    vehicles = options.get(OPTION_VEHICLES)
    return vehicles if isinstance(vehicles, Mapping) else {}


def own_values(
    options: Mapping[str, Any], vin: Optional[str], keys: tuple[str, ...]
) -> Optional[dict[str, Any]]:
    """The car's own values for one group, or ``None`` if it has none."""

    own = _vehicles(options).get(vin) if vin else None
    if not isinstance(own, Mapping) or not any(key in own for key in keys):
        return None
    return {key: own[key] for key in keys if key in own}


def values_for(
    options: Mapping[str, Any], vin: Optional[str], keys: tuple[str, ...]
) -> dict[str, Any]:
    """The values of one group in force for ``vin`` (what its screen shows)."""

    own = own_values(options, vin, keys)
    if own is not None:
        return own
    return {key: options[key] for key in keys if key in options}


def cars_with_own(options: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Each car with values of its own for one group, by VIN."""

    found = {}
    for vin in _vehicles(options):
        own = own_values(options, vin, keys)
        if own is not None:
            found[vin] = own
    return found


def store_values(
    options: Mapping[str, Any],
    values: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    vin: Optional[str],
    per_vehicle: bool,
) -> dict[str, Any]:
    """New options with one screen's per-car values stored.

    ``per_vehicle`` is set when the account has more than one car: the group
    then goes under that car alone, whole, next to whatever other groups the
    car holds. Otherwise the values are the account-wide keys, and a copy of the
    group the one car may still carry from a time the account had two is
    dropped -- it would silently outrank what was just saved.
    """

    updated = dict(options)
    picked = {key: values[key] for key in keys if key in values}
    for key in _CLEARABLE.intersection(keys):
        picked.setdefault(key, None)
    vehicles = {vin_: dict(own) for vin_, own in _vehicles(updated).items()}
    if per_vehicle and vin:
        own = {key: value for key, value in vehicles.get(vin, {}).items() if key not in keys}
        vehicles[vin] = {**own, **picked}
    else:
        updated.update(picked)
        if vin and vin in vehicles:
            rest = {key: value for key, value in vehicles[vin].items() if key not in keys}
            if rest:
                vehicles[vin] = rest
            else:
                del vehicles[vin]
    if vehicles:
        updated[OPTION_VEHICLES] = vehicles
    else:
        updated.pop(OPTION_VEHICLES, None)
    return updated
