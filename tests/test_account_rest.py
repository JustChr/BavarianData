"""An account's REST side: its containers, the catch-up, tyres and its records.

``account_rest`` runs here against ``tests/fake_account.py``. The bootstrap,
the container fetch and the per-car refresh have their own files
(``test_bootstrap``, ``test_service_container``, ``test_vehicles``); this one
holds the rest.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from .conftest import load_module
from .fake_account import FakeBmw, FakeContainer, FakeHost, api, container, stored_containers

account_rest = load_module("account_rest")
const = load_module("const")
rest = load_module("rest")
device_flow = load_module("device_flow")

CAR_A = "WBA00000000000001"
CAR_B = "WBA00000000000002"
FULL = container.FULL


def _account(host: FakeHost):
    return account_rest.AccountRest(host)


# -- containers -------------------------------------------------------------


def test_a_current_container_is_used_as_it_is() -> None:
    host = FakeHost(FakeBmw(), data=stored_containers())

    assert asyncio.run(_account(host).container_id(FULL)) == "id-full"
    assert not host.writes and host.tokens.asked == 0
    assert host.containers[FULL.key].synced == ["id-full"]


def test_an_entry_from_before_signatures_takes_its_container_as_current() -> None:
    host = FakeHost(FakeBmw(), data={FULL.id_key: "old"})

    assert asyncio.run(_account(host).container_id(FULL)) == "old"
    assert host.writes == [{FULL.signature_key: "sig-full"}]


def test_a_container_holding_another_descriptor_set_is_replaced() -> None:
    host = FakeHost(FakeBmw(), data={FULL.id_key: "old", FULL.signature_key: "sig-before"})
    host.containers[FULL.key] = FakeContainer(FULL, created="new")

    assert asyncio.run(_account(host).container_id(FULL)) == "new"
    assert host.containers[FULL.key].calls == ["reset"]
    assert host.data[FULL.id_key] == "new" and host.data[FULL.signature_key] == "sig-full"


def test_an_entry_without_a_container_gets_one() -> None:
    host = FakeHost(FakeBmw())

    assert asyncio.run(_account(host).container_id(FULL)) == "id-full"
    assert host.containers[FULL.key].calls == ["ensure"]
    assert host.tokens.asked == 1


@pytest.mark.parametrize("error", [True, False])
def test_a_container_bmw_will_not_create_is_none_and_nothing_stored(error: bool) -> None:
    host = FakeHost(FakeBmw())
    host.containers[FULL.key] = FakeContainer(FULL, created=None, error=error)

    assert asyncio.run(_account(host).container_id(FULL)) is None
    assert not host.writes


def test_two_fetches_at_once_create_one_container() -> None:
    host = FakeHost(FakeBmw())

    async def both():
        account = _account(host)
        return await asyncio.gather(account.container_id(FULL), account.container_id(FULL))

    assert asyncio.run(both()) == ["id-full", "id-full"]
    assert host.containers[FULL.key].calls == ["ensure"]


# -- the catch-up -----------------------------------------------------------


def _catch_up(**kwargs) -> FakeHost:
    bmw = FakeBmw({api.async_get_telematic_data: {"telematicData": {"x": {"value": 1}}}})
    data = {**stored_containers(), **kwargs.pop("data", {})}
    host = FakeHost(bmw, vins=(CAR_A, CAR_B), data=data, **kwargs)
    asyncio.run(_account(host).catch_up("after startup"))
    return host


def _fetched(host: FakeHost) -> list[str]:
    return list(dict.fromkeys(args[0] for args in host.rest.asked(api.async_get_telematic_data)))


def test_a_start_catches_up_every_car_and_stamps_it_first() -> None:
    host = _catch_up()

    assert _fetched(host) == [CAR_A, CAR_B]
    assert host.writes[0] == {const.LAST_STARTUP_REFRESH: pytest.approx(time.time(), abs=5)}


def test_a_start_right_after_a_fetch_asks_nothing() -> None:
    host = _catch_up(data={"last_telematic_poll": time.time() - 60})

    assert not host.rest.sent and not host.writes


def test_an_unconfirmed_charge_is_asked_about_sooner() -> None:
    bmw = FakeBmw({api.async_get_telematic_data: {"telematicData": {"x": {"value": 1}}}})
    host = FakeHost(
        bmw,
        vins=(CAR_A, CAR_B),
        data={**stored_containers(), const.LAST_STARTUP_REFRESH: time.time() - 45 * 60},
    )
    host.coordinator.unconfirmed = [CAR_B]
    asyncio.run(_account(host).catch_up("after startup"))

    assert _fetched(host) == [CAR_B]


def test_the_catch_up_can_be_switched_off() -> None:
    host = _catch_up(options={const.OPTION_REFRESH_ON_START: False})

    assert not host.rest.sent


def test_the_catch_up_never_spends_the_last_of_the_quota() -> None:
    host = _catch_up(quota_remaining=11)

    assert _fetched(host) == [CAR_A]


# -- tyres ------------------------------------------------------------------


def test_a_car_without_tyre_data_does_not_stop_the_next() -> None:
    refused = api.CardataApiError("HTTP 404", status=404)
    bmw = FakeBmw(
        {api.async_get_tyre_diagnosis: lambda vin: refused if vin == CAR_A else {"wheels": []}}
    )
    host = FakeHost(bmw, vins=(CAR_A, CAR_B))

    assert asyncio.run(_account(host).fetch_tyres()) is True
    assert list(host.coordinator.tyres) == [CAR_B]


@pytest.mark.parametrize(
    "error", [rest.CardataQuotaError("limit"), device_flow.CardataAuthError("invalid_grant")]
)
def test_a_spent_quota_or_lost_token_ends_the_tyre_round(error) -> None:
    host = FakeHost(FakeBmw({api.async_get_tyre_diagnosis: error}), vins=(CAR_A, CAR_B))

    assert asyncio.run(_account(host).fetch_tyres()) is False
    assert host.rest.asked(api.async_get_tyre_diagnosis) == [(CAR_A,)]


# -- basic data -------------------------------------------------------------


def test_basic_data_bmw_refuses_is_logged_without_the_request_path(caplog) -> None:
    refused = api.CardataApiError(
        f"HTTP 403 for /customers/vehicles/{CAR_A}/basicData", status=403, error_id="CU-403"
    )
    host = FakeHost(FakeBmw({api.async_get_basic_data: refused}))

    with caplog.at_level(logging.WARNING):
        asyncio.run(_account(host).fetch_basic_data([CAR_A, CAR_B]))

    assert "HTTP 403 CU-403" in caplog.text
    assert CAR_A not in caplog.text, "the VIN stays out of the log"
    assert host.rest.asked(api.async_get_basic_data) == [(CAR_A,), (CAR_B,)]


def test_basic_data_lost_to_the_network_says_so(caplog) -> None:
    host = FakeHost(FakeBmw({api.async_get_basic_data: api.CardataApiError("timeout")}))

    with caplog.at_level(logging.WARNING):
        asyncio.run(_account(host).fetch_basic_data([CAR_A]))

    assert "network error" in caplog.text


@pytest.mark.parametrize(
    "error", [rest.CardataQuotaError("limit"), device_flow.CardataAuthError("invalid_grant")]
)
def test_a_spent_quota_or_lost_token_ends_the_basic_data_round(error) -> None:
    host = FakeHost(FakeBmw({api.async_get_basic_data: error}))

    asyncio.run(_account(host).fetch_basic_data([CAR_A, CAR_B]))

    assert host.rest.asked(api.async_get_basic_data) == [(CAR_A,)]


@pytest.mark.parametrize("answer", [None, "text", {}])
def test_empty_basic_data_names_no_device(answer) -> None:
    host = FakeHost(FakeBmw({api.async_get_basic_data: answer}))

    asyncio.run(_account(host).fetch_basic_data([CAR_A]))

    assert not host.devices and not host.writes


# -- the entry's records ----------------------------------------------------


def test_unchanged_basic_data_is_not_written_again() -> None:
    host = FakeHost(FakeBmw(), data={const.VEHICLE_METADATA: {CAR_A: {"a": 1}}})
    account = _account(host)

    account.store_vehicle_metadata(CAR_A, {"a": 1})
    assert not host.writes
    account.store_vehicle_metadata(CAR_B, {"b": 2})
    assert host.data[const.VEHICLE_METADATA] == {CAR_A: {"a": 1}, CAR_B: {"b": 2}}


def test_damaged_stored_basic_data_is_replaced() -> None:
    host = FakeHost(FakeBmw(), data={const.VEHICLE_METADATA: "junk"})

    _account(host).store_vehicle_metadata(CAR_A, {"a": 1})
    assert host.data[const.VEHICLE_METADATA] == {CAR_A: {"a": 1}}


def test_the_poll_stamp_is_not_rewritten_within_a_second() -> None:
    host = FakeHost(FakeBmw(), data={"last_telematic_poll": 1000.0})
    account = _account(host)

    account.update_last_telematic_poll(1000.5)
    assert not host.writes
    account.update_last_telematic_poll(1002.0)
    assert host.data["last_telematic_poll"] == 1002.0


def test_the_bootstrap_is_marked_complete_once() -> None:
    host = FakeHost(FakeBmw())
    account = _account(host)

    account.mark_bootstrap_complete()
    account.mark_bootstrap_complete()
    assert host.writes == [{const.BOOTSTRAP_COMPLETE: True}]


def test_asking_bmw_pings_the_diagnostics() -> None:
    bmw = FakeBmw({api.async_get_telematic_data: {"telematicData": {}}})
    host = FakeHost(bmw, data=stored_containers())

    assert asyncio.run(_account(host).fetch_telematics([CAR_A])) is True
    assert host.diagnostics == 1
    assert host.coordinator.last_telematic_api_at is not None
    assert not host.coordinator.handled, "an empty answer feeds nothing in"


def test_the_daily_refresh_is_due_a_day_after_the_last() -> None:
    day = const.TELEMATIC_POLL_INTERVAL

    assert account_rest.seconds_until_poll(100.0, 100.0) == day
    assert account_rest.seconds_until_poll(100.0, 100.0 + day) == 0
    assert account_rest.seconds_until_poll(None, 10.0**9) < 0, "never polled: due now"
