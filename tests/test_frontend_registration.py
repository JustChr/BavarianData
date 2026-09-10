"""The bundled card must register cleanly when config entries set up concurrently.

Home Assistant sets up every config entry of a domain at the same time
(``asyncio.gather`` in ``homeassistant.setup``), so a user with two BMW accounts
runs ``_async_register_frontend_card`` twice, concurrently, on every restart. The
module-level ``_FRONTEND_REGISTERED`` flag has to hold across that. If it does not,
the second entry adds the same static route again, aiohttp rejects it, and that
entry's setup fails -- leaving one of the cars unavailable until a manual reload.

``__init__.py`` imports Home Assistant, so -- like the rest of this suite -- the
test does not import it. It compiles the real function out of the shipped source
and runs it against a stand-in ``hass`` whose ``http`` mirrors Home Assistant's
``async_register_static_paths``: an executor job first, then ``add_route`` on a
real aiohttp router, which raises exactly the error users see.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import logging
import os
import pathlib
import sys
import types
from dataclasses import dataclass

import pytest
from aiohttp import web

from .conftest import load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
_CONST = load_module("const")

CARD_URL = "/bavariandata/bavariandata-card.js"


@dataclass(slots=True)
class _StaticPathConfig:
    """Mirror of ``homeassistant.components.http.StaticPathConfig``."""

    url_path: str
    path: str
    cache_headers: bool = True


async def _serve(request):  # pragma: no cover - routes are never served here
    raise NotImplementedError


class _FakeHttp:
    """Mirrors ``HomeAssistantHTTP.async_register_static_paths``.

    Home Assistant awaits an executor job (building the static resources) before
    it touches the router -- that await is where a concurrent caller gets to run.
    """

    def __init__(self, hass: _FakeHass) -> None:
        self._hass = hass
        self.app = web.Application()

    async def async_register_static_paths(self, configs) -> None:
        await self._hass.async_add_executor_job(lambda: None)
        for config in configs:
            self.app.router.add_route("GET", config.url_path, _serve)


class _FakeHass:
    def __init__(self) -> None:
        self.http = _FakeHttp(self)
        self.data: dict = {}

    async def async_add_executor_job(self, target, *args):
        future = asyncio.get_running_loop().run_in_executor(None, target, *args)
        # A job on Home Assistant's busy executor hands its result back through
        # the event loop. A trivial job on a freshly spawned worker thread can
        # finish before it is awaited, and asyncio then resolves the await without
        # suspending -- hiding the interleaving this test exists to exercise.
        await asyncio.sleep(0)
        return await future


def _card_routes(hass: _FakeHass) -> list:
    return [
        route
        for route in hass.http.app.router.routes()
        if route.method == "GET" and route.resource.canonical == CARD_URL
    ]


@pytest.fixture
def ha_http(monkeypatch):
    """Provide ``homeassistant.components.http.StaticPathConfig`` without HA."""

    for name in ("homeassistant", "homeassistant.components"):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = []
            monkeypatch.setitem(sys.modules, name, package)
    http = types.ModuleType("homeassistant.components.http")
    http.StaticPathConfig = _StaticPathConfig
    monkeypatch.setitem(sys.modules, "homeassistant.components.http", http)


def _load_register_card() -> dict:
    """Compile the real flag + function from ``__init__.py`` into a fresh namespace.

    A fresh namespace per test means every test starts unregistered, exactly like
    a freshly started Home Assistant process.
    """

    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_FRONTEND_REGISTERED"
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_async_register_frontend_card"
        )
    ]
    assert len(wanted) == 2, "_FRONTEND_REGISTERED or _async_register_frontend_card moved"

    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )

    async def _no_lovelace_resource(hass, url):  # pragma: no cover - runs after start
        return False

    namespace: dict = {
        "__file__": str(_INIT),
        "os": os,
        "_LOGGER": logging.getLogger(__name__),
        "LOVELACE_CARD_FILENAME": _CONST.LOVELACE_CARD_FILENAME,
        "LOVELACE_CARD_URL": _CONST.LOVELACE_CARD_URL,
        "_integration_version": lambda: "0.0.0",
        # Publishing the Lovelace resource waits for HA to have started; the race
        # is over by then, so the tests only record that it was scheduled.
        "async_at_started": lambda hass, target: None,
        "_async_register_lovelace_resource": _no_lovelace_resource,
        "_async_add_frontend_module": lambda hass, url: None,
    }
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace


def test_concurrent_entry_setups_register_the_card_route_once(ha_http):
    register_card = _load_register_card()["_async_register_frontend_card"]
    hass = _FakeHass()

    async def two_entries_starting_together() -> None:
        # ``homeassistant.setup`` starts each entry's setup with
        # ``create_eager_task``, i.e. ``Task(coro, loop=loop, eager_start=True)``.
        loop = asyncio.get_running_loop()
        await asyncio.gather(
            *(asyncio.Task(register_card(hass), loop=loop, eager_start=True) for _ in range(2))
        )

    asyncio.run(two_entries_starting_together())

    assert len(_card_routes(hass)) == 1


def test_failed_registration_lets_the_next_setup_retry(ha_http):
    register_card = _load_register_card()["_async_register_frontend_card"]
    hass = _FakeHass()
    register_routes = hass.http.async_register_static_paths
    attempts = 0

    async def card_unreadable_on_first_attempt(configs) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("card file not readable")
        await register_routes(configs)

    hass.http.async_register_static_paths = card_unreadable_on_first_attempt

    with pytest.raises(OSError):
        asyncio.run(register_card(hass))
    # Reloading the entry runs its setup again; that must get a second chance.
    asyncio.run(register_card(hass))

    assert attempts == 2
    assert len(_card_routes(hass)) == 1
