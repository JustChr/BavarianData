"""What the services work out, without Home Assistant."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from .conftest import load_module

answers = load_module("service_answers")
refusal = load_module("refusal")
evcc = load_module("evcc")
pricing = load_module("history.pricing")
activation = load_module("stream_activation")
descriptors = load_module("descriptors")

VIN = "WBAEXAMPLE0000001"
BERLIN = timezone(timedelta(hours=2))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), (5.0, 5), ("7", 7), ("many", None), ([], None)],
)
def test_a_limit_is_a_whole_number_or_none(value, expected) -> None:
    assert answers.as_limit(value) == expected


def test_bmw_timestamps_are_utc_with_a_z() -> None:
    assert answers.cardata_timestamp(datetime(2026, 10, 24, 10, 0, tzinfo=BERLIN)) == (
        "2026-10-24T08:00:00Z"
    )
    naive = datetime(2026, 10, 24, 10, 0)
    assert answers.as_cardata_timestamp(naive, naive, BERLIN) == "2026-10-24T08:00:00Z"
    assert answers.as_cardata_timestamp(None, None, BERLIN) is None
    assert answers.as_cardata_timestamp("", None, BERLIN) is None
    # Not a date we can read: BMW gets it as typed, and says what's wrong.
    assert answers.as_cardata_timestamp("yesterday", None, BERLIN) == "yesterday"


def test_a_bare_date_bound_covers_the_whole_day() -> None:
    day = date(2026, 8, 31)
    assert answers.day_bound(day, end_of_day=False) == datetime(2026, 8, 31, tzinfo=timezone.utc)
    end = answers.day_bound(day, end_of_day=True)
    assert end.date() == day and end.hour == 23 and end.tzinfo is timezone.utc


def test_an_explicit_bound_is_taken_at_its_word() -> None:
    aware = datetime(2026, 8, 31, 12, 0, tzinfo=BERLIN)
    assert answers.day_bound(aware, end_of_day=True) == datetime(
        2026, 8, 31, 10, 0, tzinfo=timezone.utc
    )
    naive = datetime(2026, 8, 31, 12, 0)
    assert answers.day_bound(naive, end_of_day=True) == naive.replace(tzinfo=timezone.utc)


def test_the_month_argument() -> None:
    today = date(2026, 10, 24)
    assert answers.month_arg(None, today) == (2026, 10)
    assert answers.month_arg("2026-03", today) == (2026, 3)
    with pytest.raises(refusal.Refusal, match="YYYY-MM"):
        answers.month_arg("March", today)
    with pytest.raises(refusal.Refusal, match="no month 13"):
        answers.month_arg("2026-13", today)
    assert answers.previous_month(2026, 1) == (2025, 12)
    assert answers.previous_month(2026, 10) == (2026, 9)


def test_a_trip_id_names_its_car() -> None:
    other = "WBAEXAMPLE0000002"
    assert answers.trip_vin(f"{other}-20261024T0800", [VIN, other]) == other
    assert answers.trip_vin("someone-else", [VIN]) is None


def test_a_backfilled_session_is_priced_only_on_a_fixed_tariff() -> None:
    fixed = pricing.PricingConfig(mode=pricing.MODE_FIXED, fixed_price=0.30, currency="EUR")
    cost = answers.backfill_cost(fixed)(10.0)
    assert cost["amount"] == pytest.approx(3.0)
    assert answers.backfill_cost(fixed)(0) is None
    assert answers.backfill_cost(pricing.PricingConfig(mode=pricing.MODE_ENTITY))(10.0) is None


def test_an_open_drive_belongs_to_the_window_holding_now() -> None:
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert answers.window_holds(None, None, now)
    assert answers.window_holds(now - timedelta(days=1), now + timedelta(days=1), now)
    assert not answers.window_holds(None, now - timedelta(days=1), now)
    assert not answers.window_holds(now + timedelta(hours=1), None, now)


def test_the_export_files() -> None:
    assert answers.export_stem(VIN, "2026-10") == f"bavariandata-{VIN.lower()}-2026-10"
    assert answers.export_stem(None, "2026-10") == "bavariandata-vehicle-2026-10"
    both = answers.csv_files("stem", [], [], "both", lambda value: value)
    assert [f["filename"] for f in both] == ["stem-charging.csv", "stem-trips.csv"]
    assert all(f["mime"] == "text/csv" and f["rows"] == 0 for f in both)
    assert [f["filename"] for f in answers.csv_files("s", [], [], "trips", str)] == ["s-trips.csv"]
    assert answers.html_file("stem", 3, "<html>") == {
        "filename": "stem.html",
        "mime": "text/html",
        "rows": 3,
        "content": "<html>",
    }


def test_the_evcc_block_names_the_live_topics() -> None:
    config = evcc.BridgeConfig.from_options({"bridge_enabled": True})
    block = answers.evcc_config(
        vin=VIN,
        config=config,
        live=(),
        mqtt_available=False,
        title="i5",
        capacity_kwh=81.2,
    )
    assert block["vin"] == VIN
    assert block["mqtt_available"] is False
    assert block["published_topics"] == []
    assert len(block["topics"]) == len(evcc.ALL_TOPICS)
    assert all(topic.startswith(f"{config.prefix}/{VIN}/") for topic in block["topics"])
    assert VIN in block["yaml"]


def test_what_the_portal_is_asked_to_stream() -> None:
    assert answers.activation_attributes({"attributes": ["a.b"]}, ["tire"]) == ["a.b"]
    sections = sorted(descriptors.default_sections())[:1]
    assert answers.activation_attributes({"sections": sections}, None) == (
        descriptors.descriptors_for_sections(sections)
    )
    assert answers.activation_attributes({}, sections) == (
        descriptors.descriptors_for_sections(sections)
    )
    assert answers.activation_attributes({}, None) == list(activation.DEFAULT_STREAM_ATTRIBUTES)
    with pytest.raises(refusal.Refusal, match="No attributes"):
        answers.activation_attributes({"sections": ["no-such-cluster"]}, None)


def test_a_portal_failure_says_what_to_do() -> None:
    assert "fresh session" in str(answers.activation_error("auth", "401"))
    assert "mapped_vehicle_id" in str(answers.activation_error("not_found", "404"))
    assert str(answers.activation_error("server", "BMW 503")) == "BMW 503"
    assert answers.activation_error("server", "BMW 503").placeholders == {"error": "BMW 503"}
