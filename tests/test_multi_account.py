"""Guards for households with more than one car or more than one BMW account.

The integration was built against one car in one account, and every assumption
that quietly encoded has had to be found the hard way: a second car's REST-only
values frozen forever (#13), the card calling services without saying which
entry it meant (#11). This file pins the rest of that audit.

What can be exercised directly is exercised directly (the debug flag, the
coverage clock -- both Home Assistant-free by design). The rest are *shape*
checks over the shipped source, like ``test_review_guards.py``: a second account
cannot be set up in this harness, so the reviewable artefact is the code that
decides, and a regression has to fail here rather than on a user's dashboard.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

from .conftest import load_module

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"

debug = load_module("debug")
coverage = load_module("coverage")


# --- the debug flag is a shared logger, but a per-entry choice ---------------


def _reset_debug() -> None:
    debug._ENTRY_CHOICES.clear()
    debug.set_debug_enabled(False)


def test_one_account_cannot_switch_off_the_others_debug_logging() -> None:
    """The symptom: you turn debug on for the account you are diagnosing, the
    other entry reloads, and the log goes quiet again with nothing to explain
    it. The level is one logger's, so the flag has to be the union."""

    _reset_debug()
    debug.set_debug_enabled(True, entry_id="account_a")
    debug.set_debug_enabled(False, entry_id="account_b")
    assert debug.debug_enabled() is True

    debug.set_debug_enabled(False, entry_id="account_a")
    assert debug.debug_enabled() is False
    _reset_debug()


def test_an_unloaded_entry_stops_voting() -> None:
    _reset_debug()
    debug.set_debug_enabled(True, entry_id="account_a")
    debug.set_debug_enabled(False, entry_id="account_b")
    debug.forget_entry("account_a")
    assert debug.debug_enabled() is False
    _reset_debug()


def test_the_logger_level_follows_the_union() -> None:
    import logging

    _reset_debug()
    logger = logging.getLogger(debug._LOGGER_NAMESPACE)
    debug.set_debug_enabled(True, entry_id="account_a")
    assert logger.level == logging.DEBUG
    debug.set_debug_enabled(False, entry_id="account_a")
    assert logger.level == logging.INFO
    _reset_debug()


# --- the coverage grace clock is per vehicle --------------------------------


def test_a_car_added_later_gets_its_own_grace_window() -> None:
    """A car bought six months in must not be told on day one that a cluster
    has sent nothing: it is the entry that has been monitored for six months,
    not the car."""

    entry_clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    arrived = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert coverage.monitoring_since(entry_clock, arrived) == arrived


def test_a_car_present_all_along_keeps_the_entry_clock() -> None:
    entry_clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    # A selection change restarts the entry clock; the car is older than that
    # and must be judged from the new selection, not from its first sighting.
    arrived = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert coverage.monitoring_since(entry_clock, arrived) == entry_clock


def test_a_record_from_before_first_sightings_were_kept_still_works() -> None:
    entry_clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert coverage.monitoring_since(entry_clock, None) == entry_clock
    assert coverage.monitoring_since(None, None) is None


def test_the_new_cars_window_is_actually_honoured_by_the_report() -> None:
    """End to end over the analysis: same car, same silence, different clock."""

    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    expected = {"electric": ["vehicle.a", "vehicle.b"]}
    labels = {"electric": "Electric"}

    def report(since: datetime):
        return coverage.analyze_coverage(
            vin="WBAEXAMPLE0000000",
            expected_by_section=expected,
            labels=labels,
            seen=set(),
            monitoring_since=since,
            now=now,
            grace_days=7,
        )

    old_entry_clock = report(now - timedelta(days=200))
    just_arrived = report(now - timedelta(days=1))
    assert old_entry_clock.past_grace is True
    assert just_arrived.past_grace is False
    assert just_arrived.overdue_clusters() == []


# --- one account, one entry -------------------------------------------------


def test_setup_refuses_a_second_entry_for_the_same_account() -> None:
    """BMW allows one MQTT connection per GCID and counts the 50-request quota
    per account, and entities are keyed by VIN -- so a second Client ID for an
    account already set up breaks both entries. The Client ID check cannot see
    it; only the GCID that comes back with the tokens can."""

    source = (_PKG / "config_flow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    step = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_step_tokens"
    )
    body = ast.get_source_segment(source, step) or ""
    assert "account_already_configured" in body, (
        "async_step_tokens must abort when another entry already has this GCID"
    )
    assert body.index('"gcid"') < body.index("account_already_configured")


