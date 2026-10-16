"""Image platform for BMW CarData.

Exposes the vehicle render served by BMW's ``/customers/vehicles/{vin}/image``
endpoint as a proper Home Assistant ``image`` entity so it can be used as the
hero image in the Lovelace card (or anywhere else).

The render is effectively static, and the CarData REST API only allows ~50 calls
per day, so the bytes are cached in memory *and* persisted to a Store. That way a
Home Assistant restart re-uses the stored render instead of spending a quota slot
on every boot; the picture is only (re)fetched when it is missing or when the
``fetch_vehicle_image`` service is called explicitly.

With Configure -> Vehicle image set to the configurator, the picture is instead
BMW's online configurator drawing the exact car (see ``configurator.py``): a
36-frame turntable kept as one sprite file under ``.storage`` and served to the
card through an authenticated view, a still frame as this entity's image, and
the sampled paint colour for the card's top-down car. The official picture
stays cached as the fallback.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import CardataApiError, async_get_vehicle_image
from .configurator import OPTION_COLUMNS, ConfiguratorError, async_render
from .const import (
    DEFAULT_IMAGE_SOURCE,
    DOMAIN,
    IMAGE_SOURCE_CONFIGURATOR,
    OPTION_IMAGE_SOURCE,
    SIGNAL_VEHICLE_IMAGE,
)
from .coordinator import CardataCoordinator
from .debug import mask_vin
from .entity import CardataEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}_vehicle_images"
# Where the shared image cache / store live on hass.data[DOMAIN].
_CACHE_KEY = "_vehicle_image_cache"
_STORE_HANDLE_KEY = "_vehicle_image_store"

# The configurator renders: metadata in a Store, the images as files next to it
# (a sprite is ~0.5-1 MB, too big to keep base64 in a JSON store).
_RENDER_STORE_KEY = f"{DOMAIN}_configurator_renders"
_RENDER_CACHE_KEY = "_configurator_render_cache"
_RENDER_STORE_HANDLE_KEY = "_configurator_render_store"
_RENDER_VIEW_KEY = "_configurator_render_view"
_RENDER_DIR = f"{DOMAIN}_renders"
SPIN_PATH = f"/api/{DOMAIN}/spin"
# A served file: the plain spin is the bare key, the rest carry a suffix.
_PARTS = ("lights", "night", "interior", "top", "options")
_NAME_RE = re.compile(r"[0-9a-f]{24}(?:_(?:lights|night|interior|top|options))?")
# Bumped when a render gains parts; an older record is refetched once in the
# background so cars drawn by an earlier build get them too. 3: the night spin
# is a cut-out (MINIs in their own night scene) on the card's backdrop.
_RENDER_SCHEMA = 3


def image_source(entry: ConfigEntry) -> str:
    """The picture source chosen under Configure -> Vehicle image."""

    return entry.options.get(OPTION_IMAGE_SOURCE, DEFAULT_IMAGE_SOURCE)


def _render_key(vin: str) -> str:
    """A file name for a car's render that doesn't spell out its VIN."""

    return hashlib.sha256(f"{DOMAIN}:render:{vin}".encode()).hexdigest()[:24]


def _render_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(".storage", _RENDER_DIR))


async def _async_load_renders(hass: HomeAssistant) -> Dict[str, dict]:
    """Return the shared ``{vin: {...}}`` configurator-render metadata."""

    domain_data = _domain_data(hass)
    renders = domain_data.get(_RENDER_CACHE_KEY)
    if renders is not None:
        return renders
    store = domain_data.get(_RENDER_STORE_HANDLE_KEY)
    if store is None:
        store = Store(hass, _STORE_VERSION, _RENDER_STORE_KEY)
        domain_data[_RENDER_STORE_HANDLE_KEY] = store
    stored = await store.async_load() or {}
    renders = {vin: dict(record) for vin, record in stored.items() if isinstance(record, dict)}
    domain_data[_RENDER_CACHE_KEY] = renders
    return renders


class _SpinView(HomeAssistantView):
    """Serves a car's configurator pictures to the card (signed or logged-in only)."""

    url = SPIN_PATH + "/{name}/{version}"
    name = f"api:{DOMAIN}:spin"
    requires_auth = True

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def get(self, request: web.Request, name: str, version: str) -> web.StreamResponse:
        # ``version`` only busts the browser cache after a refetch.
        if not _NAME_RE.fullmatch(name):
            return web.Response(status=404)
        for suffix in (".webp", ".png"):
            path = self._directory / f"{name}{suffix}"
            if await request.app["hass"].async_add_executor_job(path.is_file):
                return web.FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})
        return web.Response(status=404)


