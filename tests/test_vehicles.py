"""The account's vehicle list, and the guard that it is actually walked.

Issue #13: on an account with two cars the daily REST refresh only ever covered
one of them. Everything BMW cannot stream -- Condition Based Servicing, service
demands, charging level, door-lock status, the tyre diagnosis -- therefore froze
on the other car at its setup value, while its stream kept running and hid the
gap. The bug was not the fetching, it was the *selection*: a single VIN resolved
from ``next(iter(coordinator.data))``, i.e. whichever car spoke first after a
restart.

So unit tests over the selection (order is load-bearing, see
``vehicles.known_vins``), and the two REST refresh paths run over a two-car
account (``account_rest``, against ``tests/fake_account.py``), proving they ask
for every car instead of picking one VIN again.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from .conftest import load_module
from .fake_account import FakeBmw, FakeHost, api, stored_containers

vehicles = load_module("vehicles")
account_rest = load_module("account_rest")
const = load_module("const")
known_vins = vehicles.known_vins


def test_metadata_and_stream_vins_are_merged() -> None:
    assert known_vins(
        stored_metadata={"WBA1": {}, "WBA2": {}},
        coordinator_data={"WBA2": {}, "WBA3": {}},
    ) == ["WBA1", "WBA2", "WBA3"]


def test_configured_vin_is_refreshed_first() -> None:
    """A cut-short round must keep serving the car it served before."""

    assert known_vins(
        stored_metadata={"WBA1": {}, "WBA2": {}},
        coordinator_data={"WBA3": {}},
        configured_vin="WBA2",
    ) == ["WBA2", "WBA1", "WBA3"]


def test_a_car_seen_only_on_the_stream_still_counts() -> None:
    """A vehicle added to the account after setup has no stored metadata."""

    assert known_vins(coordinator_data={"WBA9": {}}) == ["WBA9"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"stored_metadata": None, "coordinator_data": None},
        {"stored_metadata": {}, "coordinator_data": {}},
        {"configured_vin": ""},
        {"configured_vin": "   "},
        {"stored_metadata": "not-a-mapping"},
    ],
)
def test_nothing_known_is_an_empty_list(kwargs) -> None:
    assert known_vins(**kwargs) == []


def test_junk_never_reaches_a_request_path() -> None:
    assert known_vins(stored_metadata={"WBA1": {}, "": {}, 7: {}, None: {}}) == ["WBA1"]


CAR_A = "WBA00000000000001"
CAR_B = "WBA00000000000002"


def _two_cars() -> FakeHost:
    bmw = FakeBmw(
        {
            api.async_get_telematic_data: {"telematicData": {"x": {"value": 1}}},
            api.async_get_tyre_diagnosis: {"wheels": []},
            api.async_get_basic_data: {"modelName": "iX1"},
        }
    )
    return FakeHost(bmw, vins=(CAR_A, CAR_B), data=stored_containers())


def test_the_daily_refresh_asks_for_every_car() -> None:
    """Both quota-spending paths, one request per vehicle each (#13)."""

    host = _two_cars()
    asyncio.run(account_rest.AccountRest(host).daily_refresh())

    containers = [args[0] for args in host.rest.asked(api.async_get_telematic_data)]
    assert sorted(set(containers)) == [CAR_A, CAR_B]
    assert host.rest.asked(api.async_get_tyre_diagnosis) == [(CAR_A,), (CAR_B,)]
    assert host.data["last_telematic_poll"] > 0


def test_a_refresh_for_one_car_asks_for_that_car_only() -> None:
    host = _two_cars()
    asyncio.run(account_rest.AccountRest(host).refresh(vin=CAR_B))

    assert {args[0] for args in host.rest.asked(api.async_get_telematic_data)} == {CAR_B}


def test_a_refresh_without_cars_asks_nothing_and_says_so_when_strict() -> None:
    host = FakeHost(FakeBmw())
    assert asyncio.run(account_rest.AccountRest(host).refresh()) is False
    with pytest.raises(account_rest.FetchFailed, match="no car yet"):
        asyncio.run(account_rest.AccountRest(host).refresh(strict=True))
    assert not host.rest.sent


def test_adopted_car_gets_its_entities_seeded() -> None:
    """A car found by Discover is a bare device: BMW replays nothing and a parked
    car may not stream for days, so adopting it also seeds its entities (#44)."""

    host = _two_cars()
    asyncio.run(account_rest.AccountRest(host).adopt(CAR_B))

    assert host.rest.asked(api.async_get_basic_data) == [(CAR_B,)]
    assert {args[0] for args in host.rest.asked(api.async_get_telematic_data)} == {CAR_B}
    assert host.devices[CAR_B]["name"] == "iX1"


def test_a_car_that_already_has_basic_data_is_not_adopted_again() -> None:
    host = _two_cars()
    host.data[const.VEHICLE_METADATA] = {CAR_B: {"modelName": "iX1"}}
    asyncio.run(account_rest.AccountRest(host).adopt(CAR_B))

    assert not host.rest.sent


def test_seeding_skips_a_car_that_already_streamed() -> None:
    host = _two_cars()
    host.coordinator.data[CAR_B] = {"x": {"value": 1}}
    asyncio.run(account_rest.AccountRest(host).adopt(CAR_B))

    assert not host.rest.asked(api.async_get_telematic_data)


def test_primary_vins_reads_both_payload_shapes_and_skips_secondary() -> None:
    listed = [
        {"vin": "WBA1", "mappingType": "PRIMARY"},
        {"vin": "WBA2", "mappingType": "SECONDARY"},
        {"vin": "WBA3"},
        {"vin": "WBA1", "mappingType": "primary"},
        {"vin": ""},
        "junk",
    ]
    assert vehicles.primary_vins(listed) == ["WBA1", "WBA3"]
    assert vehicles.primary_vins({"mappings": listed}) == ["WBA1", "WBA3"]
    assert vehicles.primary_vins({"vehicles": [{"vin": "WBA9"}]}) == ["WBA9"]
    assert vehicles.primary_vins(None) == []
    assert vehicles.primary_vins({"mappings": "nope"}) == []


def test_discover_vehicles_adopts_unknown_cars() -> None:
    """The service used to fetch the mappings and only log their count, so it
    could never add a car (#44)."""

    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components/bavariandata/service_fetch.py"
    )
    source = path.read_text(encoding="utf-8")
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_handle_fetch_mappings"
    )
    body = ast.get_source_segment(source, node) or ""
    assert "primary_vins(" in body and "async_on_new_vehicle(" in body


