"""The account's vehicle list, and the guard that it is actually walked.

Issue #13: on an account with two cars the daily REST refresh only ever covered
one of them. Everything BMW cannot stream -- Condition Based Servicing, service
demands, charging level, door-lock status, the tyre diagnosis -- therefore froze
on the other car at its setup value, while its stream kept running and hid the
gap. The bug was not the fetching, it was the *selection*: a single VIN resolved
from ``next(iter(coordinator.data))``, i.e. whichever car spoke first after a
restart.

So two kinds of test here: real unit tests over the selection (order is
load-bearing, see ``vehicles.known_vins``), and AST guards proving the two REST
refresh paths iterate it instead of picking one VIN again.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from .conftest import load_module

vehicles = load_module("vehicles")
known_vins = vehicles.known_vins

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
# The two paths that spend the daily quota, one request per vehicle each.
REFRESH_FUNCTIONS = ("_async_perform_telematic_fetch", "_async_perform_tyre_fetch")


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


def _function(name: str) -> ast.AST:
    tree = ast.parse(_INIT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in __init__.py")


@pytest.mark.parametrize("name", REFRESH_FUNCTIONS)
def test_refresh_paths_iterate_every_vehicle(name: str) -> None:
    """The fetch must loop the account's VINs, not resolve a single one."""

    node = _function(name)
    calls_helper = any(
        isinstance(call.func, ast.Name) and call.func.id == "known_vins"
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    )
    assert calls_helper, f"{name} must take its VINs from known_vins()"
    loops_over_vins = any(
        isinstance(loop.target, ast.Name) and loop.target.id == "vin"
        for loop in ast.walk(node)
        if isinstance(loop, (ast.For, ast.AsyncFor))
    )
    assert loops_over_vins, f"{name} must loop over every VIN, not fetch one"


@pytest.mark.parametrize("name", REFRESH_FUNCTIONS)
def test_refresh_paths_never_pick_an_arbitrary_vin(name: str) -> None:
    """``next(iter(coordinator.data))`` is the bug of #13 itself.

    Insertion order is whichever car streamed first since the restart, so this
    pattern silently picks a different vehicle across reboots.
    """

    source = ast.unparse(_function(name))
    assert "next(iter(" not in source, (
        f"{name} resolves a single arbitrary VIN again -- see issue #13"
    )
