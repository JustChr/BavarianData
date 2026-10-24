"""A car's basic data must not depend on its first telematics fetch.

Basic data is what names a car and tells its drivetrain, steering side and
picture. The bootstrap used to ask for it only when the first telematics fetch
had delivered something, so a fresh install whose container BMW refused (#135's
CU-400, the 10-container limit, a spent quota) left its cars unnamed -- and
nothing asked again, because a car with a device is never "new". Now the
bootstrap always asks, and every start asks once for a car still without it.

It all runs in ``account_rest.py``, against ``tests/fake_account.py``.
"""

from __future__ import annotations

import asyncio

from .conftest import load_module
from .fake_account import FakeBmw, FakeHost, api, stored_containers

account_rest = load_module("account_rest")
const = load_module("const")
device_flow = load_module("device_flow")

CAR_A = "WBA00000000000001"
CAR_B = "WBA00000000000002"
REFUSED = api.CardataApiError("HTTP 400 (CU-400: Bad request)", status=400, error_id="CU-400")


def _bootstrap(*, telematics_ok: bool, mapping_error: Exception | None = None) -> FakeHost:
    bmw = FakeBmw(
        {
            api.async_get_vehicle_mappings: mapping_error
            or [{"vin": CAR_A, "mappingType": "PRIMARY"}, {"vin": CAR_B}],
            api.async_get_telematic_data: (
                {"telematicData": {"vehicle.cabin.door.lock.status": {"value": "LOCKED"}}}
                if telematics_ok
                else REFUSED
            ),
            api.async_get_basic_data: lambda vin: {"modelName": f"car {vin[-1]}"},
        }
    )
    host = FakeHost(bmw, data=stored_containers())
    asyncio.run(account_rest.AccountRest(host).bootstrap())
    return host


def test_basic_data_is_fetched_even_when_the_telematics_fetch_fails():
    host = _bootstrap(telematics_ok=False)
    assert host.rest.asked(api.async_get_basic_data) == [(CAR_A,), (CAR_B,)]
    assert host.data[const.BOOTSTRAP_COMPLETE] is True
    assert list(host.devices) == [CAR_A, CAR_B]
    assert host.devices[CAR_A]["name"] == "car 1", "named by its basic data"
    # Refused, but asked: the quota was spent, so the daily refresh counts as done.
    assert host.data["last_telematic_poll"] > 0


def test_basic_data_is_fetched_after_a_working_telematics_fetch_too():
    host = _bootstrap(telematics_ok=True)
    assert [args[0] for args in host.rest.asked(api.async_get_telematic_data)] == [
        CAR_A,
        CAR_A,
        CAR_B,
        CAR_B,
    ]
    assert host.rest.asked(api.async_get_basic_data) == [(CAR_A,), (CAR_B,)]
    assert host.data[const.VEHICLE_METADATA] == {
        CAR_A: {"modelName": "car 1"},
        CAR_B: {"modelName": "car 2"},
    }
    assert host.data["last_telematic_poll"] > 0


def test_without_a_token_the_bootstrap_waits_for_the_next_start():
    host = _bootstrap(
        telematics_ok=True, mapping_error=device_flow.CardataAuthError("invalid_grant")
    )
    assert [name for name, _args in host.rest.sent] == ["async_get_vehicle_mappings"]
    assert const.BOOTSTRAP_COMPLETE not in host.data
    assert not host.devices


def test_an_account_without_cars_is_bootstrapped_once():
    bmw = FakeBmw({api.async_get_vehicle_mappings: []})
    host = FakeHost(bmw)
    asyncio.run(account_rest.AccountRest(host).bootstrap())
    assert host.data[const.BOOTSTRAP_COMPLETE] is True
    assert len(bmw.sent) == 1


def test_a_failed_mapping_request_is_an_account_without_cars():
    bmw = FakeBmw({api.async_get_vehicle_mappings: REFUSED})
    host = FakeHost(bmw)
    asyncio.run(account_rest.AccountRest(host).bootstrap())
    assert host.data[const.BOOTSTRAP_COMPLETE] is True
    assert not host.devices


def test_every_start_finds_the_cars_still_without_basic_data():
    # The third car only has a device: it streamed nothing since the restart.
    host = FakeHost(
        FakeBmw(),
        vins=(CAR_A, CAR_B, "WBA00000000000003"),
        data={const.VEHICLE_METADATA: {CAR_A: {"brand": "BMW"}}},
    )
    assert account_rest.AccountRest(host).vins_without_basic_data() == [
        CAR_B,
        "WBA00000000000003",
    ]
