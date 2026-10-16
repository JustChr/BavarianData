"""One refused field must not cost a car its whole daily refresh (#135).

BMW answers a container it won't serve for a car with a bare ``CU-400`` for the
whole request, naming no field. In #130 it started refusing, one morning, any
container that holds both ``charging.method`` and ``conditionBasedServices`` for
two UK combustion cars, while an i5 fetched the same container fine. The cars
lost service items, door locks and everything else BMW doesn't stream.

Now a car BMW refuses the full set for is fetched from a second container that
holds only what BMW cannot stream -- at once, and from then on -- and a car
known to have no battery uses that one from the start.

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

import pytest

from .conftest import FakeResponse, FakeSession, load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
container = load_module("container")
api = load_module("api")
rest = load_module("rest")
debug = load_module("debug")
device_flow = load_module("device_flow")

PETROL = "WBA00000000000001"
EV = "WBA00000000000002"
REFUSED = api.CardataApiError("HTTP 400 (CU-400: Bad request)", status=400, error_id="CU-400")


# --- The rule --------------------------------------------------------------


def test_the_base_container_holds_only_what_bmw_cannot_stream():
    meta = load_module("descriptor_metadata").DESCRIPTOR_META
    assert set(container.BASE.descriptors) < set(container.FULL.descriptors)
    assert all(not meta[d].get("streamable", True) for d in container.BASE.descriptors)
    # The fields of George's refused pair: the EV one is gone, the service list stays.
    assert "vehicle.drivetrain.electricEngine.charging.method" not in container.BASE.descriptors
    assert "vehicle.status.conditionBasedServices" in container.BASE.descriptors


def test_the_two_containers_never_take_each_other_for_their_own():
    assert container.BASE.name != container.FULL.name
    assert container.BASE.purpose != container.FULL.purpose
    assert container.BASE.id_key != container.FULL.id_key
    assert container.BASE.signature_key != container.FULL.signature_key


@pytest.mark.parametrize(
    ("status", "error_id", "refused"),
    [
        (400, "CU-400", True),
        (400, None, True),
        (400, "CU-121", False),  # malformed container id: a reset's job
        (404, "CU-105", False),
        (429, "CU-429", False),
        (None, None, False),  # network
    ],
)
def test_only_a_bare_bad_request_is_a_refusal_of_the_container(status, error_id, refused):
    assert container.refused_whole(status, error_id) is refused


def test_a_car_uses_the_base_container_once_refused_or_known_to_burn_fuel():
    def petrol(vin):
        return vin == PETROL

    assert container.kind_for(EV, refused=(), combustion_only=petrol) is container.FULL
    assert container.kind_for(PETROL, refused=(), combustion_only=petrol) is container.BASE
    assert container.kind_for(EV, refused=[EV], combustion_only=petrol) is container.BASE


def test_replacing_the_full_container_leaves_the_base_one_alone():
    session = FakeSession(
        [
            FakeResponse(
                200,
                [
                    {"containerId": "old-full", "name": container.FULL.name},
                    {"containerId": "base", "name": container.BASE.name},
                ],
            ),
            FakeResponse(201, {"containerId": "new-full"}),
            FakeResponse(204, None),
        ]
    )
    manager = container.CardataContainerManager(session=session, entry_id="e")
    assert asyncio.run(manager.async_reset_hv_container("t")) == "new-full"
    deleted = [c["url"] for c in session.calls if c["method"] == "DELETE"]
    assert deleted == [f"{api.API_BASE_URL}/customers/containers/old-full"]


def test_the_base_container_is_created_under_its_own_name():
    session = FakeSession([FakeResponse(201, {"containerId": "b"})])
    manager = container.CardataContainerManager(session=session, entry_id="e", kind=container.BASE)
    asyncio.run(manager.async_ensure_hv_container("t"))
    body = session.calls[0]["json"]
    assert body["name"] == container.BASE.name
    assert body["technicalDescriptors"] == list(container.BASE.descriptors)


# --- The fetch ---------------------------------------------------------------


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


class _Car:
    """BMW, as one account's cars see it: which container each car is refused."""

    def __init__(self, refuses_full: set[str], refuses_base: set[str] = frozenset()) -> None:
        self.refuses = {"id-full": refuses_full, "id-base": set(refuses_base)}
        self.sent: list[tuple[str, str]] = []

    async def call(self, request, vin, container_id):
        self.sent.append((vin, container_id))
        if vin in self.refuses[container_id]:
            raise REFUSED
        return {"telematicData": {"vehicle.status.conditionBasedServices": {"value": "[]"}}}