def test_the_duplicate_account_abort_is_translated() -> None:
    import json

    for lang in ("en", "de"):
        doc = json.loads((_PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        assert "account_already_configured" in doc["config"]["abort"], lang


# --- removal leaves nothing retained on the broker --------------------------


def test_removal_clears_every_car_the_entry_ever_had() -> None:
    """Retained MQTT topics outlive the integration: a charge controller would
    go on charging against a frozen state of charge. The stored basic-data
    record is not the full list of cars -- one added after setup can have
    streamed for months without ever appearing in it."""

    source = (_PKG / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    remove = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_remove_entry"
    )
    body = ast.get_source_segment(source, remove) or ""
    assert "async_clear_published" in body
    assert "known_vins(" in body, "the VIN list must come from the shared helper"
    assert "_registered_vins(" in body, (
        "a car that only ever streamed is in no stored record, but it has a device"
    )


# --- a car added to the account after setup ---------------------------------


def _function_source(path: pathlib.Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_a_vehicle_seen_for_the_first_time_is_announced() -> None:
    """The bootstrap runs once per entry, and everything that names a car comes
    from it. Without a first-sight signal, a car added later stays a bare VIN on
    the device page for good."""

    body = _function_source(_PKG / "coordinator.py", "async_handle_message")
    assert "first_sight = vin not in self.data" in body
    # Order matters: setdefault() makes every VIN look familiar.
    assert body.index("first_sight = vin not in self.data") < body.index(
        "self.data.setdefault(vin, {})"
    )
    assert "self.on_new_vehicle(vin)" in body


def test_the_setup_path_wires_the_first_sight_handler() -> None:
    body = _function_source(_PKG / "__init__.py", "async_setup_entry")
    assert "coordinator.on_new_vehicle" in body


def test_adopting_a_new_car_cannot_spend_the_quota_twice() -> None:
    """It costs one of the 50 daily requests. A car BMW answers nothing for
    keeps streaming, so without claiming the attempt up front the failure would
    be retried on every message until the account's quota was gone."""

    body = _function_source(_PKG / "__init__.py", "_async_on_new_vehicle")
    assert "basic_data_attempted" in body
    assert "runtime.basic_data_attempted.add(vin)" in body
    assert body.index("in runtime.basic_data_attempted") < body.index(
        "runtime.basic_data_attempted.add(vin)"
    )
    # The bootstrap already covers every mapped vehicle; both racing would spend
    # two requests on the same car.
    assert "BOOTSTRAP_COMPLETE" in body


def test_upgrading_does_not_hand_existing_cars_a_fresh_grace_window() -> None:
    """The record that predates per-vehicle clocks still says when each car
    turned up -- read it back, or the upgrade looks like first contact and every
    real coverage gap goes quiet for a week."""

    seen = {
        "WBAEXAMPLE0000000": {
            "vehicle.a": "2026-01-05T10:00:00+00:00",
            "vehicle.b": "2026-03-09T08:00:00+00:00",
        },
        "WBAEXAMPLE0000001": {"vehicle.a": "2026-08-01T12:00:00+00:00"},
    }
    recovered = coverage.backfill_first_seen(seen, {})
    assert recovered["WBAEXAMPLE0000000"] == "2026-01-05T10:00:00+00:00"
    assert recovered["WBAEXAMPLE0000001"] == "2026-08-01T12:00:00+00:00"


def test_a_known_first_sighting_is_never_overwritten() -> None:
    seen = {"WBAEXAMPLE0000000": {"vehicle.a": "2026-03-09T08:00:00+00:00"}}
    known = {"WBAEXAMPLE0000000": "2026-01-05T10:00:00+00:00"}
    assert coverage.backfill_first_seen(seen, known) == {}


def test_a_car_that_has_sent_nothing_gets_no_invented_sighting() -> None:
    assert coverage.backfill_first_seen({"WBAEXAMPLE0000000": {}}, {}) == {}
