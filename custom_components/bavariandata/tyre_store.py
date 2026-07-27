"""Persistence for the smart-maintenance tyre diagnosis.

The diagnosis is REST-only -- BMW cannot stream it -- and it costs one of the
50 daily requests to fetch, so it is refreshed at most once a day. Without a
store it lived in the coordinator's memory alone, which meant every Home
Assistant restart blanked the tyre sensors until the next daily poll came due
(itself gated on ``last_telematic_poll``, so up to 24 h later) or the user
called ``fetch_tyre_diagnosis`` by hand.

Restoring via ``RestoreEntity`` is not an option here: a wheel's payload is a
dozen-odd attributes, and the card reads them as one document. Keeping the
parsed document itself is both smaller and honest about its age -- ``fetched_at``
rides along so consumers can tell day-old data from live data.

The stored shape is ``{"vehicles": {vin: {"fetched_at": iso, "diagnosis": {...}}}}``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .tyre import restore_diagnosis

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# A fetch happens at most a couple of times a day, so the debounce is only here
# to coalesce the setup-time restore with an immediately following fetch.
SAVE_DELAY_S = 10


class TyreStore:
    """Keeps the last fetched tyre diagnosis per VIN across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}_{entry_id}_tyre")
        self._vehicles: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    async def async_load(self) -> Dict[str, Dict[str, Any]]:
        """Read the store once at setup.

        Never raises -- a missing or corrupt tyre store must not stop the stream
        from setting up; the next fetch repopulates it.
        """

        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a corrupt store must not block setup
            _LOGGER.exception("Could not read tyre store; starting fresh")
            data = None

        self._loaded = True
        if not data:
            return {}

        raw = data.get("vehicles")
        if not isinstance(raw, dict):
            return {}

        restored: Dict[str, Dict[str, Any]] = {}
        for vin, record in raw.items():
            # Validation lives in tyre.py so it stays unit-testable without a
            # Home Assistant harness (tests/test_tyre.py).
            diagnosis = restore_diagnosis(record)
            if diagnosis is None:
                continue
            self._vehicles[str(vin)] = {
                "fetched_at": diagnosis.get("fetched_at"),
                "diagnosis": diagnosis,
            }
            restored[str(vin)] = diagnosis
        return restored

    @callback
    def async_record(self, vin: str, diagnosis: Dict[str, Any]) -> str:
        """Store ``diagnosis`` for ``vin`` and return the fetch timestamp.

        Saved with a short debounce rather than immediately: a multi-vehicle
        refresh records once per VIN in the same pass.
        """

        fetched_at = datetime.now(timezone.utc).isoformat()
        self._vehicles[vin] = {"fetched_at": fetched_at, "diagnosis": diagnosis}
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_S)
        return fetched_at

    def fetched_at(self, vin: str) -> Optional[str]:
        record = self._vehicles.get(vin) or {}
        stamp = record.get("fetched_at")
        return stamp if isinstance(stamp, str) else None

    @callback
    def _data_to_save(self) -> Dict[str, Any]:
        return {"vehicles": {vin: dict(record) for vin, record in self._vehicles.items()}}

    async def async_save_now(self) -> None:
        """Flush immediately -- used on unload so a pending debounce isn't lost."""

        if self._loaded:
            await self._store.async_save(self._data_to_save())

    async def async_clear(self) -> None:
        """Delete the stored diagnosis (integration removal)."""

        self._vehicles = {}
        await self._store.async_remove()
