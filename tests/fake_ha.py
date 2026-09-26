"""Just enough of Home Assistant to run the real coordinator in a test.

Home Assistant cannot be imported on Windows at all (a bare ``import fcntl`` in
``homeassistant/runner.py``), so the coordinator -- the integration's central
state machine -- could never be executed by this suite. It only needs seven
names from Home Assistant, though, and each is small enough to stand in for
faithfully:

=====================================  =========================================
Home Assistant                          Stand-in
=====================================  =========================================
``core.HomeAssistant`` / ``callback``   a marker class / identity decorator
``helpers.dispatcher``                  records every signal on the ``FakeHass``
``helpers.event.async_call_later``      a timer on the virtual clock
``helpers.issue_registry``              records raised/cleared repair issues
``helpers.storage.Store``               an in-memory store on the ``FakeHass``
``util.dt``                             the few helpers the code calls
=====================================  =========================================

The stubs are installed only while the integration's modules are imported (the
same pattern as ``test_stream_reconnect.py``) and forward to whichever
``FakeHass`` a test created, so every test gets fresh state and nothing leaks
into the HA-free tests. Keep them this dumb: a stand-in that grows behaviour of
its own starts testing itself instead of the integration.
"""

from __future__ import annotations

import math
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pytest

from .conftest import load_module

UTC = timezone.utc


