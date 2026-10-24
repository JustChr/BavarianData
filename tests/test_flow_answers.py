"""What the setup and Configure screens work out, without Home Assistant."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from .conftest import load_module

answers = load_module("flow_answers")
const = load_module("const")
device_flow = load_module("device_flow")
mileage = load_module("mileage")

TODAY = date(2026, 10, 24)


def _report(**kwargs):
    fields = {
        "activated_count": None,
        "added_count": None,
        "activation_ok": False,
        "errors": [],
        "mapped_vehicle_id": None,
    }
    return SimpleNamespace(**{**fields, **kwargs})


# --- setup ------------------------------------------------------------------


def test_a_refused_login_shows_every_piece_bmw_sent() -> None:
    err = device_flow.CardataAuthError(
        "Authorization was denied",
        status=400,
        error_code="access_denied",
        error_description="The user declined",
        correlation_id="abc-123",
    )
    assert answers.auth_error_details(err, "openid cardata:streaming:read") == (
        "Authorization was denied\n"
        "- HTTP status: 400\n"
        "- Error code: access_denied\n"
        "- Description: The user declined\n"
        "- BMW reference: abc-123\n"
        "- Requested scope: openid cardata:streaming:read"
    )


def test_a_bare_refusal_still_names_the_scope() -> None:
    err = device_flow.CardataAuthError("Network down")
    assert answers.auth_error_details(err, "openid") == "Network down\n- Requested scope: openid"


def test_a_login_becomes_the_entrys_data() -> None:
    tokens = {"access_token": "a", "refresh_token": "r", "id_token": "i", "gcid": "g", "x": 1}
    assert answers.token_entry_data("client", tokens, 12.5) == {
        "client_id": "client",
        "access_token": "a",
        "refresh_token": "r",
        "id_token": "i",
        "expires_in": None,
        "scope": None,
        "gcid": "g",
        "token_type": None,
        "received_at": 12.5,
    }


def test_a_working_activation_shows_its_numbers_and_no_error() -> None:
    report = _report(activated_count=42, added_count=3, activation_ok=True, errors=["old"])
    assert answers.activation_done_placeholders(report) == {
        "field_count": "42",
        "added_count": "3",
        "error": "",
    }


def test_a_failed_activation_shows_its_last_error() -> None:
    report = _report(errors=["first", "Portal session expired"])
    assert answers.activation_done_placeholders(report) == {
        "field_count": "the selected",
        "added_count": "0",
        "error": "\n\n⚠️ Portal session expired",
    }


def test_no_report_at_all_reads_as_the_selected_fields() -> None:
    assert answers.activation_done_placeholders(None) == {
        "field_count": "the selected",
        "added_count": "0",
        "error": "",
    }
    assert answers.guided_done_placeholders(None) == {
        "field_count": "the selected",
        "vehicle": "your vehicle",
    }


def test_the_guided_end_shows_the_start_of_the_portals_car_id() -> None:
    report = _report(activated_count=7, mapped_vehicle_id="0123456789abcdef")
    assert answers.guided_done_placeholders(report) == {
        "field_count": "7",
        "vehicle": "01234567…",
    }


def test_clusters_keep_the_catalogues_order() -> None:
    assert answers.in_catalogue_order(["a", "b", "c"], ["c", "a", "x"]) == ["a", "c"]


@pytest.mark.parametrize(
    ("typed", "expected"), [("  id-1 \n", "id-1"), ("", ""), (None, ""), (42, "")]
)
def test_a_typed_client_id_is_trimmed_text(typed, expected) -> None:
    assert answers.typed_client_id(typed) == expected


# --- the per-car screens ----------------------------------------------------


def test_cars_are_listed_by_name_and_twins_get_their_vins_end() -> None:
    named = [
        ("WBA0000000000AAA1", "iX1"),
        ("WBA0000000000BBB2", "i5 eDrive40"),
        ("WBA0000000000CCC3", "iX1"),
    ]
    assert answers.car_labels(named) == [
        ("WBA0000000000BBB2", "i5 eDrive40"),
        ("WBA0000000000AAA1", "iX1 (…AAA1)"),
        ("WBA0000000000CCC3", "iX1 (…CCC3)"),
    ]


def test_shared_values_come_from_their_section_with_two_cars() -> None:
    submitted = {"loss": 5, answers.SHARED_SECTION: {"price": 0.3}}
    assert answers.split_shared(submitted, ("loss",)) == ({"loss": 5}, {"price": 0.3})
    assert answers.SHARED_SECTION in submitted  # the form's values are left alone


def test_with_one_car_every_key_not_the_cars_is_shared() -> None:
    assert answers.split_shared({"loss": 5, "price": 0.3}, ("loss",)) == (
        {"loss": 5},
        {"price": 0.3},
    )


@pytest.mark.parametrize(
    ("shared", "expected"),
    [
        ({}, None),
        ({const.OPTION_PRICE_MODE: "none"}, None),
        ({const.OPTION_PRICE_MODE: "fixed"}, "price_required"),
        ({const.OPTION_PRICE_MODE: "fixed", const.OPTION_PRICE_FIXED: 0}, None),
        ({const.OPTION_PRICE_MODE: "entity"}, "price_entity_required"),
        ({const.OPTION_PRICE_MODE: "entity", const.OPTION_PRICE_ENTITY: "sensor.p"}, None),
    ],
)
def test_a_tariff_needs_its_price(shared, expected) -> None:
    assert answers.price_error(shared) == expected


@pytest.mark.parametrize(
    ("detection", "track", "follows"),
    [
        (const.TRIP_DETECTION_REPORTS, True, True),
        (const.TRIP_DETECTION_REPORTS, False, False),
        (const.TRIP_DETECTION_LIVE, True, False),
    ],
)
def test_the_phone_screen_follows_only_a_reporting_car_that_records_its_route(
    detection, track, follows
) -> None:
    values = {const.OPTION_TRIP_DETECTION: detection, const.OPTION_TRIP_TRACK: track}
    assert answers.phone_route_follows(values) is follows


def test_every_car_shares_one_vehicles_key() -> None:
    blocks = ["vehicles:\n  - name: a\n", "vehicles:\n  - name: b\n"]
    assert answers.evcc_vehicles(blocks) == "vehicles:\n  - name: a\n\n  - name: b"


def test_no_car_seen_yet_says_so() -> None:
    assert answers.evcc_vehicles([]).startswith("(no vehicle seen yet")


def test_the_units_line_names_fuel_only_when_asked() -> None:
    assert answers.units_line([("i5", "km", None), ("530e", "mi", "l_mpg_uk")]) == (
        "i5: km\n530e: mi · mpg (UK)"
    )
    assert answers.units_line([]) == "—"


# --- mileage allowance ------------------------------------------------------


def test_distances_convert_between_miles_and_km() -> None:
    assert answers.to_km(10000, True) == 16093.4
    assert answers.to_km("15000", False) == 15000.0
    assert answers.to_km("", True) is None and answers.to_km(None, False) is None
    assert answers.to_display(16093.44, True) == 10000
    assert answers.to_display(14999.6, False) == 15000
    assert answers.to_display(None, True) is None


def test_a_typed_allowance_is_stored_in_km() -> None:
    typed = {
        mileage.KEY_START: "2026-01-01",
        mileage.KEY_PER_YEAR: 10000,
        mileage.KEY_START_KM: "",
    }
    assert answers.mileage_typed(typed, True) == {
        mileage.KEY_START: "2026-01-01",
        mileage.KEY_END: None,
        mileage.KEY_PER_YEAR: 16093.4,
        mileage.KEY_START_KM: None,
    }


def test_a_fresh_lease_runs_three_years_from_today() -> None:
    assert answers.mileage_prefill({}, mileage.KIND_LEASE, TODAY) == {
        mileage.KEY_START: "2026-10-24",
        mileage.KEY_END: "2029-10-23",
    }
    assert answers.mileage_prefill({}, mileage.KIND_YEARLY, TODAY) == {
        mileage.KEY_START: "2026-10-24"
    }


def test_a_stored_allowance_is_not_prefilled() -> None:
    stored = {mileage.KEY_START: "2025-03-01", mileage.KEY_END: "2028-02-29"}
    assert answers.mileage_prefill(stored, mileage.KIND_LEASE, TODAY) == stored


def _allowance(**kwargs):
    return {
        mileage.KEY_START: "2026-01-01",
        mileage.KEY_END: "2028-12-31",
        mileage.KEY_PER_YEAR: 15000,
        mileage.KEY_START_KM: 100,
        **kwargs,
    }


def test_a_lease_is_stored_as_typed() -> None:
    stored, errors = answers.mileage_to_store(mileage.KIND_LEASE, _allowance(), TODAY)
    assert errors == {}
    assert stored == {mileage.KEY_KIND: mileage.KIND_LEASE, **_allowance()}


def test_a_yearly_limit_starts_at_its_latest_anniversary_and_has_no_end() -> None:
    values = _allowance(**{mileage.KEY_START: "2023-03-01"})
    stored, errors = answers.mileage_to_store(mileage.KIND_YEARLY, values, TODAY)
    assert errors == {}
    assert stored[mileage.KEY_START] == "2026-03-01"
    assert stored[mileage.KEY_END] is None


def test_a_yearly_limit_starting_later_keeps_its_start() -> None:
    values = _allowance(**{mileage.KEY_START: "2027-01-01"})
    stored, _errors = answers.mileage_to_store(mileage.KIND_YEARLY, values, TODAY)
    assert stored[mileage.KEY_START] == "2027-01-01"


def test_an_allowance_needs_a_yearly_distance() -> None:
    values = _allowance(**{mileage.KEY_PER_YEAR: None})
    assert answers.mileage_to_store(mileage.KIND_LEASE, values, TODAY)[1] == {
        mileage.KEY_PER_YEAR: "invalid_int"
    }


def test_a_lease_cannot_end_before_it_starts() -> None:
    values = _allowance(**{mileage.KEY_END: "2025-12-31"})
    assert answers.mileage_to_store(mileage.KIND_LEASE, values, TODAY)[1] == {
        mileage.KEY_END: "mileage_end_before_start"
    }
