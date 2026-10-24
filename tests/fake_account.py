"""An ``account_rest.AccountHost`` without Home Assistant, and BMW behind it.

``FakeHost`` records what the REST side writes (entry data, devices, the
diagnostics ping); ``FakeBmw`` answers ``host.rest.call`` per request function
and records every request, so a test reads what was asked of BMW and in which
order.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from .conftest import load_module

api = load_module("api")
container = load_module("container")


class FakeBmw:
    """``RestGateway.call`` with an answer per request function.

    An answer is a value, an exception to raise, or a callable taking the
    request's arguments and returning (or raising) one.
    """

    def __init__(self, answers: Optional[dict[Callable, Any]] = None) -> None:
        self.answers = dict(answers or {})
        self.sent: list[tuple[str, tuple]] = []

    async def call(self, request: Callable, *args: Any) -> Any:
        self.sent.append((request.__name__, args))
        answer = self.answers.get(request)
        if callable(answer) and not isinstance(answer, type):
            answer = answer(*args)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def asked(self, request: Callable) -> list[tuple]:
        return [args for name, args in self.sent if name == request.__name__]


class FakeCoordinator:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.handled: list[dict] = []
        self.basic: dict[str, dict] = {}
        self.tyres: dict[str, Any] = {}
        self.unconfirmed: list[str] = []
        self.last_telematic_api_at = None

    async def async_handle_message(self, message: dict) -> None:
        self.handled.append(message)
        self.data.setdefault(message["vin"], {}).update(message["data"])

    def apply_basic_data(self, vin: str, payload: dict) -> dict:
        self.basic[vin] = payload
        name = payload.get("modelName")
        if name:
            self.names[vin] = name
        return {"name": name or vin, "model": name} if payload else {}

    def apply_tyre_diagnosis(self, vin: str, payload: Any) -> dict:
        self.tyres[vin] = payload
        return {"wheels": {"front_left": {}}}

    def unconfirmed_restored_charges(self) -> list[str]:
        return list(self.unconfirmed)


class FakeContainer:
    """A container manager whose BMW side answers ``created``."""

    def __init__(self, kind: Any, created: Optional[str] = None, error: bool = False) -> None:
        self.kind = kind
        self.descriptor_signature = f"sig-{kind.key}"
        self.created = created
        self.error = error
        self.calls: list[str] = []
        self.synced: list[Optional[str]] = []

    def sync_from_entry(self, container_id: Optional[str]) -> None:
        self.synced.append(container_id)

    async def async_ensure_hv_container(self, token: str) -> Optional[str]:
        return self._answer("ensure")

    async def async_reset_hv_container(self, token: str) -> Optional[str]:
        return self._answer("reset")

    def _answer(self, call: str) -> Optional[str]:
        self.calls.append(call)
        if self.error:
            raise container.CardataContainerError("BMW said no")
        return self.created


class FakeTokens:
    def __init__(self) -> None:
        self.asked = 0

    async def access_token(self) -> str:
        self.asked += 1
        return "token"


class FakeHost:
    def __init__(
        self,
        bmw: FakeBmw,
        *,
        vins: tuple[str, ...] = (),
        data: Optional[dict] = None,
        options: Optional[dict] = None,
        containers: Optional[dict] = None,
        quota_remaining: Optional[int] = None,
    ) -> None:
        self.entry_id = "entry"
        self.coordinator = FakeCoordinator()
        self.rest = bmw
        self.tokens = FakeTokens()
        self.containers = containers or {
            kind.key: FakeContainer(kind, created=f"id-{kind.key}") for kind in container.KINDS
        }
        self.container_lock = asyncio.Lock()
        self.data: dict = dict(data or {})
        self.options: dict = dict(options or {})
        self.quota_remaining = quota_remaining
        self.vins = list(vins)
        self.writes: list[dict] = []
        self.devices: dict[str, dict] = {}
        self.diagnostics = 0

    def update_data(self, **values: Any) -> None:
        self.writes.append(values)
        self.data = {**self.data, **values}

    def register_device(self, vin: str, fields: Any) -> None:
        self.devices[vin] = dict(fields)

    def account_vins(self) -> list[str]:
        return list(self.vins)

    def diagnostics_changed(self) -> None:
        self.diagnostics += 1


def stored_containers() -> dict[str, str]:
    """Entry data holding a current container of every kind (``id-<kind>``)."""

    data: dict[str, str] = {}
    for kind in container.KINDS:
        data[kind.id_key] = f"id-{kind.key}"
        data[kind.signature_key] = f"sig-{kind.key}"
    return data