class Clock:
    """The one clock every patched module reads."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def clocked_datetime(clock: Clock) -> type:
    """A ``datetime`` whose ``now()`` is the virtual clock, for module patching."""

    class _Clocked(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 - mirrors datetime.now
            value = clock.now
            return value if tz is None else value.astimezone(tz)

        @classmethod
        def utcnow(cls):
            return clock.now.replace(tzinfo=None)

    return _Clocked


@dataclass
class _Timer:
    due: datetime
    callback: Callable[[datetime], Any]
    cancelled: bool = False


@dataclass
class FakeHass:
    """The slice of ``HomeAssistant`` the coordinator touches."""

    clock: Clock
    events: list[tuple[str, dict]] = field(default_factory=list)
    signals: list[tuple[str, tuple]] = field(default_factory=list)
    issues: dict[str, dict] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    timers: list[_Timer] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    # Zones for ``homeassistant.components.zone.async_active_zone``.
    zones: list[Any] = field(default_factory=list)
    tasks: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.bus = types.SimpleNamespace(async_fire=self._fire)
        # ``zone_at`` and friends read entity states; no zones exist here.
        self.states = types.SimpleNamespace(get=lambda entity_id: None, async_all=lambda *a: [])
        self.config = types.SimpleNamespace(latitude=None, longitude=None, config_dir=".")

    def _fire(self, event_type: str, data: Optional[dict] = None, **_kw) -> None:
        self.events.append((event_type, dict(data or {})))

    def async_create_task(self, coro, *_a, **_kw):
        # Queued, and run by the harness right after whatever scheduled it --
        # what HA's event loop does on its next turn. Never dropped: work the
        # coordinator hands off must not silently vanish in a test.
        self.tasks.append(coro)

    def call_later(self, delay: float, callback: Callable[[datetime], Any]) -> Callable[[], None]:
        timer = _Timer(self.clock.now + timedelta(seconds=delay), callback)
        self.timers.append(timer)

        def cancel() -> None:
            timer.cancelled = True

        return cancel

    def due_timers(self, until: datetime) -> list[_Timer]:
        due = sorted(
            (t for t in self.timers if not t.cancelled and t.due <= until), key=lambda t: t.due
        )
        for timer in due:
            timer.cancelled = True  # a timer fires once
        return due

    def event_types(self) -> list[str]:
        return [name for name, _data in self.events]

    def add_zone(self, name: str, latitude: float, longitude: float, radius_m: float = 100) -> None:
        self.zones.append(
            types.SimpleNamespace(
                name=name,
                entity_id=f"zone.{name.lower()}",
                latitude=latitude,
                longitude=longitude,
                radius=radius_m,
            )
        )


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.radians
    a = (
        math.sin(rad(lat2 - lat1) / 2) ** 2
        + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(rad(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _active_zone(hass: FakeHass, latitude: float, longitude: float, radius: float = 0):
    """Smallest zone containing the point, as HA picks it."""

    inside = [
        zone
        for zone in hass.zones
        if _distance_m(latitude, longitude, zone.latitude, zone.longitude) <= zone.radius
    ]
    return min(inside, key=lambda zone: zone.radius, default=None)


class _Store:
    """``helpers.storage.Store`` backed by ``FakeHass.storage``."""

    def __init__(self, hass: FakeHass, version: int, key: str, **_kw) -> None:
        self._hass = hass
        self._key = key

    async def async_load(self):
        return self._hass.storage.get(self._key)

    async def async_save(self, data) -> None:
        self._hass.storage[self._key] = data

    def async_delay_save(self, data_func: Callable[[], Any], delay: float = 0) -> None:
        # Written at once: a delayed save that a restart can outrun is the
        # production risk the flush-on-stop path exists for, and the harness
        # models that path explicitly (see ``CoordinatorHarness.restart``).
        self._hass.storage[self._key] = data_func()

    async def async_remove(self) -> None:
        self._hass.storage.pop(self._key, None)


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stub_modules() -> dict[str, types.ModuleType]:
    def module(name: str, **attrs) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        return mod

    def dispatcher_send(hass, signal, *args) -> None:
        hass.signals.append((signal, args))

    def call_later(hass, delay, callback):
        return hass.call_later(delay, callback)

    def create_issue(hass, domain, issue_id, **kwargs) -> None:
        hass.issues[issue_id] = kwargs

    def delete_issue(hass, domain, issue_id) -> None:
        hass.issues.pop(issue_id, None)

    dt = module(
        "homeassistant.util.dt",
        parse_datetime=_parse_datetime,
        utcnow=lambda: datetime.now(UTC),
        as_utc=lambda value: value.astimezone(UTC),
        as_local=lambda value: value,
        UTC=UTC,
    )
    package = module("homeassistant")
    package.__path__ = []
    components = module("homeassistant.components")
    components.__path__ = []
    zone = module("homeassistant.components.zone", async_active_zone=_active_zone)
    components.zone = zone
    return {
        "homeassistant": package,
        "homeassistant.components": components,
        "homeassistant.components.zone": zone,
        "homeassistant.core": module(
            "homeassistant.core", HomeAssistant=FakeHass, callback=lambda func: func
        ),
        "homeassistant.helpers": module("homeassistant.helpers"),
        "homeassistant.helpers.dispatcher": module(
            "homeassistant.helpers.dispatcher", async_dispatcher_send=dispatcher_send
        ),
        "homeassistant.helpers.event": module(
            "homeassistant.helpers.event", async_call_later=call_later
        ),
        "homeassistant.helpers.issue_registry": module(
            "homeassistant.helpers.issue_registry",
            IssueSeverity=types.SimpleNamespace(WARNING="warning", ERROR="error"),
            async_create_issue=create_issue,
            async_delete_issue=delete_issue,
        ),
        "homeassistant.helpers.storage": module("homeassistant.helpers.storage", Store=_Store),
        "homeassistant.util": module("homeassistant.util", dt=dt),
        "homeassistant.util.dt": dt,
    }


def install_fake_ha(patch: pytest.MonkeyPatch) -> None:
    """Keep the stand-ins importable while a harness runs.

    A few integration paths import Home Assistant lazily, at call time -- the
    zone lookup does -- so the stubs have to outlive the module import.
    """

    for mod_name, mod in _stub_modules().items():
        patch.setitem(sys.modules, mod_name, mod)


def load_with_fake_ha(name: str):
    """Import an HA-coupled integration module against the stand-ins above.

    The stubs are only in ``sys.modules`` for the duration of the import; the
    module keeps its references to them, so they forward to the ``FakeHass``
    each call is given.
    """

    with pytest.MonkeyPatch.context() as patch:
        for mod_name, mod in _stub_modules().items():
            patch.setitem(sys.modules, mod_name, mod)
        return load_module(name)
