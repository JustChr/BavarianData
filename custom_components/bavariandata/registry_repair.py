"""Re-enable entities that an older release created disabled.

Home Assistant decides whether an entity starts enabled exactly once: when it is
first registered, from ``entity_registry_enabled_default``. Change that default
in a later release and every install that already had the entity keeps the old
answer forever -- its registry entry says ``disabled_by=integration`` and nothing
ever revisits it.

That is not hypothetical. ``batteryManagement.header``, the car's *measured*
state of charge, was created disabled until v0.9.6 flipped its default, so every
install from before then still has it switched off (found on the maintainer's
own i5, registered at its July install). No data is wrong -- the coordinator
reads the stream, not the entity -- but the measured SoC is invisible, and
because a disabled entity never restores its state, a restart leaves the
integration without a last-measured SoC timestamp until the car next reports
one.

Home Assistant-free on purpose: *which* registry entries to re-enable is a pure
function of the rows and our metadata, so it is unit-tested here; ``__init__.py``
makes the registry calls.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional

# The integration's own entities that only mean something with a high-voltage
# battery: the state-of-charge estimate and its rate, the charged-energy
# counters, the charging ledger and cost sensors, battery health and real range.
# Up to v0.9.9-beta.9 the first ten were created for every car, so a petrol or
# diesel install carries them stuck at "unknown" -- and, because they are
# re-created from the registry on every start, would carry them forever.
EV_ONLY_SUFFIXES = frozenset(
    {
        "soc_estimate",
        "soc_estimate_testing",
        "soc_rate",
        "charged_energy_total",
        "charged_energy_session",
        "charging_energy_month",
        "charging_cost_month",
        "charging_cost_session",
        "charging_cost_per_100km",
        "battery_health",
        "real_range",
    }
)

# Home Assistant's ``RegistryEntryDisabler.INTEGRATION``, spelled as its string
# value so this module needs no HA import (the enum is a ``StrEnum``, so it
# compares equal). It is the only disabler that is ours to undo: ``user`` is a
# choice someone made, and ``device`` / ``config_entry`` are decided elsewhere.
DISABLED_BY_INTEGRATION = "integration"


def descriptor_of(unique_id: str) -> Optional[str]:
    """The descriptor behind a ``<VIN>_<descriptor>`` unique id, if it has one.

    Our other entities (``<VIN>_soc_estimate``, ``<VIN>_tracker``,
    ``<entry_id>_diagnostics_*``) split to a remainder that is simply not in the
    descriptor metadata, so they are never matched.
    """

    vin, separator, rest = unique_id.partition("_")
    return rest if separator and vin and rest else None


def entities_to_reenable(
    rows: Iterable[Any], metadata: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Entity ids disabled by an old default that the current metadata enables.

    ``rows`` are entity-registry entries (anything with ``entity_id``,
    ``unique_id`` and ``disabled_by``). An entry qualifies only when all three
    hold: *we* disabled it, it is a descriptor entity, and that descriptor is
    ``enabled_default: True`` today. A descriptor still off by default stays off
    -- re-enabling it would be the same mistake in the other direction.
    """

    selected: list[str] = []
    for row in rows:
        if row.disabled_by != DISABLED_BY_INTEGRATION:
            continue
        descriptor = descriptor_of(row.unique_id or "")
        meta = metadata.get(descriptor) if descriptor else None
        if meta is not None and meta.get("enabled_default") is True:
            selected.append(row.entity_id)
    return selected


def ev_entities_to_remove(rows: Iterable[Any], combustion_only: Callable[[str], bool]) -> list[str]:
    """Entity ids of battery-only entities on a car proven to have no battery.

    ``combustion_only`` answers for a VIN, and must need positive evidence (see
    ``coverage.is_combustion_only``): a car that has sent nothing yet is not a
    petrol car, and removing its entities would throw away a real EV's history
    on a fresh restart. Removed rather than disabled on purpose -- a
    ``disabled_by`` change makes Home Assistant reload the entry, and an entity
    that can never have a value has no customisation worth keeping.
    """

    selected: list[str] = []
    for row in rows:
        vin, separator, suffix = (row.unique_id or "").partition("_")
        if not (separator and vin) or suffix not in EV_ONLY_SUFFIXES:
            continue
        if combustion_only(vin):
            selected.append(row.entity_id)
    return selected
