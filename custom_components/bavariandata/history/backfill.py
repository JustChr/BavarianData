"""Publish recorded history into Home Assistant's long-term statistics.

The second module in ``history/`` that imports Home Assistant (``store.py`` is
the other); the bucketing arithmetic it stands on lives in the HA-free
``stats.py`` so it stays unit-testable.

**Why external statistics rather than entity statistics.** The sensors this
integration already exposes generate their own statistics through the recorder
from the moment they exist. Backfill is about the opposite: charging that
happened *before* the integration was installed, or while Home Assistant was
down. Those hours have no entity state behind them, so they are published as
external statistics under our own ``bavariandata:`` namespace, where they can be
written for any point in time without fighting the recorder over an entity it
owns. External sum-statistics in kWh are selectable on the Energy dashboard as
an individual device, which is the point of the exercise.

**The statistics are a mirror of the store, not a second archive.** Every
rebuild clears our statistic ids and re-imports the whole series from the
records currently held, so the running sums are regenerated from zero each time.
That is what keeps them consistent once retention prunes the oldest records --
an incremental append would leave the sums stranded on a baseline whose records
no longer exist. It also means the retention setting genuinely governs *all* the
data we keep, which is the honest reading of the "Delete all history" button.

Rebuilds are gated on a fingerprint of the stored records, so a restart that
changes nothing does not churn the recorder's database.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..debug import debug_enabled
from .stats import cumulative, hourly_cost, hourly_distance, hourly_energy
from .store import HistoryStore

_LOGGER = logging.getLogger(__name__)

# Statistic id suffixes. Kept as constants because they are user-visible forever:
# renaming one orphans whatever the user selected on their Energy dashboard.
STAT_CHARGING_ENERGY = "charging_energy"
STAT_CHARGING_COST = "charging_cost"
STAT_DRIVING_DISTANCE = "driving_distance"

_KM = "km"
_KWH = "kWh"


def statistic_id(vin: str, suffix: str) -> str:
    """``bavariandata:charging_energy_wby...`` -- lowercase, no double underscore.

    Home Assistant validates external statistic ids against
    ``<source>:<object_id>`` with a restricted character set; a VIN is
    alphanumeric, so lowercasing is the only normalisation needed.
    """

    return f"{DOMAIN}:{suffix}_{vin.lower()}"


def _metadata(*, name: str, stat_id: str, unit: Optional[str]) -> dict[str, Any]:
    """Build ``StatisticMetaData`` across the ``has_mean``/``mean_type`` change.

    Home Assistant replaced the boolean ``has_mean`` with a ``mean_type`` enum;
    both spellings have shipped inside our supported range, so probe for the
    enum and fall back rather than pinning a minimum version for one field.
    """

    meta: dict[str, Any] = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": unit,
    }
    try:
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticMeanType,
        )
    except ImportError:  # pragma: no cover - older cores
        meta["has_mean"] = False
    else:
        meta["mean_type"] = StatisticMeanType.NONE
    return meta


def _fingerprint(store: HistoryStore, vin: str) -> tuple:
    """Cheap "has anything changed?" key for one vehicle.

    Count plus the oldest and newest record id catches every mutation that
    matters: a new record, an enrichment (which replaces by id), and retention
    dropping the oldest. Comparing full records would cost more than the rebuild.
    """

    sessions = store.sessions(vin)
    trips = store.trips(vin)

    def _edges(items: list) -> tuple:
        if not items:
            return ()
        return (items[0].id, items[-1].id)

    return (len(sessions), _edges(sessions), len(trips), _edges(trips))


class StatisticsPublisher:
    """Keeps one config entry's statistics in step with its history store."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: HistoryStore,
        *,
        name_for: Callable[[str], str],
        enabled: bool = True,
    ) -> None:
        self.hass = hass
        self.store = store
        self.enabled = enabled
        self._name_for = name_for
        self._fingerprints: dict[str, tuple] = {}

    @property
    def available(self) -> bool:
        """False when the recorder isn't set up -- statistics are its feature."""

        return "recorder" in self.hass.config.components

    def statistic_ids(self, vins: Optional[list[str]] = None) -> list[str]:
        vins = vins if vins is not None else self._vins()
        return [
            statistic_id(vin, suffix)
            for vin in vins
            for suffix in (
                STAT_CHARGING_ENERGY,
                STAT_CHARGING_COST,
                STAT_DRIVING_DISTANCE,
            )
        ]

    def _vins(self) -> list[str]:
        vins = {session.vin for session in self.store.sessions()}
        vins.update(trip.vin for trip in self.store.trips())
        return sorted(vins)

    async def async_publish(
        self, *, vin: Optional[str] = None, force: bool = False
    ) -> dict[str, int]:
        """Rebuild the statistics for one vehicle, or all of them.

        Returns the number of hourly rows written per statistic id, so the
        service can report something verifiable rather than "done".
        """

        written: dict[str, int] = {}
        if not self.enabled or not self.available:
            return written

        vins = [vin] if vin else self._vins()
        for target in vins:
            fingerprint = _fingerprint(self.store, target)
            if not force and self._fingerprints.get(target) == fingerprint:
                continue
            written.update(await self._async_publish_vin(target))
            self._fingerprints[target] = fingerprint

        if written and debug_enabled():
            _LOGGER.debug("[stats] published %s", written)
        return written

    async def _async_publish_vin(self, vin: str) -> dict[str, int]:
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            async_add_external_statistics,
        )

        sessions = self.store.sessions(vin)
        trips = self.store.trips(vin)
        cost_buckets, currency = hourly_cost(sessions)
        name = self._name_for(vin)

        series: list[tuple[str, str, Optional[str], list[dict[str, Any]]]] = [
            (
                STAT_CHARGING_ENERGY,
                f"{name} charging energy",
                _KWH,
                cumulative(hourly_energy(sessions)),
            ),
            (
                STAT_DRIVING_DISTANCE,
                f"{name} driving distance",
                _KM,
                cumulative(hourly_distance(trips)),
            ),
        ]
        # Cost only exists once a tariff has produced one, and only in a single
        # currency -- see ``stats.hourly_cost``.
        if currency:
            series.append(
                (
                    STAT_CHARGING_COST,
                    f"{name} charging cost",
                    currency,
                    cumulative(cost_buckets, precision=2),
                )
            )

        # Clear all three ids, not just the ones about to be rewritten: dropping
        # the tariff (or switching currency) removes the cost series from
        # ``series`` entirely, and a stale mirror of it must not survive.
        await self._async_clear(self.statistic_ids([vin]))

        written: dict[str, int] = {}
        for suffix, label, unit, rows in series:
            stat_id = statistic_id(vin, suffix)
            written[stat_id] = len(rows)
            if not rows:
                continue
            async_add_external_statistics(
                self.hass,
                _metadata(name=label, stat_id=stat_id, unit=unit),
                rows,
            )
        return written

    async def _async_clear(self, stat_ids: list[str]) -> None:
        """Drop our own statistic ids before re-importing the series.

        Never touches anything outside the ``bavariandata:`` namespace -- the
        ids are built by :func:`statistic_id`, not taken from user input.
        """

        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415

            get_instance(self.hass).async_clear_statistics(stat_ids)
        except Exception:  # noqa: BLE001 - statistics must never break setup
            _LOGGER.debug("Could not clear statistics %s", stat_ids, exc_info=True)

    async def async_remove(self) -> None:
        """Delete every statistic we published (option turned off, history wiped)."""

        if not self.available:
            return
        await self._async_clear(self.statistic_ids())
        self._fingerprints.clear()
