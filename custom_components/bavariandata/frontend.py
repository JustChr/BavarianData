"""The bundled dashboard card: served once per Home Assistant run and registered as a Lovelace resource."""

from __future__ import annotations

import json
import logging
import os

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started

from .const import (
    LOVELACE_CARD_FILENAME,
    LOVELACE_CARD_URL,
)

_LOGGER = logging.getLogger(__name__)


_FRONTEND_REGISTERED = False


async def async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and make dashboards load it.

    Runs once per Home Assistant process (guarded by a module-level flag so it
    survives config-entry reloads) making the ``custom:bavariandata-card`` element
    available without the user manually adding a dashboard resource.

    The card is published as a **Lovelace resource** rather than through
    ``add_extra_js_url``. The latter renders a bare, unawaited ``import()`` into
    the index HTML, and the frontend's service worker can serve that page from
    cache without it — leaving every placed card stuck on "Configuration error"
    after a plain reload until the browser session is restarted
    (home-assistant/frontend#18728). Resources travel with the Lovelace config
    instead and are loaded before any dashboard renders.
    """

    global _FRONTEND_REGISTERED
    if _FRONTEND_REGISTERED:
        return

    card_path = os.path.join(os.path.dirname(__file__), "www", LOVELACE_CARD_FILENAME)
    if not os.path.isfile(card_path):
        _LOGGER.warning("Cardata: bundled Lovelace card missing at %s", card_path)
        return

    from homeassistant.components.http import StaticPathConfig

    # Claim before the first await. Home Assistant sets up every entry of this
    # domain concurrently, so with two accounts a second entry would otherwise
    # pass the check above while the first is still registering, add the same
    # route again and fail its setup.
    _FRONTEND_REGISTERED = True
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(LOVELACE_CARD_URL, card_path, cache_headers=False)]
        )
    except BaseException:
        # Release the claim so reloading the entry can try again. BaseException,
        # not Exception: a setup cancelled mid-await must not leave the flag set,
        # or the card stays unserved until Home Assistant restarts.
        _FRONTEND_REGISTERED = False
        raise

    # Cache-bust on integration version so browsers pick up card updates.
    # Read the manifest off the event loop — open() is a blocking call.
    version = await hass.async_add_executor_job(_integration_version)
    url = f"{LOVELACE_CARD_URL}?v={version}"

    # ``hass.data["lovelace"]`` only exists once the lovelace integration has set
    # itself up, which is not guaranteed while our entry is starting.
    async def _publish(_hass: HomeAssistant) -> None:
        if not await _async_register_lovelace_resource(hass, url):
            _async_add_frontend_module(hass, url)

    async_at_started(hass, _publish)
    _LOGGER.debug("Cardata: serving Lovelace card at %s", url)


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add or refresh the card in Lovelace's resource collection.

    Returns ``True`` once the resource is registered. ``False`` means Lovelace is
    unavailable or its resources are YAML-managed (the collection is read-only
    then), so the caller should fall back to the frontend-module path.
    """

    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        _LOGGER.debug("Cardata: lovelace resources unavailable; using frontend module")
        return False

    # HA 2026.2 split the resource mode off ``mode`` so YAML dashboards can still
    # manage resources through the UI. Prefer the newer attribute when present.
    mode = getattr(lovelace, "resource_mode", None) or getattr(lovelace, "mode", None)
    if mode != "storage":
        _LOGGER.debug("Cardata: lovelace resources are %s-managed; using frontend module", mode)
        return False

    # The collection is loaded lazily — async_items() stays empty until it is.
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True

    try:
        for item in resources.async_items():
            # Match on the path so an older ?v= from a previous version is found.
            if str(item.get("url", "")).split("?", 1)[0] != LOVELACE_CARD_URL:
                continue
            if item.get("url") != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                _LOGGER.debug("Cardata: updated Lovelace resource to %s", url)
            return True

        await resources.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001 - never block setup over a dashboard resource
        _LOGGER.exception("Cardata: could not register Lovelace resource")
        return False

    _LOGGER.debug("Cardata: registered Lovelace resource %s", url)
    return True


@callback
def _async_add_frontend_module(hass: HomeAssistant, url: str) -> None:
    """Load the card as a frontend module (fallback for YAML-managed resources).

    Subject to frontend#18728 — the card may need a fresh browser session after a
    reload — but it is the only automatic option when resources are not in
    storage mode.
    """

    try:
        from homeassistant.components.frontend import add_extra_js_url
    except ImportError:  # pragma: no cover - frontend always present in practice
        _LOGGER.debug("Cardata: frontend component unavailable; card not auto-loaded")
        return

    add_extra_js_url(hass, url)
    _LOGGER.debug("Cardata: added frontend module %s", url)


def _integration_version() -> str:
    try:
        manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            return str(json.load(handle).get("version", "0"))
    except OSError, ValueError:
        return "0"
