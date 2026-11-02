"""BMW's REST quota: 50 requests a day, per account.

The day is BMW's: its counter resets at 00:00 UTC (BMW support, Nov 2026),
not 24 hours after each request.

Every request claims a slot first (rest.py's ``RestGateway``); a claim with no
slot left raises :class:`~.rest.CardataQuotaError` instead of asking BMW. The
claims are kept on disk, so a restart does not hand the account a fresh 50.

Home Assistant-free: ``store`` is anything with ``async_load`` and
``async_save`` (HA's ``Store`` in ``__init__.py``), and the repair issue that
says the quota is spent comes and goes through two callbacks.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Iterable, Optional

from .const import REQUEST_LIMIT
from .rest import CardataQuotaError


def parse_timestamps(raw: Any) -> Deque[float]:
    """The stored claims as epoch seconds, oldest first.

    Numbers, numeric strings and ISO 8601 strings (an older format) are read;
    anything else is dropped rather than failing the entry's setup.
    """

    values: list[float] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        value: Optional[float] = None
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            value = float(item)
        elif isinstance(item, str):
            try:
                value = float(item)
            except TypeError, ValueError:
                try:
                    value = datetime.fromisoformat(item.replace("Z", "+00:00")).timestamp()
                except TypeError, ValueError:
                    value = None
        if value is not None:
            values.append(value)
    return deque(sorted(values))


def day_start(now: float) -> float:
    """The last 00:00 UTC at or before ``now``, when BMW last reset the count."""

    return now - now % 86400


class QuotaManager:
    """Count the day's requests against BMW's limit."""

    def __init__(
        self,
        store: Any,
        timestamps: Iterable[float] = (),
        *,
        on_exhausted: Callable[[str], None] = lambda _reset: None,
        on_available: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._timestamps: Deque[float] = deque(timestamps)
        self._on_exhausted = on_exhausted
        self._on_available = on_available
        self._clock = clock
        self._lock = asyncio.Lock()

    @classmethod
    async def async_create(cls, store: Any, **kwargs: Any) -> "QuotaManager":
        """Load the stored claims, drop the expired ones and save the rest."""

        data = await store.async_load() or {}
        manager = cls(store, parse_timestamps(data.get("timestamps")), **kwargs)
        async with manager._lock:
            manager._prune(manager._clock())
            await manager._async_save_locked()
        return manager

    def _prune(self, now: float) -> None:
        cutoff = day_start(now)
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    async def async_claim(self) -> None:
        """Claim a request, or raise ``CardataQuotaError`` when none is left."""

        async with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._timestamps) >= REQUEST_LIMIT:
                self._on_exhausted(self.next_reset_iso or "00:00 UTC")
                raise CardataQuotaError(
                    "BMW CarData API limit reached; try again after quota resets"
                )
            self._timestamps.append(now)
            # A successful claim means we're back under the limit; clear any
            # exhaustion repair that a previous window raised.
            self._on_available()
            await self._async_save_locked()

    @property
    def used(self) -> int:
        self._prune(self._clock())
        return len(self._timestamps)

    @property
    def remaining(self) -> int:
        return max(0, REQUEST_LIMIT - self.used)

    @property
    def next_reset_epoch(self) -> Optional[float]:
        self._prune(self._clock())
        if len(self._timestamps) < REQUEST_LIMIT:
            return None
        return day_start(self._clock()) + 86400

    @property
    def next_reset_iso(self) -> Optional[str]:
        ts = self.next_reset_epoch
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    async def async_close(self) -> None:
        async with self._lock:
            self._prune(self._clock())
            await self._async_save_locked()

    async def _async_save_locked(self) -> None:
        await self._store.async_save({"timestamps": list(self._timestamps)})
