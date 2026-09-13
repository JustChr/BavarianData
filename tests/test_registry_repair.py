"""Re-enabling entities an old default left disabled.

Home Assistant fixes an entity's enabled state when it is first registered, so a
default flipped in a later release never reaches existing installs. The rows
below mirror the maintainer's real i5 registry, where the measured state of
charge (``batteryManagement.header``) had sat disabled since before v0.9.6.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace

from .conftest import load_module

repair = load_module("registry_repair")
metadata = load_module("descriptor_metadata").DESCRIPTOR_META

VIN = "WBAEXAMPLE0000001"
HEADER = "vehicle.drivetrain.batteryManagement.header"
PLUG_EVENT = "vehicle.body.chargingPort.plugEventId"
FINISH_REASON = "vehicle.drivetrain.electricEngine.charging.hvpmFinishReason"


def row(entity_id: str, unique_id: str, disabled_by=None) -> SimpleNamespace:
    return SimpleNamespace(entity_id=entity_id, unique_id=unique_id, disabled_by=disabled_by)


class Disabler(str, enum.Enum):
    """Stand-in for Home Assistant's ``RegistryEntryDisabler`` (a StrEnum)."""

    INTEGRATION = "integration"
    USER = "user"


def test_the_measured_soc_is_enabled_by_default_today() -> None:
    """The premise of the repair: without this there is nothing to restore."""

    assert metadata[HEADER]["enabled_default"] is True


def test_the_real_i5_registry_repairs_exactly_the_measured_soc() -> None:
    rows = [
        row("sensor.soc_header", f"{VIN}_{HEADER}", Disabler.INTEGRATION),
        # Disabled by the integration, and still off by default: correct as is.
        row("sensor.plug_event", f"{VIN}_{PLUG_EVENT}", Disabler.INTEGRATION),
        row("sensor.finish_reason", f"{VIN}_{FINISH_REASON}", Disabler.INTEGRATION),
        # Enabled entities and our own derived ones.
        row("sensor.max_energy", f"{VIN}_vehicle.drivetrain.batteryManagement.maxEnergy"),
        row("sensor.soc_estimate", f"{VIN}_soc_estimate"),
    ]
    assert repair.entities_to_reenable(rows, metadata) == ["sensor.soc_header"]


def test_a_user_disabled_entity_is_never_touched() -> None:
    """Someone switched it off on purpose; an upgrade must not overrule them."""

    rows = [row("sensor.soc_header", f"{VIN}_{HEADER}", Disabler.USER)]
    assert repair.entities_to_reenable(rows, metadata) == []


def test_a_descriptor_still_off_by_default_stays_off() -> None:
    rows = [row("sensor.plug_event", f"{VIN}_{PLUG_EVENT}", Disabler.INTEGRATION)]
    assert metadata[PLUG_EVENT]["enabled_default"] is False
    assert repair.entities_to_reenable(rows, metadata) == []


def test_the_plain_string_disabler_matches_too() -> None:
    rows = [row("sensor.soc_header", f"{VIN}_{HEADER}", "integration")]
    assert repair.entities_to_reenable(rows, metadata) == ["sensor.soc_header"]


def test_entities_without_a_descriptor_are_ignored() -> None:
    """Derived, diagnostic, tracker and image entities are not metadata-driven."""

    rows = [
        row("sensor.soc_estimate", f"{VIN}_soc_estimate", Disabler.INTEGRATION),
        row("sensor.quota", "01ENTRY_diagnostics_api_quota_remaining", Disabler.INTEGRATION),
        row("device_tracker.car", f"{VIN}_tracker", Disabler.INTEGRATION),
        row("sensor.odd", "no-separator", Disabler.INTEGRATION),
        row("sensor.empty", "", Disabler.INTEGRATION),
    ]
    assert repair.entities_to_reenable(rows, metadata) == []


def test_an_unknown_descriptor_is_left_alone() -> None:
    rows = [row("sensor.gone", f"{VIN}_vehicle.not.in.the.catalogue", Disabler.INTEGRATION)]
    assert repair.entities_to_reenable(rows, metadata) == []


def test_unique_ids_split_on_the_first_separator_only() -> None:
    assert repair.descriptor_of(f"{VIN}_{HEADER}") == HEADER
    assert repair.descriptor_of(f"{VIN}_soc_estimate") == "soc_estimate"
    assert repair.descriptor_of("nounderscore") is None
    assert repair.descriptor_of("_leading") is None