def _run(bmw: _Car, vins: list[str], *, data: dict | None = None, petrol=()):
    handled: list[str] = []
    entry = types.SimpleNamespace(entry_id="e", data=dict(data or {}))

    def update_entry(_entry, *, data):
        entry.data = data

    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_update_entry=update_entry)
    )

    async def container_id(hass, entry, runtime, kind):
        return f"id-{kind.key}"

    async def handle(message):
        handled.append(message["vin"])

    coordinator = types.SimpleNamespace(
        hass=hass,
        data={},
        is_combustion_only=lambda vin: vin in petrol,
        async_handle_message=handle,
        signal_diagnostics="sig",
        last_telematic_api_at=None,
    )
    runtime = types.SimpleNamespace(coordinator=coordinator, rest=bmw)
    namespace = _compile(
        ("_async_fetch_telematics", "_async_update_entry_data"),
        {
            "_LOGGER": logging.getLogger(__name__),
            "_async_container_id": container_id,
            "async_get_telematic_data": object(),
            "async_dispatcher_send": lambda *args: None,
            "debug_enabled": lambda: False,
            "mask_vin": debug.mask_vin,
            "datetime": datetime,
            "timezone": timezone,
            "BASE": container.BASE,
            "FULL": container.FULL,
            "REFUSED_VINS_KEY": container.REFUSED_VINS_KEY,
            "kind_for": container.kind_for,
            "refused_whole": container.refused_whole,
            "CardataApiError": api.CardataApiError,
            "CardataQuotaError": rest.CardataQuotaError,
            "CardataAuthError": device_flow.CardataAuthError,
        },
    )
    asked = asyncio.run(namespace["_async_fetch_telematics"](hass, entry, runtime, vins))
    return asked, handled, entry.data


def test_a_refused_car_is_fetched_from_the_base_container_at_once():
    bmw = _Car(refuses_full={PETROL})
    asked, handled, data = _run(bmw, [PETROL, EV])
    assert asked
    assert bmw.sent == [(PETROL, "id-full"), (PETROL, "id-base"), (EV, "id-full")]
    assert handled == [PETROL, EV]
    assert data[container.REFUSED_VINS_KEY] == [PETROL]


def test_a_refused_car_goes_straight_to_the_base_container_next_time():
    bmw = _Car(refuses_full={PETROL})
    _asked, handled, _data = _run(bmw, [PETROL], data={container.REFUSED_VINS_KEY: [PETROL]})
    assert bmw.sent == [(PETROL, "id-base")]
    assert handled == [PETROL]


def test_a_car_known_to_burn_fuel_never_asks_for_the_battery_fields():
    bmw = _Car(refuses_full=set())
    _run(bmw, [PETROL], petrol={PETROL})
    assert bmw.sent == [(PETROL, "id-base")]


def test_a_car_refused_even_the_base_container_is_logged_not_retried_forever():
    bmw = _Car(refuses_full={PETROL}, refuses_base={PETROL})
    asked, handled, data = _run(bmw, [PETROL, EV])
    assert asked
    assert bmw.sent == [(PETROL, "id-full"), (PETROL, "id-base"), (EV, "id-full")]
    assert handled == [EV]
    assert data[container.REFUSED_VINS_KEY] == [PETROL]


def test_another_bad_request_is_not_taken_for_a_refusal():
    class _BadId(_Car):
        async def call(self, request, vin, container_id):
            self.sent.append((vin, container_id))
            raise api.CardataApiError("HTTP 400 (CU-121)", status=400, error_id="CU-121")

    bmw = _BadId(refuses_full=set())
    _asked, _handled, data = _run(bmw, [EV])
    assert bmw.sent == [(EV, "id-full")]
    assert container.REFUSED_VINS_KEY not in data
