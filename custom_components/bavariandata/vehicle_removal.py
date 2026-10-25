"""Forgetting a car that left the account: its device, entities and stored data."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from . import entry_setup
from .bridge import async_clear_published
from .const import (
    DOMAIN,
    OPTION_STREAM_SECTIONS,
    VEHICLE_METADATA,
)
from .coverage import coverage_issue_id, legacy_coverage_issue_id
from .coverage_store import CoverageStore
from .evcc import BridgeConfig
from .history.backfill import StatisticsPublisher
from .history.store import HistoryStore
from .tyre_store import TyreStore
from .vehicle_options import without_vehicle
from .vehicle_support import motorcycle_issue_id
from .vehicles import (
    forget_in_entry_data,
    left_account_issue_id,
)

_LOGGER = logging.getLogger(__name__)


@callback
def registered_vins(hass: HomeAssistant, entry: ConfigEntry, *, exclude: set[str]) -> list[str]:
    """VINs this entry has a device for, minus ``exclude``.

    The last word on which cars an entry ever had: the coordinator is gone by
    removal time and a car that streamed without ever answering a basic-data
    request is in no stored record, but it does have a device. The debug device
    is identified by the entry id rather than a VIN, so it is skipped.
    """

    found: list[str] = []
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        for domain, value in device.identifiers:
            if domain != DOMAIN or value == entry.entry_id or value in exclude:
                continue
            exclude.add(value)
            found.append(value)
    return found


# One cleanup at a time per entry. Two cars deleted seconds apart each start
# one, and the second, finding the entry mid-unload, would open the same stores
# alongside the first: whichever saved last brought the other car's data back.
# Module-level, not in ``hass.data``: unloading the last entry empties that.
_FORGET_LOCKS: Dict[str, asyncio.Lock] = {}


async def async_forget_account_cars(hass: HomeAssistant, entry_id: str, vins: list[str]) -> None:
    """:func:`_async_forget_vehicles_now`, after any cleanup already under way."""

    async with _FORGET_LOCKS.setdefault(entry_id, asyncio.Lock()):
        await _async_forget_vehicles_now(hass, entry_id, vins)


async def _async_forget_vehicles_now(hass: HomeAssistant, entry_id: str, vins: list[str]) -> None:
    """Delete everything kept for these cars, the way removing the entry does for all.

    The entry is unloaded first: its coordinator and stores hold the cars in
    memory and would write them straight back on the next save. Each store is
    then opened with the entry's own settings (a default retention would prune
    the other cars' history on load), and the entry set up again.
    """

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return
    reload = entry.state is ConfigEntryState.LOADED
    if reload and not await hass.config_entries.async_unload(entry_id):
        _LOGGER.warning("Could not unload entry %s to delete a car's data", entry_id)
        return
    with suppress(Exception):
        history = HistoryStore(
            hass, entry_id, retain_months=entry_setup.retain_months(dict(entry.options))
        )
        await history.async_load()
        publisher = StatisticsPublisher(hass, history, name_for=lambda vin: vin)
        for vin in vins:
            history.forget_vehicle(vin)
            await publisher.async_remove_vehicle(vin)
        await history.async_save_now()
    with suppress(Exception):
        coverage = CoverageStore(
            hass, entry_id, sections=entry.data.get(OPTION_STREAM_SECTIONS) or []
        )
        await coverage.async_load()
        for vin in vins:
            coverage.forget_vehicle(vin)
        await coverage.async_save_now()
    with suppress(Exception):
        tyre = TyreStore(hass, entry_id)
        await tyre.async_load()
        for vin in vins:
            tyre.forget_vehicle(vin)
        await tyre.async_save_now()
    bridge_config = BridgeConfig.from_options(dict(entry.options))
    if bridge_config.any_enabled:
        with suppress(Exception):
            await async_clear_published(hass, prefix=bridge_config.prefix, vins=vins)
    with suppress(Exception):
        from .image import async_forget_vehicles  # noqa: PLC0415 - pulls in HA's image component

        await async_forget_vehicles(hass, vins)
    data: dict[str, Any] = dict(entry.data)
    options: dict[str, Any] = dict(entry.options)
    for vin in vins:
        data = forget_in_entry_data(data, vin, VEHICLE_METADATA)
        options = without_vehicle(options, vin)
        for issue_id in (
            coverage_issue_id(entry_id, vin),
            legacy_coverage_issue_id(entry_id, vin),
            motorcycle_issue_id(entry_id, vin),
            left_account_issue_id(entry_id, vin),
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    hass.config_entries.async_update_entry(entry, data=data, options=options)
    _LOGGER.info("Deleted the stored data of %s car(s) on entry %s", len(vins), entry_id)
    if reload:
        await hass.config_entries.async_setup(entry_id)
