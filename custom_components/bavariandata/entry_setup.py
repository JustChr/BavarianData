"""What setting up an account decides, apart from Home Assistant.

``async_setup_entry`` in ``__init__.py`` builds the stores, the coordinator and
the stream, then wires them into Home Assistant. The decisions on the way --
how the options configure the coordinator, which one-time repairs the stored
history gets and in which order, which stored cars come back as devices, which
registry entries are ours to fix -- are made here, where they are tested
without Home Assistant (``tests/test_entry_setup.py``). ``__init__.py`` makes
the calls these answers name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from .const import (
    DEBUG_LOG,
    DEFAULT_HISTORY_RETAIN_MONTHS,
    DIAGNOSTIC_LOG_INTERVAL,
    OPTION_DEBUG_LOG,
    OPTION_DIAGNOSTIC_INTERVAL,
    OPTION_HISTORY_RETAIN_MONTHS,
    OPTION_TRIP_DEBUG,
    OPTION_TRIP_GEOCODE,
)
from .container import RETIRED_ENTRY_KEYS
from .history.geocoding import ReverseGeocoder
from .history.pricing import PricingConfig, vehicle_pricing
from .registry_repair import (
    entities_to_reenable,
    ev_entities_to_remove,
    hybrid_entities_to_remove,
    retired_entities_to_remove,
)
from .trip_settings import account_trip_settings, vehicle_trip_settings
from .units import unit_choices
from .vehicle_options import mileage_allowances

_LOGGER = logging.getLogger(__name__)


def debug_flag(options: Mapping[str, Any]) -> bool:
    """Whether debug logging is on: the option, or the build default unset."""

    value = options.get(OPTION_DEBUG_LOG)
    return DEBUG_LOG if value is None else bool(value)


def retain_months(options: Mapping[str, Any]) -> Optional[int]:
    """Retention in months, where 0 (or nonsense) means keep forever."""

    try:
        months = int(options.get(OPTION_HISTORY_RETAIN_MONTHS, DEFAULT_HISTORY_RETAIN_MONTHS))
    except TypeError, ValueError:
        return DEFAULT_HISTORY_RETAIN_MONTHS
    return months if months > 0 else None


def without_retired_keys(data: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The entry's data without keys no release reads, or ``None`` if it has none.

    v0.9.20-beta.6's fallback container. Its BMW side goes when the full
    container is next replaced (``retired_names``), which the full set's new
    signature makes happen on the first fetch.
    """

    if not any(key in data for key in RETIRED_ENTRY_KEYS):
        return None
    return {key: value for key, value in data.items() if key not in RETIRED_ENTRY_KEYS}


def repair_history(history: Any, zone_at: Callable[..., Any]) -> None:
    """The stored history's one-time repairs, in the order they depend on."""

    # Legacy imports stored coordinates but no zone (see
    # HistoryStore.reresolve_zones). Now that the coordinator's zone lookup is
    # available, resolve them locally so a charge at Home stops showing "public".
    history.reresolve_zones(zone_at)
    # And charges made away from home lose the house mix and tariff cost they
    # were given before #97. After the line above, so a zone it resolves counts.
    history.drop_house_figures_elsewhere()
    # Metered charges whose mix and cost covered only part of the meter's count
    # before #197 say so instead of passing for complete.
    history.flag_unbilled_meter_energy()
    # With *Remember charging locations* off, no coordinates stay on disk --
    # after the zone repair above, which needs them one last time.
    if not history.remember_locations:
        history.forget_locations()


