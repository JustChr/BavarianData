"""The containers every car reads, and the way back from the service items' own.

BMW answers a container holding one field it won't serve with a bare
``CU-400`` for the whole request, naming no field. From 16 Oct 2026 it did so
for ``vehicle.status.conditionBasedServices`` for about a week (#130/#142), so
v0.9.20 to v0.9.24 read that field from a container of its own. BMW has
answered it since 23 Oct (seen live every day to 2 Nov), so it is back in the
full container: one request per car again, and the old container goes the
first time an upgraded entry fetches.

The fetch is ``account_rest.AccountRest.fetch_telematics``, run against
``tests/fake_account.py``.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from .conftest import FakeResponse, FakeSession, load_module
from .fake_account import FakeBmw, FakeContainer, FakeHost, gateway, stored_containers

container = load_module("container")
api = load_module("api")
const = load_module("const")
descriptors = load_module("descriptors")
rest = load_module("rest")
account_rest = load_module("account_rest")
entry_setup = load_module("entry_setup")

CBS = "vehicle.status.conditionBasedServices"
LOCKS = "vehicle.cabin.door.lock.status"
CAR = "WBA00000000000001"
OTHER = "WBA00000000000002"
REFUSED = api.CardataApiError("HTTP 400 (CU-400: Bad request)", status=400, error_id="CU-400")
SERVICE_NAME = "BavarianData service items"


# --- The containers ------------------------------------------------------------


def test_the_service_items_are_back_in_the_full_container():
    assert CBS in container.FULL.descriptors
    assert container.KINDS == (container.FULL,)


def test_the_full_container_holds_every_field_worth_fetching():
    wanted = descriptors.container_descriptors(const.HV_BATTERY_DESCRIPTORS)
    assert container.FULL.descriptors == tuple(wanted)


def test_replacing_the_full_container_clears_every_retired_one():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "containers": [
                        {"containerId": "old-full", "name": container.FULL.name},
                        {"containerId": "beta6", "name": "BavarianData non-streamable"},
                        {"containerId": "service", "name": SERVICE_NAME},
                        {"containerId": "theirs", "name": "probe one field"},
                    ]
                },
            ),
            FakeResponse(201, {"containerId": "new-full"}),
            FakeResponse(204, None),
            FakeResponse(204, None),
            FakeResponse(204, None),
        ]
    )
    manager = container.CardataContainerManager(rest=gateway(session), entry_id="e")
    assert asyncio.run(manager.async_reset_hv_container()) == "new-full"
    deleted = [c["url"].rsplit("/", 1)[1] for c in session.calls if c["method"] == "DELETE"]
    assert deleted == ["old-full", "beta6", "service"]


def test_an_upgraded_entry_replaces_its_full_container_on_the_first_fetch():
    """v0.9.24's entry: a full container without the service items, and theirs."""

    full = container.FULL
    without_cbs = [d for d in full.descriptors if d != CBS]
    stored = {
        full.id_key: "old-full",
        full.signature_key: container.CardataContainerManager.compute_signature(without_cbs),
        "service_container_id": "service",
        "service_descriptor_signature": "sig",
    }
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "containers": [
                        {"containerId": "old-full", "name": full.name},
                        {"containerId": "service", "name": SERVICE_NAME},
                    ]
                },
            ),
            FakeResponse(201, {"containerId": "new-full"}),
            FakeResponse(204, None),
            FakeResponse(204, None),
        ]
    )
    data = entry_setup.without_retired_keys(stored)
    assert data == {key: stored[key] for key in (full.id_key, full.signature_key)}
    manager = container.CardataContainerManager(rest=gateway(session), entry_id="e")
    host = FakeHost(FakeBmw(), data=data, containers={full.key: manager})

    assert asyncio.run(account_rest.AccountRest(host).container_id(full)) == "new-full"
    created = [c["json"] for c in session.calls if c["method"] == "POST"]
    assert [body["technicalDescriptors"] for body in created] == [list(full.descriptors)]
    deleted = [c["url"].rsplit("/", 1)[1] for c in session.calls if c["method"] == "DELETE"]
    assert deleted == ["old-full", "service"]
    assert host.data[full.id_key] == "new-full"
    assert host.data[full.signature_key] == manager.descriptor_signature


# --- The fetch -------------------------------------------------------------------


class _Bmw(FakeBmw):
    """BMW as the account's cars see it: which cars it refuses."""

    def __init__(self, refuses: set[str] = frozenset()) -> None:
        super().__init__({api.async_get_telematic_data: self._answer})
        self.refuses = set(refuses)

    def _answer(self, vin, container_id):
        if vin in self.refuses:
            return REFUSED
        return {"telematicData": {LOCKS: {"value": "x"}, CBS: {"value": "[]"}}}

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self.asked(api.async_get_telematic_data)


def _run(bmw: _Bmw, vins: list[str], *, missing: bool = False, strict: bool = False):
    # A missing container is one the entry has none of and BMW won't create.
    full = container.FULL
    host = FakeHost(
        bmw,
        data={} if missing else stored_containers(),
        containers={full.key: FakeContainer(full, created=None if missing else "id-full")},
    )
    asked = asyncio.run(account_rest.AccountRest(host).fetch_telematics(vins, strict=strict))
    handled = [(m["vin"], tuple(m["data"])) for m in host.coordinator.handled]
    return asked, handled


def test_every_car_reads_one_container():
    bmw = _Bmw()
    asked, handled = _run(bmw, [CAR, OTHER])
    assert asked
    assert bmw.requests == [(CAR, "id-full"), (OTHER, "id-full")]
    assert handled == [(CAR, (LOCKS, CBS)), (OTHER, (LOCKS, CBS))]


def test_a_refused_car_costs_only_itself(caplog):
    bmw = _Bmw(refuses={CAR})
    with caplog.at_level(logging.ERROR):
        asked, handled = _run(bmw, [CAR, OTHER])
    assert asked
    assert handled == [(OTHER, (LOCKS, CBS))]
    assert "request failed" in caplog.text


def test_no_container_asks_bmw_nothing():
    bmw = _Bmw()
    asked, handled = _run(bmw, [CAR], missing=True)
    assert not asked and not bmw.requests and not handled


def test_a_strict_fetch_without_a_container_says_so():
    with pytest.raises(account_rest.FetchFailed, match="no full REST container"):
        _run(_Bmw(), [CAR], missing=True, strict=True)


def test_a_strict_fetch_says_what_it_missed_once_every_car_had_its_turn():
    bmw = _Bmw(refuses={CAR})
    with pytest.raises(account_rest.FetchFailed, match=r"\*\*\*0001: HTTP 400"):
        _run(bmw, [CAR, OTHER], strict=True)
    assert (OTHER, "id-full") in bmw.requests


def test_a_spent_quota_stops_the_round_at_the_car_it_hit():
    bmw = FakeBmw({api.async_get_telematic_data: rest.CardataQuotaError("limit")})
    host = FakeHost(bmw, data=stored_containers())
    fetch = account_rest.AccountRest(host).fetch_telematics([CAR, OTHER], strict=True)
    with pytest.raises(account_rest.FetchFailed, match="limit"):
        asyncio.run(fetch)
    assert [args[0] for args in bmw.asked(api.async_get_telematic_data)] == [CAR]
