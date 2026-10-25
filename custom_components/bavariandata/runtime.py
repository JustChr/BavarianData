"""What a loaded account holds while it runs: ``entry.runtime_data``."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
from homeassistant.config_entries import ConfigEntry

from .bridge import VehicleBridge
from .container import (
    CardataContainerManager,
)
from .coordinator import CardataCoordinator
from .coverage_store import CoverageStore
from .history.backfill import StatisticsPublisher
from .history.store import HistoryStore
from .quota import QuotaManager
from .reauth import ReauthGate
from .rest import RestGateway, TokenKeeper
from .stream import CardataStreamManager
from .tyre_store import TyreStore

_LOGGER = logging.getLogger(__name__)


@dataclass
class CardataRuntimeData:
    stream: CardataStreamManager
    refresh_task: asyncio.Task[Any]
    session: aiohttp.ClientSession
    coordinator: CardataCoordinator
    # The entry's containers by ``ContainerKind.key`` (container.py).
    containers: dict[str, CardataContainerManager]
    # Set to have the token-refresh loop refresh now instead of at its next turn.
    refresh_wake: asyncio.Event
    # The way into BMW's REST API (rest.py): every request takes its token from
    # ``tokens`` and its quota slot through ``rest``.
    tokens: TokenKeeper
    rest: RestGateway
    history: Optional[HistoryStore] = None
    statistics: Optional[StatisticsPublisher] = None
    coverage: Optional[CoverageStore] = None
    tyre: Optional[TyreStore] = None
    bridge: Optional[VehicleBridge] = None
    bootstrap_task: asyncio.Task[Any] | None = None
    quota_manager: QuotaManager | None = None
    telematic_task: asyncio.Task[Any] | None = None
    # Whether a reauth flow is open, and when we last tried (reauth.py).
    reauth: ReauthGate = field(default_factory=ReauthGate)
    # VINs whose basic data has been fetched (or attempted) since this entry
    # loaded, so a car that BMW answers nothing for cannot spend a quota
    # request on every stream message. See async_on_new_vehicle.
    basic_data_attempted: set[str] = field(default_factory=set)
    # Held while the container is checked and, if need be, created or replaced,
    # so two fetches at once cannot both create one.
    container_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Per-entry runtime lives on ``entry.runtime_data`` (HA 2026.3+), so a typed
# ConfigEntry alias gives callers ``entry.runtime_data`` with the right type and
# removes the old ``hass.data[DOMAIN][entry.entry_id]`` lookups. Process-wide
# state (registered services, the shared vehicle-image cache) still lives on
# ``hass.data[DOMAIN]``.
CardataConfigEntry = ConfigEntry[CardataRuntimeData]
