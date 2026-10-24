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
    buckets, currency = stats.hourly_cost([_session(cost={"amount": 4.0, "currency": "EUR"})])
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


def test_report_shows_miles_when_the_account_counts_in_them():
    # Configure -> Units (#183): a UK owner's report in miles, every distance and
    # every per-distance figure, while the trips themselves stay in km.
    trip = _trip(distance_km=40.0, energy_kwh=8.0, soc_start=80.0, soc_end=70.0)
    ledger = {**summary.summarise([_session()]), "cost_per_100km": 5.0}
    html = export.month_report_html(
        month="2026-07",
        vehicle="i4",
        sessions=[_session()],
        trips=[trip],
        charging_summary=ledger,
        driving=summary.driving_summary([trip]),
        distance_unit="mi",
    )
    assert "24.9 mi" in html  # 40 km
    assert "Cost per 100 mi" in html and "8.05" in html  # 5.00 per 100 km
    assert "Consumption (kWh/100 mi)" in html
    assert " km<" not in html and "100 km" not in html
    assert trip.distance_km == 40.0


def test_report_never_claims_tax_compliance():
    for lang, needle in (("en", "not a tax-compliant"), ("de", "kein")):
        html = export.month_report_html(
            month="2026-07", vehicle="i5", sessions=[], trips=[_trip()], lang=lang
        )
        assert needle in html


def test_report_speaks_german():
    html = export.month_report_html(month="2026-07", vehicle="i5", sessions=[], trips=[], lang="de")
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


def test_report_says_what_the_solar_share_is_a_share_of():
    # September on the i5: 197 kWh charged, but only 99 kWh of it had a mix
    # (the rest predates the house sensors). "76 %" beside "197 kWh" read as 76 %
    # of the month; the note names the measured total it is really a share of.
    sessions = [
        _session(energy_kwh=98.0, energy_mix=None),
        _session(energy_kwh=99.0, energy_mix={"pv": 75.0, "grid": 24.0, "solar_percent": 75.8}),
    ]
    html = export.month_report_html(
        month="2026-09",
        vehicle="i5",
        sessions=sessions,
        trips=[],
        charging_summary=summary.summarise(sessions),
    )
    assert "75.8 % <small>75.0 of 99.0 kWh measured</small>" in html


def test_report_numbers_read_right_in_the_reader_s_language():
    # October on the i5 printed "297.752 kWh" in every language: three decimals
    # nobody needs, and to a German reader almost three hundred thousand.
    sessions = [
        _session(energy_kwh=297.752, cost={"amount": 36.57, "currency": "EUR"}),
    ]
    for lang, energy, cost in (
        ("en", "297.8 kWh", "36.57 EUR"),
        ("de", "297,8 kWh", "36,57 EUR"),
        ("fr", "297,8 kWh", "36,57 EUR"),
    ):
        html = export.month_report_html(
            month="2026-10",
            vehicle="i5",
            sessions=sessions,
            trips=[_trip()],
            charging_summary=summary.summarise(sessions),
            driving=summary.driving_summary([_trip()]),
            lang=lang,
        )
        assert energy in html and cost in html, lang
        assert "297.752" not in html and "297,752" not in html


def test_report_handles_an_empty_month():
    html = export.month_report_html(month="2026-07", vehicle="i5", sessions=[], trips=[])
    assert "No charging sessions" in html and "No trips" in html


def test_report_has_every_label_in_every_language():
    # A missing label raises KeyError mid-render, so a partial table would only
    # surface when a user in that language exported a month.
    for lang, labels in export.STRINGS.items():
        assert set(labels) == set(export.STRINGS["en"]), lang


def test_report_languages_match_the_service_selector():
    import pathlib

    import yaml

    pkg = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"
    services = yaml.safe_load((pkg / "services.yaml").read_text(encoding="utf-8"))
    options = services["export_history"]["fields"]["language"]["selector"]["select"]["options"]
    assert set(options) == set(export.STRINGS)


def test_the_home_series_leaves_out_charges_made_elsewhere():
    """#97: the Energy dashboard reads a device as part of the house's use."""

    def charge(hours: int, kwh: float, location) -> ChargingSession:
        start = START + timedelta(hours=hours)
        return ChargingSession(
            vin="WBY1",
            start=start,
            end=start + timedelta(minutes=20),
            energy_kwh=kwh,
            location=location,
        )

    sessions = [
        charge(0, 10.0, {"zone": "Home"}),
        charge(3, 40.0, {"zone": None, "address": "A9 Stammham"}),
        charge(6, 5.0, {"zone": "Work"}),
        charge(9, 2.0, None),  # no position: counts as home, like the mix
    ]
    assert sum(stats.hourly_energy(sessions).values()) == 57.0
    assert sum(stats.hourly_energy_at_home(sessions, "Home").values()) == 12.0


def test_report_names_the_plug_side_figure_an_assumed_place_and_a_floor():
    """The rarer tiles: consumption at the plug beside the battery's, a place
    the car didn't report, a private share, and a total that is a floor."""

    session = _session(end=None, location={"zone": "Home"}, location_assumed=True)
    driving = {
        "energy_balance": {"kwh_per_100km": 19.0, "source": "grid"},
        "avg_consumption_kwh_per_100km": 17.0,
        "split": {"private_km": 12.0},
    }
    html = export.month_report_html(
        month="2026-07",
        vehicle="i5",
        sessions=[session],
        trips=[_trip()],
        charging_summary={**summary.summarise([session]), "partial": True},
        driving={**summary.driving_summary([_trip()]), **driving},
    )
    assert "at the plug" in html and "17.0 at the battery" in html
    assert 'Home <span class="sub">(assumed)</span>' in html
    assert "Private" in html
    assert export.STRINGS["en"]["footnote_partial"][:30] in html


def test_report_says_where_a_battery_side_figure_comes_from():
    driving = {"energy_balance": {"kwh_per_100km": 18.0, "source": "battery"}}
    html = export.month_report_html(
        month="2026-07", vehicle="i5", sessions=[], trips=[_trip()], driving=driving
    )
    assert "at the battery" in html and "at the plug" not in html


def test_a_session_still_charging_exports_an_empty_end():
    header, row = _rows(export.sessions_csv([_session(end=None)]))[:2]
    assert row[header.index("end")] == ""


def test_report_shows_the_battery_figure_alone_without_a_balance():
    driving = {
        **summary.driving_summary([_trip()]),
        "avg_consumption_kwh_per_100km": 17.5,
        "recuperation_kwh_per_100km": 3.25,
    }
    driving.pop("energy_balance", None)
    html = export.month_report_html(
        month="2026-07", vehicle="i5", sessions=[], trips=[_trip()], driving=driving
    )
    assert "17.5" in html and "at the plug" not in html
    assert "Recuperated" in html
