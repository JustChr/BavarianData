"""One field BMW won't serve must not cost a car its whole daily refresh.

BMW answers a container holding one field it won't serve with a bare
``CU-400`` for the whole request, naming no field. From 16 Oct 2026 it did so
for ``vehicle.status.conditionBasedServices``: bisected against the live API
(#142), that field alone was refused while the other 65 came back, and every
car -- combustion (#130), iX, i5 -- lost service items, door locks and
everything else BMW doesn't stream. v0.9.20-beta.6's fallback to a smaller
container never helped, because it held that field too.

So the service items live in a container of their own, fetched in the same
refresh, and either container failing costs only itself.

``_async_fetch_telematics`` lives in ``__init__.py``, which imports Home
Assistant, so it is compiled out of the shipped source and run against
stand-ins for the gateway and the entry.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import logging
import pathlib
import types
from datetime import datetime, timezone

from .conftest import FakeResponse, FakeSession, load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
container = load_module("container")
api = load_module("api")
const = load_module("const")
descriptors = load_module("descriptors")
rest = load_module("rest")
debug = load_module("debug")
device_flow = load_module("device_flow")

CBS = "vehicle.status.conditionBasedServices"
LOCKS = "vehicle.cabin.door.lock.status"
CAR = "WBA00000000000001"
OTHER = "WBA00000000000002"
REFUSED = api.CardataApiError("HTTP 400 (CU-400: Bad request)", status=400, error_id="CU-400")


# --- The containers ------------------------------------------------------------


def test_the_service_items_are_fetched_apart_from_everything_else():
    assert container.SERVICE.descriptors == (CBS,)
    assert CBS not in container.FULL.descriptors


def test_splitting_them_off_loses_no_field():
    before = set(descriptors.container_descriptors(const.HV_BATTERY_DESCRIPTORS))
    assert set(container.FULL.descriptors) | set(container.SERVICE.descriptors) == before


def test_the_containers_never_take_each_other_for_their_own():
    full, service = container.FULL, container.SERVICE
    assert service.name != full.name and service.purpose != full.purpose
    assert service.name not in full.retired_names
    assert service.id_key != full.id_key and service.signature_key != full.signature_key


def test_replacing_the_full_container_also_clears_the_retired_fallback():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "containers": [
                        {"containerId": "old-full", "name": container.FULL.name},
                        {"containerId": "beta6", "name": "BavarianData non-streamable"},
                        {"containerId": "service", "name": container.SERVICE.name},
                        {"containerId": "theirs", "name": "probe one field"},
                    ]
                },
            ),
            FakeResponse(201, {"containerId": "new-full"}),
            FakeResponse(204, None),
            FakeResponse(204, None),
        ]
    )
    manager = container.CardataContainerManager(session=session, entry_id="e")
    assert asyncio.run(manager.async_reset_hv_container("t")) == "new-full"
    deleted = [c["url"].rsplit("/", 1)[1] for c in session.calls if c["method"] == "DELETE"]
    assert deleted == ["old-full", "beta6"]


def test_the_service_container_is_created_under_its_own_name():
    session = FakeSession([FakeResponse(201, {"containerId": "s"})])
    manager = container.CardataContainerManager(
        session=session, entry_id="e", kind=container.SERVICE
    )
    asyncio.run(manager.async_ensure_hv_container("t"))
    body = session.calls[0]["json"]
    assert body["name"] == container.SERVICE.name
    assert body["technicalDescriptors"] == [CBS]


# --- The fetch -------------------------------------------------------------------


def _compile(names: tuple[str, ...], namespace: dict) -> dict:
    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names
    ]
    assert len(wanted) == len(names), f"one of {names} moved"
    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace


class _Bmw:
    """BMW as the account's cars see it: which (car, container) it refuses."""

    def __init__(self, refuses: set[tuple[str, str]] = frozenset()) -> None:
        self.refuses = set(refuses)
        self.sent: list[tuple[str, str]] = []

    async def call(self, request, vin, container_id):
        self.sent.append((vin, container_id))
        if (vin, container_id) in self.refuses:
            raise REFUSED
        field = CBS if container_id == "id-service" else LOCKS
        return {"telematicData": {field: {"value": "x"}}}


def _run(bmw: _Bmw, vins: list[str], *, missing=()):
    handled: list[tuple[str, tuple[str, ...]]] = []
    entry = types.SimpleNamespace(entry_id="e", data={})
    hass = types.SimpleNamespace()

    async def container_id(hass, entry, runtime, kind):
        return None if kind.key in missing else f"id-{kind.key}"

    async def handle(message):
        handled.append((message["vin"], tuple(message["data"])))

    coordinator = types.SimpleNamespace(
        hass=hass,
        data={},
        async_handle_message=handle,
        signal_diagnostics="sig",
        last_telematic_api_at=None,
    )
    runtime = types.SimpleNamespace(coordinator=coordinator, rest=bmw)
    namespace = _compile(
        ("_async_fetch_telematics",),
        {
            "_LOGGER": logging.getLogger(__name__),
            "_async_container_id": container_id,
            "async_get_telematic_data": object(),
            "async_dispatcher_send": lambda *args: None,
            "debug_enabled": lambda: False,
            "mask_vin": debug.mask_vin,
            "datetime": datetime,
            "timezone": timezone,
            "KINDS": container.KINDS,
            "SERVICE": container.SERVICE,
            "CardataApiError": api.CardataApiError,
            "CardataQuotaError": rest.CardataQuotaError,
            "CardataAuthError": device_flow.CardataAuthError,
        },
    )
    asked = asyncio.run(namespace["_async_fetch_telematics"](hass, entry, runtime, vins))
    return asked, handled


def test_every_car_reads_both_containers():
    bmw = _Bmw()
    asked, handled = _run(bmw, [CAR, OTHER])
    assert asked
    assert bmw.sent == [
        (CAR, "id-full"),
        (CAR, "id-service"),
        (OTHER, "id-full"),
        (OTHER, "id-service"),
    ]
    assert handled == [(CAR, (LOCKS,)), (CAR, (CBS,)), (OTHER, (LOCKS,)), (OTHER, (CBS,))]


def test_refused_service_items_cost_only_themselves(caplog):
    bmw = _Bmw(refuses={(CAR, "id-service")})
    with caplog.at_level(logging.WARNING):
        asked, handled = _run(bmw, [CAR, OTHER])
    assert asked
    assert handled == [(CAR, (LOCKS,)), (OTHER, (LOCKS,)), (OTHER, (CBS,))]
    assert "service items" in caplog.text and "the rest of the refresh is unaffected" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_failed_full_fetch_still_brings_the_service_items(caplog):
    bmw = _Bmw(refuses={(CAR, "id-full")})
    with caplog.at_level(logging.ERROR):
        _asked, handled = _run(bmw, [CAR])
    assert handled == [(CAR, (CBS,))]
    assert "request failed" in caplog.text


def test_a_missing_container_is_skipped_not_fatal():
    bmw = _Bmw()
    asked, handled = _run(bmw, [CAR], missing={"service"})
    assert asked
    assert bmw.sent == [(CAR, "id-full")]
    assert handled == [(CAR, (LOCKS,))]


def test_no_container_at_all_asks_bmw_nothing():
    bmw = _Bmw()
    asked, handled = _run(bmw, [CAR], missing={"full", "service"})
    assert not asked and not bmw.sent and not handled