def _function_body(relative: str, name: str) -> str:
    path = pathlib.Path(__file__).resolve().parents[1] / "custom_components/bavariandata" / relative
    source = path.read_text(encoding="utf-8")
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_flows_that_map_cars_look_for_them_afterwards() -> None:
    """Bootstrap runs once. Re-authorizing an account and choosing streamed data
    are the two moments a car gets mapped, so both must run discovery (#44)."""

    package = pathlib.Path(__file__).resolve().parents[1] / "custom_components/bavariandata"
    for flow in ("config_flow.py", "options_flow.py"):
        source = (package / flow).read_text(encoding="utf-8")
        assert source.count("schedule_vehicle_discovery(") == 1, flow
    assert "fetch_vehicle_mappings" in _function_body(
        "flow_activator.py", "schedule_vehicle_discovery"
    )


# --- the entry is titled after its cars --------------------------------------


def test_the_setup_title_gives_way_to_the_car_names() -> None:
    setup = vehicles.setup_title("0f3c9a71-1111-2222-3333-444455556666")
    assert setup == "BavarianData (0f3c9a71)"
    assert vehicles.new_account_title(setup, None, ["i5 eDrive40", "iX1 xDrive30"]) == (
        "i5 eDrive40, iX1 xDrive30"
    )


def test_the_old_setup_title_is_replaced_too() -> None:
    old = "BavarianData: Connect Home Assistant to BMW CarData (0f3c9a71)"
    assert vehicles.new_account_title(old, None, ["i5 eDrive40"]) == "i5 eDrive40"


def test_a_title_we_set_follows_a_second_car() -> None:
    assert (
        vehicles.new_account_title("i5 eDrive40", "i5 eDrive40", ["i5 eDrive40", "iX1"])
        == "i5 eDrive40, iX1"
    )


def test_a_title_the_user_chose_is_left_alone() -> None:
    assert vehicles.new_account_title("Family cars", "i5 eDrive40", ["i5", "iX1"]) is None
    # Even one that looks like ours but isn't the setup pattern.
    assert vehicles.new_account_title("BavarianData garage", None, ["i5"]) is None


def test_no_names_or_no_change_leaves_the_title() -> None:
    assert vehicles.new_account_title("BavarianData (0f3c9a71)", None, []) is None
    assert vehicles.new_account_title("i5", "i5", ["i5"]) is None


# --- a car that left the account, and forgetting one -------------------------

I5 = "WBAEXAMPLE0000001"
IX1 = "WBAEXAMPLE0000002"


def test_a_car_missing_from_the_mapping_has_left() -> None:
    payload = [{"vin": I5, "mappingType": "PRIMARY"}]
    assert vehicles.left_account_vins([I5, IX1], payload) == [IX1]
    assert vehicles.left_account_vins([I5], {"mappings": payload}) == []


def test_a_car_that_became_secondary_has_left_too() -> None:
    payload = [{"vin": I5, "mappingType": "PRIMARY"}, {"vin": IX1, "mappingType": "SECONDARY"}]
    assert vehicles.left_account_vins([I5, IX1], payload) == [IX1]


def test_a_malformed_mapping_never_reads_as_every_car_gone() -> None:
    assert vehicles.left_account_vins([I5], {"error": "oops"}) is None
    assert vehicles.left_account_vins([I5], None) is None
    assert vehicles.left_account_vins([I5], "text") is None


def test_the_left_account_repair_id_carries_no_vin() -> None:
    issue_id = vehicles.left_account_issue_id("entry", I5)
    assert I5 not in issue_id
    assert issue_id == vehicles.left_account_issue_id("entry", I5)
    assert issue_id != vehicles.left_account_issue_id("entry", IX1)


def test_forgetting_a_car_drops_its_basic_data_and_legacy_key() -> None:
    data = {"vin": I5, "vehicle_metadata": {I5: {"a": 1}, IX1: {"b": 2}}, "other": 3}
    forgotten = vehicles.forget_in_entry_data(data, I5, "vehicle_metadata")
    assert forgotten == {"vehicle_metadata": {IX1: {"b": 2}}, "other": 3}
    assert data["vin"] == I5  # the original is left alone
