"""Send each car's live state to ABRP while it is switched on.

The Home Assistant half of the ABRP push; the point, the settings and the HTTP
call live in the HA-free ``abrp.py``.

Sent when something changes, a moment after a burst of messages settles, and
again every ``HEARTBEAT_S`` with the last known values: Iternio wants a point
every few seconds and treats a car that falls silent as gone, while BMW's
stream says nothing for hours on a parked car. Switching a car off simply
stops: ABRP holds nothing of ours that would need deleting.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .abrp import AbrpConfig, SendResult, abrp_snapshot, async_send, telemetry
from .debug import debug_enabled

_LOGGER = logging.getLogger(__name__)

# Bursts of descriptor messages become one point, as on the MQTT bridge.
SEND_DEBOUNCE_S = 2
# How often the last known values are repeated. Iternio advises against gaps
# over 30 s; a few small HTTPS requests a minute per car.
HEARTBEAT_S = 30
HEARTBEAT = timedelta(seconds=HEARTBEAT_S)


class AbrpPush:
    """Keeps ABRP's view of each switched-on car current."""

    def __init__(self, hass: HomeAssistant, coordinator: Any, *, config: AbrpConfig) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._config = config
        self._timers: dict[str, Any] = {}
        # Cars whose token ABRP refused, so the log says so once, not twice a
        # minute. Cleared when the settings change.
        self._refused: set[str] = set()
        self._closed = False

    @property
    def config(self) -> AbrpConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.any_enabled and not self._closed

    @callback
    def apply_config(self, config: AbrpConfig) -> None:
        """Adopt changed settings in place (a reload would restart the stream)."""

        self._config = config
        self._refused.clear()

    @callback
    def async_schedule(self, vin: Optional[str] = None) -> None:
        """Queue a send for one car (or all), coalescing a burst."""

        if not self.enabled:
            return
        vins = [vin] if vin else list(self._coordinator.data.keys())
        for target in vins:
            if not target or target in self._timers or not self._config.sends(target):
                continue

            @callback
            def _fire(_now: Any, target: str = target) -> None:
                self._timers.pop(target, None)
                self.hass.async_create_task(self.async_send(target), eager_start=False)

            self._timers[target] = async_call_later(self.hass, SEND_DEBOUNCE_S, _fire)

    @callback
    def async_shutdown(self) -> None:
        self._closed = True
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    async def async_send_all(self) -> None:
        """The heartbeat."""

        if not self.enabled:
            return
        for vin in list(self._coordinator.data.keys()):
            await self.async_send(vin)

    async def async_send(self, vin: str) -> None:
        """Send one car's point now, if it is switched on and has a charge level."""

        if not self.enabled or not self._config.sends(vin):
            return
        car = self._config.car(vin)
        point = telemetry(
            abrp_snapshot(self._coordinator, vin), now=dt_util.utcnow(), position=car.position
        )
        if point is None:
            return
        result = await async_send(
            async_get_clientsession(self.hass),
            key=self._config.api_key,
            token=car.token,
            point=point,
        )
        if result is SendResult.REJECTED:
            if vin not in self._refused:
                self._refused.add(vin)
                _LOGGER.warning(
                    "ABRP refused the car token or the API key, so nothing reaches "
                    "ABRP. Check the token in Configure > Share live data > ABRP."
                )
            return
        if result is SendResult.OK:
            self._refused.discard(vin)
        if debug_enabled():
            _LOGGER.debug("[abrp] %s sent %s: %s", vin, sorted(point), result.value)
