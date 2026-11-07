"""Optional reverse geocoding for trip endpoints outside any Home Assistant zone.

Off by default and never on the hot path: a trip's endpoint is resolved to a
zone name first (free, local, private), and only a point with *no* matching zone
is ever sent to OpenStreetMap's Nominatim -- and only when the user has turned
the feature on, because it means coordinates leave the box.

Privacy is preserved at rest: the address *string* is stored on the trip, never
the coordinates. The de-duplication cache is keyed by rounded coordinates but
lives only in memory (it is never persisted), so no coordinate ever reaches disk.

The address-formatting logic is a pure function so it is unit-tested; the network
call is deliberately thin and swallows every error (a missing address must never
break trip recording).

Only a real answer is remembered. A timeout, a network error, a busy (429) or
failing (5xx) server says nothing about the place, so it is retried once and then
left uncached: remembering it turned one bad moment into "Unknown" for every
later trip through the same spot until Home Assistant restarted (issue #294).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

# Nominatim's usage policy requires a real identifying User-Agent and no more
# than one request per second. We honour both; abusing the free service would
# get the whole integration's users blocked.
USER_AGENT = "BavarianData/HomeAssistant (https://github.com/JustChr/BavarianData)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
MIN_INTERVAL_S = 1.1
# ~110 m grid: fine enough that two ends of a street don't share a cache entry,
# coarse enough that repeat visits to the same place don't re-query.
CACHE_PRECISION = 3
REQUEST_TIMEOUT_S = 10
# One more try after a failure that says nothing about the place. The caller
# holds only its own car's trip lock while it waits.
RETRY_DELAY_S = 3.0
# Answers a retry can change: the request timed out or the server was busy.
_TRANSIENT_STATUSES = frozenset({408, 429})


class _Transient(Exception):
    """A lookup that failed for a reason unrelated to the place itself."""


def format_address(payload: Optional[dict[str, Any]]) -> Optional[str]:
    """Turn a Nominatim ``reverse`` response into a short label.

    Prefers "<road>, <city>" (or a named POI + city), falling back to whatever
    coarser field is present, and finally Nominatim's own ``display_name`` head.
    Returns ``None`` when there is nothing usable -- the caller then leaves the
    endpoint as an unknown place rather than inventing one.
    """

    if not payload:
        return None
    address = payload.get("address") or {}

    street = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("neighbourhood")
        or payload.get("name")
    )
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("suburb")
        or address.get("county")
    )

    if street and city:
        return f"{street}, {city}"
    if street or city:
        return street or city

    display = payload.get("display_name")
    if isinstance(display, str) and display:
        # display_name is a long comma-separated chain; the first two parts are
        # the most specific and enough for a legible label.
        return ", ".join(part.strip() for part in display.split(",")[:2])
    return None


class ReverseGeocoder:
    """Rate-limited, cached Nominatim reverse geocoder.

    Constructed with an aiohttp session (supplied by the coordinator) so this
    module keeps no Home Assistant dependency. ``enabled`` gates every call: when
    off, :meth:`resolve` is an immediate no-op returning ``None``.
    """

    def __init__(self, session: Any, *, enabled: bool = False) -> None:
        self._session = session
        self.enabled = enabled
        self._cache: dict[tuple[float, float], Optional[str]] = {}
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def resolve(self, latitude: float, longitude: float) -> Optional[str]:
        """Best-effort address string for a coordinate, or ``None``.

        Never raises: any network, timeout or parsing failure resolves to
        ``None`` so a trip is still recorded, just without an address.
        """

        if not self.enabled or self._session is None:
            return None

        key = (round(latitude, CACHE_PRECISION), round(longitude, CACHE_PRECISION))
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            # Re-check inside the lock: a concurrent close may have filled it.
            if key in self._cache:
                return self._cache[key]
            for attempt in range(2):
                if attempt:
                    await asyncio.sleep(RETRY_DELAY_S)
                await self._throttle()
                try:
                    address = await self._request(latitude, longitude)
                except _Transient:
                    continue
                self._cache[key] = address
                return address
            return None

    async def _throttle(self) -> None:
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def _request(self, latitude: float, longitude: float) -> Optional[str]:
        """The place's address, ``None`` for a definite "none here".

        Raises :class:`_Transient` when the answer says nothing about the place.
        """

        params = {
            "format": "jsonv2",
            "lat": f"{latitude:.5f}",
            "lon": f"{longitude:.5f}",
            "zoom": "16",
            "addressdetails": "1",
        }
        try:
            import aiohttp

            async with self._session.get(
                NOMINATIM_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
            ) as response:
                status = response.status
                if status != 200:
                    _LOGGER.debug("Nominatim returned HTTP %s", status)
                    if status in _TRANSIENT_STATUSES or status >= 500:
                        raise _Transient
                    return None
                payload = await response.json()
        except _Transient:
            raise
        except Exception as err:  # noqa: BLE001 - geocoding must never break recording
            _LOGGER.debug("Reverse geocode failed", exc_info=True)
            raise _Transient from err
        return format_address(payload)
