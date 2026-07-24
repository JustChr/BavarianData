"""Persistence for the descriptor-coverage self-test.

Keeps two things across restarts:

* **the grace clock** -- when monitoring of the *current* cluster selection
  began, so "never arrived in N days" survives a Home Assistant restart rather
  than resetting to day zero every reboot; and
* **the seen-descriptor record** -- the first time each descriptor arrived per
  VIN, which outlives a manually disabled entity (whose descriptor would drop
  out of the live coordinator state) so a gap report stays truthful.

Changing the cluster selection restarts the grace clock: the expectations
changed, and the newly selected descriptors deserve their own grace window
before being called overdue. The pure analysis lives in :mod:`coverage`; this is
the only coverage module that imports Home Assistant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .coverage import DEFAULT_GRACE_DAYS, CoverageReport, analyze_coverage

try:  # normal case: imported as part of the package
    from .descriptors import descriptors_for_sections, section_labels
except ImportError:  # pragma: no cover - defensive, package import is the norm
    from descriptors import descriptors_for_sections, section_labels

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# Seen descriptors accrue fastest in the first minutes after setup and then
# almost never; a generous debounce keeps the write off the hot stream path.
SAVE_DELAY_S = 60


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CoverageStore:
    """Records which selected descriptors have streamed, and since when."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        sections: Iterable[str],
        grace_days: int = DEFAULT_GRACE_DAYS,
    ) -> None:
        self.hass = hass
        self.grace_days = grace_days
        self._sections: list[str] = list(sections or [])
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}_{entry_id}_coverage")
        self._seen: dict[str, dict[str, str]] = {}
        self._started_at: Optional[datetime] = None
        self._loaded = False

    async def async_load(self) -> None:
        """Read the store once at setup. Never raises -- coverage is not critical."""

        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a corrupt store must not block setup
            _LOGGER.exception("Could not read coverage store; starting fresh")
            data = None

        self._loaded = True
        changed = False
        if data:
            raw_seen = data.get("seen") or {}
            if isinstance(raw_seen, dict):
                for vin, descriptors in raw_seen.items():
                    if isinstance(descriptors, dict):
                        self._seen[vin] = {
                            str(k): str(v) for k, v in descriptors.items()
                        }
            self._started_at = _parse(data.get("started_at"))
            stored_sections = data.get("sections")
            if isinstance(stored_sections, list) and stored_sections != self._sections:
                # The selection changed while we were down: the grace clock is
                # tied to a selection, so restart it for the new expectations.
                self._started_at = None
                changed = True

        if self._started_at is None:
            self._started_at = datetime.now(timezone.utc)
            changed = True

        if changed:
            self.async_schedule_save()

    def update_sections(self, sections: Iterable[str]) -> None:
        """Adopt a new cluster selection (options reload), restarting the clock."""

        new_sections = list(sections or [])
        if new_sections == self._sections:
            return
        self._sections = new_sections
        self._started_at = datetime.now(timezone.utc)
        self.async_schedule_save()

    @callback
    def note_seen(self, vin: str, descriptors: Iterable[str]) -> None:
        """Record the first arrival of any not-yet-seen descriptors for ``vin``.

        Cheap and idempotent: only a genuinely new descriptor schedules a save,
        so the steady state (every descriptor already known) costs a set lookup.
        """

        if not vin:
            return
        seen = self._seen.setdefault(vin, {})
        stamp = datetime.now(timezone.utc).isoformat()
        added = False
        for descriptor in descriptors:
            if descriptor and descriptor not in seen:
                seen[descriptor] = stamp
                added = True
        if added:
            self.async_schedule_save()

    def _expected_by_section(self) -> dict[str, list[str]]:
        # The non-diagnostic set is exactly what the picker asks BMW to stream,
        # so it is what we are entitled to expect back.
        return {
            section: descriptors_for_sections([section]) for section in self._sections
        }

    def reports(
        self,
        live_descriptors: Optional[dict[str, Iterable[str]]] = None,
        *,
        now: Optional[datetime] = None,
    ) -> list[CoverageReport]:
        """Build a coverage report per known vehicle.

        ``live_descriptors`` folds in whatever the coordinator currently holds so
        a wiped store (or a descriptor seen before this store existed) still
        counts. Union with the persisted record keeps a manually disabled entity
        from re-opening a closed gap.
        """

        now = now or datetime.now(timezone.utc)
        live = {vin: set(items) for vin, items in (live_descriptors or {}).items()}
        expected = self._expected_by_section()
        labels = section_labels()
        vins = set(self._seen) | set(live)
        out: list[CoverageReport] = []
        for vin in sorted(vins):
            seen = set(self._seen.get(vin, {})) | live.get(vin, set())
            out.append(
                analyze_coverage(
                    vin=vin,
                    expected_by_section=expected,
                    labels=labels,
                    seen=seen,
                    monitoring_since=self._started_at,
                    now=now,
                    grace_days=self.grace_days,
                )
            )
        return out

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_S)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "sections": list(self._sections),
            "seen": {vin: dict(items) for vin, items in self._seen.items()},
        }

    async def async_save_now(self) -> None:
        """Flush immediately -- used on unload so a pending debounce isn't lost."""

        if self._loaded:
            await self._store.async_save(self._data_to_save())

    async def async_clear(self) -> None:
        """Delete the stored coverage record (integration removal)."""

        self._seen = {}
        self._started_at = None
        await self._store.async_remove()