def configure(coordinator: Any, options: Mapping[str, Any], session: Any) -> None:
    """Hand the coordinator everything the options decide.

    Before the open charges are restored: a restored cost is only kept if its
    currency still matches the tariff. Everything here is refreshed on reload,
    which is what an options change does.
    """

    coordinator.pricing = PricingConfig.from_options(options)
    coordinator.vehicle_pricing = vehicle_pricing(options)
    # Trips: the shared client session drives the (opt-in) reverse geocoder.
    coordinator.geocoder = ReverseGeocoder(session, enabled=bool(options.get(OPTION_TRIP_GEOCODE)))
    # Per car (trip_settings.py): every car without settings of its own uses
    # the account-wide ones.
    coordinator.trip_settings = account_trip_settings(options)
    coordinator.vehicle_trip_settings = vehicle_trip_settings(options)
    coordinator.trip_debug = bool(options.get(OPTION_TRIP_DEBUG))
    coordinator.diagnostic_interval = options.get(
        OPTION_DIAGNOSTIC_INTERVAL, DIAGNOSTIC_LOG_INTERVAL
    )
    coordinator.mileage_allowances = mileage_allowances(options)
    coordinator.display_units = unit_choices(options)


def restore_metadata(coordinator: Any, stored: Any) -> list[tuple[str, dict[str, Any]]]:
    """Apply each stored car's basic data; the cars that come back as devices.

    Returns ``(vin, device fields)`` for the device registry, so a car is a
    device again before the platforms load, without spending a REST request.
    A payload that is not a mapping, or that the coordinator cannot read, is
    skipped: a damaged entry must not stop the account from setting up.
    """

    if not isinstance(stored, dict):
        return []
    devices: list[tuple[str, dict[str, Any]]] = []
    for vin, payload in stored.items():
        if not isinstance(payload, dict):
            continue
        try:
            metadata = coordinator.apply_basic_data(vin, payload)
        except Exception:  # noqa: BLE001 - one damaged car must not stop setup
            _LOGGER.debug("Failed to restore metadata for %s", vin, exc_info=True)
            continue
        if metadata:
            devices.append((vin, device_fields(vin, metadata)))
    return devices


def device_fields(vin: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """A car's device-registry fields from its basic data."""

    return {
        "manufacturer": metadata.get("manufacturer", "BMW"),
        "name": metadata.get("name", vin),
        "model": metadata.get("model"),
        "sw_version": metadata.get("sw_version"),
        "hw_version": metadata.get("hw_version"),
        "serial_number": metadata.get("serial_number"),
    }


@dataclass(frozen=True)
class RegistryFix:
    """One entity-registry change: re-enable or remove ``entity_id``."""

    entity_id: str
    remove: bool
    reason: str


def registry_fixes(
    rows: Iterable[Any],
    metadata: Mapping[str, Mapping[str, Any]],
    *,
    combustion_only: Callable[[str], bool],
    plug_in_hybrid: Callable[[str], bool],
) -> list[RegistryFix]:
    """The registry changes an account's entries are due, in the order to make them.

    Re-enabling first: Home Assistant fixes an entity's enabled state when it
    is first registered, so a descriptor whose ``enabled_default`` a later
    release turned on stays off on every install that already had it -- the
    measured state of charge, off until v0.9.6, is the case that prompted
    this. Only entries *we* disabled are touched, never a user's choice (see
    registry_repair.py). Home Assistant answers any ``disabled_by`` change with
    one delayed reload of the entry; that costs a single stream reconnect,
    once, on the first start after upgrading.

    Then the removals, each entity at most once: battery-only sensors on a car
    with a fuel system and no battery, the odometer ratios on a plug-in hybrid,
    and entities no release makes any more. Removal (unlike a ``disabled_by``
    change) does not reload the entry.
    """

    rows = list(rows)
    fixes = [
        RegistryFix(entity_id, False, "an earlier release created it disabled by default")
        for entity_id in entities_to_reenable(rows, metadata)
    ]
    removals = (
        (
            ev_entities_to_remove(rows, combustion_only),
            "this car has a fuel system and no high-voltage battery",
        ),
        (
            hybrid_entities_to_remove(rows, plug_in_hybrid),
            "this car is a plug-in hybrid, so its consumption cannot "
            "be measured from the charging history",
        ),
        (retired_entities_to_remove(rows), "this release no longer makes it"),
    )
    removed: set[str] = set()
    for entity_ids, reason in removals:
        for entity_id in entity_ids:
            if entity_id not in removed:
                removed.add(entity_id)
                fixes.append(RegistryFix(entity_id, True, reason))
    return fixes
