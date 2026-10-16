"""A car's basic data must not depend on its first telematics fetch.

Basic data is what names a car and tells its drivetrain, steering side and
picture. The bootstrap used to ask for it only when the first telematics fetch
had delivered something, so a fresh install whose container BMW refused (#135's
CU-400, the 10-container limit, a spent quota) left its cars unnamed -- and
nothing asked again, because a car with a device is never "new". Now the
bootstrap always asks, and every start asks once for a car still without it.

``__init__.py`` imports Home Assistant, so the functions are compiled out of the
shipped source and run against stand-ins.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import logging
import pathlib
import types

from .conftest import load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
_CONST = load_module("const")
_DEVICE_FLOW = load_module("device_flow")
_VEHICLES = load_module("vehicles")

CAR_A = "WBA00000000000001"
CAR_B = "WBA00000000000002"


def _compile(name: str, namespace: dict):
    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    assert len(wanted) == 1, f"{name} moved"
    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace[name]


class _Devices:
    def __init__(self) -> None:
        self.created: list[str] = []

    def async_get_or_create(self, *, identifiers, **_kwargs) -> None:
        self.created.extend(value for _domain, value in identifiers)


def _bootstrap(*, telematics_ok: bool, mapping_error: Exception | None = None):
    calls: dict[str, list] = {"basic": [], "telematics": [], "complete": []}
    devices = _Devices()

    async def fetch_primary_vins(runtime, entry_id):
        if mapping_error is not None:
            raise mapping_error
        return [CAR_A, CAR_B]

    async def fetch_telematics(hass, entry, runtime, vins, *, label, seed):
        calls["telematics"].append(list(vins))
        return telematics_ok

    async def fetch_basic(hass, entry, vins):
        calls["basic"].append(list(vins))

    async def mark_complete(hass, entry):
        calls["complete"].append(True)

    namespace = {
        "_LOGGER": logging.getLogger(__name__),
        "DOMAIN": _CONST.DOMAIN,
        "time": __import__("time"),
        "CardataAuthError": _DEVICE_FLOW.CardataAuthError,
        "dr": types.SimpleNamespace(async_get=lambda hass: devices),
        "_async_fetch_primary_vins": fetch_primary_vins,
        "_async_fetch_telematics": fetch_telematics,
        "_async_fetch_basic_data_for_vins": fetch_basic,
        "_async_mark_bootstrap_complete": mark_complete,
        "_async_update_last_telematic_poll": lambda hass, entry, ts: None,
    }
    bootstrap = _compile("_async_run_bootstrap", namespace)
    coordinator = types.SimpleNamespace(data={}, names={})
    runtime = types.SimpleNamespace(coordinator=coordinator)
    entry = types.SimpleNamespace(entry_id="entry", runtime_data=runtime, data={})
    asyncio.run(bootstrap(None, entry))
    return calls, devices


def test_basic_data_is_fetched_even_when_the_telematics_fetch_fails():
    calls, devices = _bootstrap(telematics_ok=False)
    assert calls["basic"] == [[CAR_A, CAR_B]]
    assert calls["complete"] == [True]
    assert devices.created == [CAR_A, CAR_B]


def test_basic_data_is_fetched_after_a_working_telematics_fetch_too():
    calls, _devices = _bootstrap(telematics_ok=True)
    assert calls["telematics"] == [[CAR_A, CAR_B]]
    assert calls["basic"] == [[CAR_A, CAR_B]]


def test_without_a_token_the_bootstrap_waits_for_the_next_start():
    calls, _devices = _bootstrap(
        telematics_ok=True, mapping_error=_DEVICE_FLOW.CardataAuthError("invalid_grant")
    )
    assert calls == {"basic": [], "telematics": [], "complete": []}


def test_every_start_finds_the_cars_still_without_basic_data():
    namespace = {
        "known_vins": _VEHICLES.known_vins,
        "VEHICLE_METADATA": _CONST.VEHICLE_METADATA,
        # A car that only has a device: it streamed nothing since the restart.
        "_registered_vins": lambda hass, entry, exclude: [
            v for v in ("WBA00000000000003",) if v not in exclude
        ],
    }
    find = _compile("_vins_without_basic_data", namespace)
    entry = types.SimpleNamespace(data={_CONST.VEHICLE_METADATA: {CAR_A: {"brand": "BMW"}}})
    runtime = types.SimpleNamespace(coordinator=types.SimpleNamespace(data={CAR_A: {}, CAR_B: {}}))
    assert sorted(find(None, entry, runtime)) == [CAR_B, "WBA00000000000003"]