def _ensure_spin_view(hass: HomeAssistant) -> None:
    domain_data = _domain_data(hass)
    if not domain_data.get(_RENDER_VIEW_KEY):
        hass.http.register_view(_SpinView(_render_dir(hass)))
        domain_data[_RENDER_VIEW_KEY] = True


async def async_refresh_configurator_render(
    hass: HomeAssistant,
    runtime: Any,
    vin: str,
    *,
    full: bool = True,
) -> Optional[str]:
    """Draw ``vin`` with BMW's configurator and keep the result.

    ``full=False`` fetches only the plain turntable (about 5 s), for the
    settings screen; the full fetch (lights-on and night spins, interior, top
    view, equipment names; about a minute) then runs in the background. Spends
    no CarData quota. Returns ``None`` on success, otherwise the
    :class:`ConfiguratorError` reason (``unsupported``, ``no_data``,
    ``unavailable``, ``no_render``) so the settings screen can say why.
    """

    metadata = runtime.coordinator.device_metadata.get(vin) or {}
    raw = metadata.get("raw_data")
    if not raw:
        return "no_data"
    try:
        render = await async_render(
            runtime.session,
            raw,
            run_sync=hass.async_add_executor_job,
            languages=(hass.config.language,),
            extras=full,
        )
    except ConfiguratorError as err:
        _LOGGER.warning("Configurator picture for %s failed: %s", mask_vin(vin), err)
        return err.reason

    key = _render_key(vin)
    directory = _render_dir(hass)
    ext = ".webp" if render.sprite_type == "image/webp" else ".png"
    files = {key: render.sprite}
    if full:
        files.update(
            {f"{key}_{part}": getattr(render, part) for part in _PARTS if getattr(render, part)}
        )

    def _write() -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # A full fetch replaces everything; a quick one only the plain spin.
        stale = directory.glob(f"{key}*") if full else directory.glob(f"{key}.*")
        for path in stale:
            if not path.name.endswith("_still.png"):
                path.unlink()
        for name, data in files.items():
            # The top view and interior are saved in whatever format _save chose.
            (directory / f"{name}{_ext_of(data, ext)}").write_bytes(data)
        (directory / f"{key}_still.png").write_bytes(render.still)

    await hass.async_add_executor_job(_write)
    renders = await _async_load_renders(hass)
    previous = renders.get(vin) or {}
    record = {
        "key": key,
        "columns": render.columns,
        "rows": render.rows,
        "frames": render.frames,
        "frame_width": render.frame_width,
        "frame_height": render.frame_height,
        "paint": render.paint,
        "brand": render.brand,
        "updated": dt_util.utcnow().isoformat(),
        "schema": _RENDER_SCHEMA if full else previous.get("schema", 1),
        "parts": [part for part in _PARTS if f"{key}_{part}" in files],
    }
    if full:
        record["equipment"] = list(render.equipment)
        record["equipment_language"] = render.equipment_language
        if render.options:
            # Where each option's picture sits in the sheet, by option code.
            record["options"] = {
                "index": {code: n for n, code in enumerate(render.option_codes)},
                "columns": OPTION_COLUMNS,
                "rows": -(-len(render.option_codes) // OPTION_COLUMNS),
            }
    renders[vin] = record
    await _domain_data(hass)[_RENDER_STORE_HANDLE_KEY].async_save(renders)
    _LOGGER.info(
        "Configurator: drew %s as %s (%s frames, %s KB%s)",
        mask_vin(vin),
        render.brand,
        render.frames,
        len(render.sprite) // 1024,
        f"; also {', '.join(record['parts']) or 'nothing else'}, {len(render.equipment)} options"
        f" named, {len(render.option_codes)} pictured"
        if full
        else "",
    )
    async_dispatcher_send(hass, SIGNAL_VEHICLE_IMAGE, vin)
    return None


def async_schedule_full_render(hass: HomeAssistant, runtime: Any, vin: str) -> None:
    """Fetch the rest of the configurator's pictures without making anyone wait."""

    hass.async_create_background_task(
        async_refresh_configurator_render(hass, runtime, vin, full=True),
        f"{DOMAIN} configurator pictures {mask_vin(vin)}",
    )


def _ext_of(data: bytes, default: str) -> str:
    if data[:4] == b"RIFF":
        return ".webp"
    if data[:4] == bytes((0x89, 0x50, 0x4E, 0x47)):
        return ".png"
    return default


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
            except ValueError, TypeError:
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
        await _refresh_tokens(entry, runtime.session, runtime.stream, runtime.container_manager)
    except CardataAuthError as err:
        # A stale-but-valid token may still work; log and try the fetch anyway.
        _LOGGER.warning("Cardata image: token refresh failed for %s: %s", mask_vin(vin), err)

    access_token = entry.data.get("access_token")
    if not access_token:
        _LOGGER.error("Cardata image: no access token available for %s", mask_vin(vin))
        return None

    quota = runtime.quota_manager
    if quota:
        try:
            await quota.async_claim()
        except CardataQuotaError as err:
            _LOGGER.warning("Cardata image fetch for %s blocked: %s", mask_vin(vin), err)
            return None

    try:
        data, content_type = await async_get_vehicle_image(runtime.session, access_token, vin)
    except CardataApiError as err:
        _LOGGER.error("Cardata image fetch failed for %s: %s", mask_vin(vin), err)
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
        mask_vin(vin),
        len(data),
        content_type or "unknown type",
    )
    async_dispatcher_send(hass, SIGNAL_VEHICLE_IMAGE, vin)
    return data, content_type


