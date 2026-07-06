"""Image platform for BMW CarData.

Exposes the vehicle render served by BMW's ``/customers/vehicles/{vin}/image``
endpoint as a proper Home Assistant ``image`` entity so it can be used as the
hero image in the Lovelace card (or anywhere else).

The render is effectively static, and the CarData REST API only allows ~50 calls
per day, so the bytes are cached in memory *and* persisted to a Store. That way a
Home Assistant restart re-uses the stored render instead of spending a quota slot
on every boot; the picture is only (re)fetched when it is missing or when the
``fetch_vehicle_image`` service is called explicitly.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional, Tuple

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import CardataApiError, async_get_vehicle_image
from .const import DOMAIN, SIGNAL_VEHICLE_IMAGE
from .coordinator import CardataCoordinator
from .entity import CardataEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}_vehicle_images"
# Where the shared image cache / store live on hass.data[DOMAIN].
_CACHE_KEY = "_vehicle_image_cache"
_STORE_HANDLE_KEY = "_vehicle_image_store"


def _domain_data(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {})


async def _async_get_store(hass: HomeAssistant) -> Store:
    domain_data = _domain_data(hass)
    store = domain_data.get(_STORE_HANDLE_KEY)
    if store is None:
        store = Store(hass, _STORE_VERSION, _STORE_KEY)
        domain_data[_STORE_HANDLE_KEY] = store
    return store


async def _async_load_cache(hass: HomeAssistant) -> Dict[str, dict]:
    """Return the shared ``{vin: {...}}`` render cache, hydrating from Store once."""

    domain_data = _domain_data(hass)
    cache = domain_data.get(_CACHE_KEY)
    if cache is not None:
        return cache
    cache = {}
    store = await _async_get_store(hass)
    stored = await store.async_load()
    if stored:
        for vin, record in stored.items():
            data_b64 = record.get("data")
            if not data_b64:
                continue
            try:
                data = base64.b64decode(data_b64)
            except (ValueError, TypeError):
                continue
            cache[vin] = {
                "data": data,
                "content_type": record.get("content_type") or "image/png",
                "updated": record.get("updated"),
            }
    domain_data[_CACHE_KEY] = cache
    return cache


async def _async_persist_cache(hass: HomeAssistant) -> None:
    cache = _domain_data(hass).get(_CACHE_KEY) or {}
    store = await _async_get_store(hass)
    serialisable = {
        vin: {
            "data": base64.b64encode(record["data"]).decode("ascii"),
            "content_type": record.get("content_type") or "image/png",
            "updated": record.get("updated"),
        }
        for vin, record in cache.items()
        if record.get("data")
    }
    await store.async_save(serialisable)


async def async_refresh_vehicle_image(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: Any,
    vin: str,
) -> Optional[Tuple[bytes, Optional[str]]]:
    """Fetch the render for ``vin``, update the shared cache and notify entities.

    Handles the token refresh and quota claim itself (mirroring the read-only
    service handlers) so it can be called both from the ``fetch_vehicle_image``
    service and from the image entity's initial lazy load. Returns the
    ``(bytes, content_type)`` tuple on success, ``None`` otherwise.
    """

    # Imported lazily: the package __init__ pulls in Home Assistant and is always
    # loaded by the time a platform runs, but a top-level import would be circular.
    from . import (  # noqa: PLC0415
        CardataAuthError,
        CardataQuotaError,
        _refresh_tokens,
    )

    try:
        await _refresh_tokens(
            entry, runtime.session, runtime.stream, runtime.container_manager
        )
    except CardataAuthError as err:
        # A stale-but-valid token may still work; log and try the fetch anyway.
        _LOGGER.warning("Cardata image: token refresh failed for %s: %s", vin, err)

    access_token = entry.data.get("access_token")
    if not access_token:
        _LOGGER.error("Cardata image: no access token available for %s", vin)
        return None

    quota = runtime.quota_manager
    if quota:
        try:
            await quota.async_claim()
        except CardataQuotaError as err:
            _LOGGER.warning("Cardata image fetch for %s blocked: %s", vin, err)
            return None

    try:
        data, content_type = await async_get_vehicle_image(
            runtime.session, access_token, vin
        )
    except CardataApiError as err:
        _LOGGER.error("Cardata image fetch failed for %s: %s", vin, err)
        return None

    cache = await _async_load_cache(hass)
    cache[vin] = {
        "data": data,
        "content_type": content_type or "image/png",
        "updated": dt_util.utcnow().isoformat(),
    }
    await _async_persist_cache(hass)
    _LOGGER.info(
        "Cardata: cached vehicle render for %s (%s bytes, %s)",
        vin,
        len(data),
        content_type or "unknown type",
    )
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, SIGNAL_VEHICLE_IMAGE, vin)
    return data, content_type


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one vehicle-image entity per known VIN."""

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not runtime:
        return
    coordinator: CardataCoordinator = runtime.coordinator

    cache = await _async_load_cache(hass)
    entities: Dict[str, CardataVehicleImage] = {}

    @callback
    def ensure_entity(vin: str) -> None:
        if vin in entities:
            return
        entity = CardataVehicleImage(hass, coordinator, entry, runtime, vin, cache)
        entities[vin] = entity
        async_add_entities([entity])

    for vin in coordinator.data.keys():
        ensure_entity(vin)

    @callback
    def handle_new(vin: str, descriptor: str) -> None:
        ensure_entity(vin)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_sensor, handle_new)
    )


class CardataVehicleImage(CardataEntity, ImageEntity):
    """The BMW-provided vehicle render for a single VIN."""

    _attr_translation_key = "vehicle_image"
    _attr_name = None

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CardataCoordinator,
        entry: ConfigEntry,
        runtime: Any,
        vin: str,
        cache: Dict[str, dict],
    ) -> None:
        CardataEntity.__init__(self, coordinator, vin, "vehicle_image")
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._runtime = runtime
        self._cache = cache
        self._attr_unique_id = f"{vin}_vehicle_image"
        record = cache.get(vin)
        if record:
            self._attr_content_type = record.get("content_type") or "image/png"
            updated = record.get("updated")
            self._attr_image_last_updated = (
                dt_util.parse_datetime(updated) if updated else dt_util.utcnow()
            )
        else:
            self._attr_content_type = "image/png"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_VEHICLE_IMAGE, self._handle_image_signal
            )
        )
        if self._vin not in self._cache:
            # No render yet (fresh install / never fetched): pull it once in the
            # background so the card has a hero image without user action.
            self.hass.async_create_task(self._async_initial_fetch())

    async def _async_initial_fetch(self) -> None:
        await async_refresh_vehicle_image(
            self.hass, self._entry, self._runtime, self._vin
        )

    @callback
    def _handle_image_signal(self, vin: str) -> None:
        if vin != self._vin:
            return
        record = self._cache.get(vin)
        if record:
            self._attr_content_type = record.get("content_type") or "image/png"
            updated = record.get("updated")
            self._attr_image_last_updated = (
                dt_util.parse_datetime(updated) if updated else dt_util.utcnow()
            )
        self.async_write_ha_state()

    async def async_image(self) -> Optional[bytes]:
        record = self._cache.get(self._vin)
        if record:
            return record.get("data")
        result = await async_refresh_vehicle_image(
            self.hass, self._entry, self._runtime, self._vin
        )
        if result:
            return result[0]
        return None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        record = self._cache.get(self._vin)
        if record and record.get("updated"):
            attrs["image_fetched_at"] = record["updated"]
        return attrs
