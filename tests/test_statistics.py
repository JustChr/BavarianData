"""Unit tests for the statistics-backfill and export layers (roadmap Phase 4).

Both are Home Assistant-free by design -- ``history.stats`` decides which hour a
kilowatt-hour is attributed to, and ``history.export`` turns records into files a
user keeps -- so both are covered here without an HA install, the same discipline
as ``test_history.py`` and ``test_trips.py``. ``history.backfill`` is excluded:
it is the thin recorder glue over ``stats``.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from .conftest import load_module

models = load_module("history.models")
trips_mod = load_module("history.trips")
stats = load_module("history.stats")
export = load_module("history.export")
summary = load_module("history.summary")

ChargingSession = models.ChargingSession
Trip = trips_mod.Trip
place = trips_mod.place

# 10:30 UTC on a Monday: deliberately mid-hour, so a spread has to split.
START = datetime(2026, 7, 6, 10, 30, tzinfo=timezone.utc)


def _hour(h: int, day: int = 6) -> datetime:
    return datetime(2026, 7, day, h, 0, tzinfo=timezone.utc)


def _session(**overrides) -> ChargingSession:
    data = {
        "vin": "WBY1",
        "start": START,
        "end": START + timedelta(hours=1),
        "energy_kwh": 10.0,
    }
    data.update(overrides)
    return ChargingSession(**data)


def _trip(**overrides) -> Trip:
    data = {
        "vin": "WBY1",
        "start": START,
        "end": START + timedelta(minutes=30),
        "distance_km": 20.0,
    }
    data.update(overrides)
    return Trip(**data)


# --- hourly bucketing ------------------------------------------------------


def test_spread_splits_across_the_hours_it_spans():
    # 10:30 -> 11:30 is half in each hour.
    buckets = stats.spread(START, START + timedelta(hours=1), 10.0)
    assert buckets == {_hour(10): 5.0, _hour(11): 5.0}


def test_spread_weights_partial_hours():
    # 10:30 -> 12:00 = 30 min + 60 min, so a third and two thirds.
    buckets = stats.spread(START, _hour(12), 9.0)
    assert round(buckets[_hour(10)], 6) == 3.0
    assert round(buckets[_hour(11)], 6) == 6.0


def test_spread_total_is_always_exact():
    buckets = stats.spread(START, START + timedelta(hours=7, minutes=13), 43.21)
    assert round(sum(buckets.values()), 9) == 43.21


def test_spread_collapses_records_with_no_usable_timeline():
    # Open-ended, zero-length and implausibly long records all land whole in the
    # starting hour rather than being dropped or smeared over days.
    assert stats.spread(START, None, 5.0) == {_hour(10): 5.0}
    assert stats.spread(START, START, 5.0) == {_hour(10): 5.0}
    assert stats.spread(START, START + timedelta(days=5), 5.0) == {_hour(10): 5.0}


def test_spread_ignores_empty_amounts():
    assert stats.spread(START, START + timedelta(hours=1), None) == {}
    assert stats.spread(START, START + timedelta(hours=1), 0.0) == {}


def test_spread_normalises_to_utc_hours():
    local = datetime(2026, 7, 6, 12, 30, tzinfo=timezone(timedelta(hours=2)))
    buckets = stats.spread(local, local + timedelta(minutes=20), 3.0)
    assert list(buckets) == [_hour(10)]


def test_measured_grid_energy_wins_over_battery_side():
    session = _session(energy_kwh=10.0, grid_kwh=11.4)
    assert stats.session_energy_kwh(session) == 11.4
    assert stats.session_energy_kwh(_session(grid_kwh=None)) == 10.0


def test_hourly_energy_accumulates_overlapping_sessions():
    buckets = stats.hourly_energy(
        [
            _session(),  # 5 kWh in each of 10:00 and 11:00
            _session(start=_hour(11), end=_hour(12), energy_kwh=4.0),
        ]
    )
    assert buckets[_hour(10)] == 5.0
    assert buckets[_hour(11)] == 9.0


def test_hourly_cost_reports_its_currency():
    buckets, currency = stats.hourly_cost(
        [_session(cost={"amount": 4.0, "currency": "EUR"})]
    )
    assert currency == "EUR"
    assert round(sum(buckets.values()), 6) == 4.0


def test_hourly_cost_refuses_mixed_currencies():
    # Summing euros and pounds into one meter would be worse than no series.
    buckets, currency = stats.hourly_cost(
        [
            _session(cost={"amount": 4.0, "currency": "EUR"}),
            _session(start=_hour(14), cost={"amount": 3.0, "currency": "GBP"}),
        ]
    )
    assert buckets == {}
    assert currency is None


def test_hourly_cost_skips_sessions_without_one():
    buckets, currency = stats.hourly_cost([_session(cost=None), _session(cost={})])
    assert buckets == {}
    assert currency is None


def test_hourly_distance_uses_trip_distance():
    buckets = stats.hourly_distance([_trip(), _trip(start=_hour(14), end=_hour(15))])
    assert round(sum(buckets.values()), 6) == 40.0


def test_cumulative_produces_a_monotonic_meter():
    rows = stats.cumulative({_hour(11): 2.0, _hour(10): 3.0, _hour(12): 1.5})
    assert [row["start"] for row in rows] == [_hour(10), _hour(11), _hour(12)]
    assert [row["sum"] for row in rows] == [3.0, 5.0, 6.5]
    # No real meter stands behind these, so state mirrors sum by construction.
    assert all(row["state"] == row["sum"] for row in rows)


def test_cumulative_of_nothing_is_nothing():
    assert stats.cumulative({}) == []


def test_full_series_totals_match_the_records():
    sessions = [
        _session(energy_kwh=10.0),
        _session(start=_hour(20), end=_hour(22), energy_kwh=7.5),
        _session(start=_hour(9, day=7), end=_hour(10, day=7), energy_kwh=3.25),
    ]
    rows = stats.cumulative(stats.hourly_energy(sessions))
    assert rows[-1]["sum"] == 20.75


# --- CSV export ------------------------------------------------------------


def _rows(text: str) -> list[list[str]]:
    assert text.startswith(export.BOM), "Excel needs the BOM to read UTF-8"
    return list(csv.reader(io.StringIO(text[len(export.BOM) :])))


def test_sessions_csv_keeps_both_energy_figures_apart():
    rows = _rows(export.sessions_csv([_session(grid_kwh=11.4)]))
    header, row = rows[0], rows[1]
    assert "energy_kwh_battery" in header and "grid_kwh_measured" in header
    assert row[header.index("energy_kwh_battery")] == "10"
    assert row[header.index("grid_kwh_measured")] == "11.4"


def test_sessions_csv_carries_cost_and_its_provenance():
    session = _session(
        cost={"amount": 4.21, "currency": "EUR", "source": "tariff", "partial": True},
        location={"zone": "Home"},
        location_assumed=True,
    )
    header, row = _rows(export.sessions_csv([session]))
    assert row[header.index("cost")] == "4.21"
    assert row[header.index("cost_source")] == "tariff"
    assert row[header.index("cost_partial")] == "yes"
    assert row[header.index("zone")] == "Home"
    assert row[header.index("location_assumed")] == "yes"


def test_trips_csv_exports_places_not_coordinates():
    trip = _trip(
        start_place=place(zone="Home"),
        end_place=place(address="Marienplatz, München"),
        classification="commute",
    )
    header, row = _rows(export.trips_csv([trip]))
    assert row[header.index("from")] == "Home"
    assert row[header.index("to")] == "Marienplatz, München"
    assert row[header.index("classification")] == "commute"
    # Privacy invariant: a coordinate must never reach a file the user shares.
    # Asserted on the columns, not the text -- "Marienplatz" contains "lat".
    assert not {"lat", "lon", "latitude", "longitude"} & set(header)


def test_csv_of_an_empty_month_is_still_a_valid_file():
    rows = _rows(export.sessions_csv([]))
    assert len(rows) == 1 and rows[0][0] == "id"


def test_csv_localizes_timestamps():
    plus_two = timezone(timedelta(hours=2))
    header, row = _rows(
        export.sessions_csv([_session()], localize=lambda dt: dt.astimezone(plus_two))
    )
    assert row[header.index("start")] == "2026-07-06 12:30"


# --- HTML report -----------------------------------------------------------


def test_report_is_self_contained_and_printable():
    html = export.month_report_html(
        month="2026-07",
        vehicle="i5 M60",
        sessions=[_session(cost={"amount": 4.21, "currency": "EUR"})],
        trips=[_trip()],
        charging_summary=summary.summarise([_session()]),
        driving=summary.driving_summary([_trip()]),
        now=START,
    )
    assert "<style>" in html and "@media print" in html
    # No external fetch: a strict CSP or an offline browser must not break it.
    assert "http://" not in html and "https://" not in html
    assert "i5 M60" in html and "2026-07" in html


def test_report_never_claims_tax_compliance():
    for lang, needle in (("en", "not a tax-compliant"), ("de", "kein")):
        html = export.month_report_html(
            month="2026-07", vehicle="i5", sessions=[], trips=[_trip()], lang=lang
        )
        assert needle in html


def test_report_speaks_german():
    html = export.month_report_html(
        month="2026-07", vehicle="i5", sessions=[], trips=[], lang="de"
    )
    assert "Fahrt- und Ladebericht" in html
    assert "keine Ladevorgänge" in html


def test_report_escapes_place_names():
    # Place names are reverse-geocoded strings from a third party.
    html = export.month_report_html(
        month="2026-07",
        vehicle="i5",
        sessions=[],
        trips=[_trip(end_place=place(address="<script>alert(1)</script>"))],
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_omits_cost_it_does_not_have():
    html = export.month_report_html(
        month="2026-07",
        vehicle="i5",
        sessions=[_session()],
        trips=[],
        charging_summary=summary.summarise([_session()]),
    )
    # Rule 4: no cost tile before a tariff has produced one.
    assert "Cost</div>" not in html


def test_report_handles_an_empty_month():
    html = export.month_report_html(
        month="2026-07", vehicle="i5", sessions=[], trips=[]
    )
    assert "No charging sessions" in html and "No trips" in html