async def async_official_fallback(
    hass: HomeAssistant, entry: ConfigEntry, runtime: Any, vin: str
) -> None:
    """Give a car the configurator can't draw its official picture.

    The image entity shows the kept official picture whenever there is no
    render; this fetches one (1 request) only for a car that has none yet.
    """

    if vin not in await _async_load_cache(hass):
        await async_refresh_vehicle_image(hass, entry, runtime, vin)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one vehicle-image entity per known VIN."""

    runtime = getattr(entry, "runtime_data", None)
    if not runtime:
        return
    coordinator: CardataCoordinator = runtime.coordinator

    cache = await _async_load_cache(hass)
    renders = await _async_load_renders(hass)
    _ensure_spin_view(hass)
    entities: Dict[str, CardataVehicleImage] = {}

    @callback
    def ensure_entity(vin: str) -> None:
        if vin in entities:
            return
        entity = CardataVehicleImage(hass, coordinator, entry, runtime, vin, cache, renders)
        entities[vin] = entity
        async_add_entities([entity])

    for vin in coordinator.data.keys():
        ensure_entity(vin)

    @callback
    def handle_new(vin: str, descriptor: str) -> None:
        ensure_entity(vin)

    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_new_sensor, handle_new))


class CardataVehicleImage(CardataEntity, ImageEntity):
    """The car's picture: BMW CarData's render, or the configurator's.

    Which one follows Configure -> Vehicle image. The configurator picture adds
    two attributes for the card: ``configurator_spin`` (the sprite's signed-URL
    path and grid) and ``paint_color``. Whenever there is no configurator
    render for the car, the official picture is shown.
    """

    _attr_translation_key = "vehicle_image"
    _attr_name = None
    # The equipment list runs to ~5 KB, and it, the picture paths and BMW's
    # basic data change only on a refetch: keep them out of the recorder's
    # history of this entity.
    _unrecorded_attributes = frozenset(
        {
            "equipment",
            "configurator_spin",
            "configurator_interior",
            "configurator_top",
            "configurator_options",
            "vehicle_basic_data",
            "vehicle_basic_data_raw",
        }
    )

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CardataCoordinator,
        entry: ConfigEntry,
        runtime: Any,
        vin: str,
        cache: Dict[str, dict],
        renders: Dict[str, dict],
    ) -> None:
        CardataEntity.__init__(self, coordinator, vin, "vehicle_image")
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._runtime = runtime
        self._cache = cache
        self._renders = renders
        self._still: Optional[bytes] = None
        self._attr_unique_id = f"{vin}_vehicle_image"
        self._sync_picture()

    def _render(self) -> Optional[dict]:
        """The configurator render, when that is the chosen source and one exists."""

        if image_source(self._entry) != IMAGE_SOURCE_CONFIGURATOR:
            return None
        return self._renders.get(self._vin)

    def _sync_picture(self) -> None:
        render = self._render()
        record = render or self._cache.get(self._vin)
        if render:
            self._attr_content_type = "image/png"
        elif record:
            self._attr_content_type = record.get("content_type") or "image/png"
        else:
            self._attr_content_type = "image/png"
        updated = record.get("updated") if record else None
        self._attr_image_last_updated = (
            dt_util.parse_datetime(updated) if updated else dt_util.utcnow()
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_VEHICLE_IMAGE, self._handle_image_signal)
        )
        if image_source(self._entry) == IMAGE_SOURCE_CONFIGURATOR:
            record = self._renders.get(self._vin)
            if record and record.get("schema", 1) < _RENDER_SCHEMA:
                # Drawn by an earlier build: fetch the newer parts once, quietly.
                async_schedule_full_render(self.hass, self._runtime, self._vin)
            if record is None:
                # A car added after the setting was chosen: draw it now (no quota).
                # Basic data may not be in yet; it arrives with the car's name.
                self.hass.async_create_task(self._async_initial_render())
                self.async_on_remove(
                    async_dispatcher_connect(
                        self.hass, f"{DOMAIN}_{self._entry.entry_id}_name", self._handle_name
                    )
                )
        elif self._vin not in self._cache:
            # No render yet (fresh install / never fetched): pull it once in the
            # background so the card has a hero image without user action.
            self.hass.async_create_task(self._async_initial_fetch())

    async def _async_initial_fetch(self) -> None:
        await async_refresh_vehicle_image(self.hass, self._entry, self._runtime, self._vin)

    async def _async_initial_render(self) -> None:
        reason = await async_refresh_configurator_render(self.hass, self._runtime, self._vin)
        # Without basic data it is tried again once the car's name arrives.
        if reason and reason != "no_data":
            await async_official_fallback(self.hass, self._entry, self._runtime, self._vin)

    @callback
    def _handle_name(self, vin: str, _name: str) -> None:
        if vin == self._vin and vin not in self._renders:
            self.hass.async_create_task(self._async_initial_render())

    @callback
    def _handle_image_signal(self, vin: str) -> None:
        if vin != self._vin:
            return
        self._still = None
        self._sync_picture()
        self.async_write_ha_state()

    async def async_image(self) -> Optional[bytes]:
        render = self._render()
        if render:
            if self._still is None:
                path = _render_dir(self.hass) / f"{render['key']}_still.png"
                try:
                    self._still = await self.hass.async_add_executor_job(path.read_bytes)
                except OSError as err:
                    _LOGGER.warning(
                        "Configurator still for %s unreadable: %s", mask_vin(self._vin), err
                    )
            if self._still is not None:
                return self._still
        record = self._cache.get(self._vin)
        if record:
            return record.get("data")
        result = await async_refresh_vehicle_image(self.hass, self._entry, self._runtime, self._vin)
        if result:
            return result[0]
        return None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(self._basic_data_attributes())
        record = self._cache.get(self._vin)
        if record and record.get("updated"):
            attrs["image_fetched_at"] = record["updated"]
        render = self._render()
        if render:
            version = re.sub(r"[^0-9]", "", render.get("updated") or "")[:14] or "0"
            key = render["key"]
            parts = set(render.get("parts") or ())

            def path(part: Optional[str] = None) -> str:
                return f"{SPIN_PATH}/{key}{'_' + part if part else ''}/{version}"

            spin = {
                "path": path(),
                "columns": render["columns"],
                "rows": render["rows"],
                "frames": render["frames"],
                "frame_width": render["frame_width"],
                "frame_height": render["frame_height"],
            }
            # Same frames, lights on / the night scene: the card swaps the sprite
            # and keeps the angle.
            for part in ("lights", "night"):
                if part in parts:
                    spin[f"{part}_path"] = path(part)
            attrs["configurator_spin"] = spin
            if "interior" in parts:
                # Six cube faces, 3 x 2, in BMW's display order.
                attrs["configurator_interior"] = {
                    "path": path("interior"),
                    "columns": 3,
                    "rows": 2,
                    "faces": ["down", "front", "back", "up", "left", "right"],
                }
            if "top" in parts:
                attrs["configurator_top"] = {"path": path("top")}
            if render.get("paint"):
                attrs["paint_color"] = render["paint"]
            if render.get("equipment"):
                attrs["equipment"] = render["equipment"]
                attrs["equipment_language"] = render.get("equipment_language")
            options = render.get("options")
            if "options" in parts and isinstance(options, dict):
                # One sheet of option pictures; the card finds a code's tile by index.
                attrs["configurator_options"] = {"path": path("options"), **options}
        return attrs
