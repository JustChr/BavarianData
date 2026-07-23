"""Unit tests for the trip (Fahrtenbuch) layer's Home Assistant-free maths.

The trip record, its retention, the zone-pair classifier, the "month in review"
aggregation and the geocode formatter are all covered here without an HA install
-- the same discipline as ``test_history.py``. ``history.store`` and the
network path of ``history.geocoding`` are excluded: they touch HA / the wire.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .conftest import load_module

trips = load_module("history.trips")
classify = load_module("history.classify")
summary = load_module("history.summary")
trip_builder = load_module("history.trip_builder")
geocoding = load_module("history.geocoding")

Trip = trips.Trip
place = trips.place
TripBuilder = trip_builder.TripBuilder

START = datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)  # a Monday


def _trip(**overrides) -> Trip:
    data = {
        "vin": "WBY1",
        "start": START,
        "end": START + timedelta(minutes=30),
        "distance_km": 20.0,
    }
    data.update(overrides)
    return Trip(**data)


# --- record ----------------------------------------------------------------


def test_round_trips_through_json():
    original = _trip(
        start_place=place(zone="Home"),
        end_place=place(address="Marienplatz, München"),
        soc_start=80.0,
        soc_end=72.0,
        energy_kwh=4.0,
        classification="commute",
        classification_source="auto",
        stats={"accel_stars": 4.0, "brake_stars": 3.0, "recuperation_kwh": 1.2},
    )
    restored = Trip.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored is not None
    assert restored.to_dict() == original.to_dict()
    assert restored.start == original.start


def test_unusable_records_are_dropped():
    assert Trip.from_dict({"start": START.isoformat()}) is None
    assert Trip.from_dict({"vin": "WBY1"}) is None
    assert Trip.from_dict({"vin": "WBY1", "start": "not-a-date"}) is None


def test_unknown_classification_is_discarded_on_load():
    restored = Trip.from_dict(
        {"vin": "WBY1", "start": START.isoformat(), "classification": "leisure"}
    )
    assert restored is not None
    assert restored.classification is None


def test_derived_values():
    trip = _trip(soc_start=80.0, soc_end=70.0, energy_kwh=5.0, distance_km=25.0)
    assert trip.duration_s == 1800
    assert trip.soc_delta == -10.0
    assert trip.consumption_kwh_per_100km == 20.0


def test_consumption_needs_distance_and_energy():
    assert _trip(energy_kwh=None).consumption_kwh_per_100km is None
    assert _trip(distance_km=0.0, energy_kwh=4.0).consumption_kwh_per_100km is None


def test_place_label_prefers_zone_then_address():
    assert place(zone="Home")["label"] == "Home"
    assert place(address="Somewhere")["label"] == "Somewhere"
    assert place()["label"] == "Unknown"
    assert "lat" not in place(zone="Home")  # never store coordinates


# --- retention -------------------------------------------------------------


def test_prune_trims_by_window_and_cap():
    now = START + timedelta(days=400)
    old = _trip(start=START)  # >12 months before "now"
    recent = _trip(start=now - timedelta(days=5))
    kept = trips.prune_trips(
        [old, recent], now=now, retain_months=12, max_entries=100
    )
    assert kept == [recent]


def test_merge_replaces_by_id():
    first = _trip(classification=None)
    reclassified = _trip(classification="business", classification_source="user")
    merged = trips.merge_trip([first], reclassified)
    assert len(merged) == 1
    assert merged[0].classification == "business"


# --- classification --------------------------------------------------------


def test_home_to_work_is_a_commute_either_direction():
    home, work = place(zone="Home"), place(zone="Work")
    assert classify.classify_trip(home, work, home="home", work="Work") == "commute"
    assert classify.classify_trip(work, home, home="Home", work="work") == "commute"


def test_known_but_non_commute_is_private():
    assert (
        classify.classify_trip(
            place(zone="Home"), place(zone="Gym"), home="Home", work="Work"
        )
        == "private"
    )


def test_unknown_endpoint_stays_unclassified():
    assert (
        classify.classify_trip(
            place(zone="Home"), place(address="Somewhere"), home="Home", work="Work"
        )
        is None
    )
    assert classify.classify_trip(None, None, home="Home", work="Work") is None


def test_classifier_never_invents_business():
    # business is only ever reachable through the manual override.
    result = classify.classify_trip(
        place(zone="Home"), place(zone="Work"), home="Home", work=None
    )
    assert result != "business"


# --- builder ---------------------------------------------------------------


def test_builder_distance_from_odometer_delta():
    builder = TripBuilder("WBY1", START, mileage_start=1000.0, soc_start=80.0)
    trip = builder.close(
        START + timedelta(minutes=20), mileage_end=1023.4, soc_end=74.0
    )
    assert trip.distance_km == 23.4
    assert trip.soc_delta == -6.0


def test_builder_falls_back_to_bmw_distance():
    builder = TripBuilder("WBY1", START, mileage_start=1000.0)
    # Odometer unchanged -> use BMW's travelled distance instead.
    trip = builder.close(
        START + timedelta(minutes=20), mileage_end=1000.0, travelled_km=12.0
    )
    assert trip.distance_km == 12.0


def test_noise_trip_detection():
    assert trip_builder.is_noise_trip(_trip(distance_km=0.2)) is True
    assert trip_builder.is_noise_trip(_trip(distance_km=5.0)) is False
    # Unknown distance falls back to a duration floor.
    brief = _trip(distance_km=None, end=START + timedelta(seconds=30))
    assert trip_builder.is_noise_trip(brief) is True


# --- month in review -------------------------------------------------------


def _classified(km, cls, **extra):
    return _trip(distance_km=km, classification=cls, **extra)


def test_driving_summary_split_and_totals():
    month = [
        _classified(30.0, "commute", start=START),
        _classified(10.0, "commute", start=START + timedelta(days=1)),
        _classified(20.0, "private", start=START + timedelta(days=2)),
        _classified(5.0, None, start=START + timedelta(days=3)),
    ]
    result = summary.driving_summary(month)
    assert result["total_km"] == 65.0
    assert result["trip_count"] == 4
    assert result["split"]["commute_km"] == 40.0
    assert result["split"]["private_km"] == 20.0
    assert result["split"]["unclassified_km"] == 5.0
    assert result["split"]["commute_percent"] == round(40 / 65 * 100, 1)


def test_driving_summary_consumption_best_worst():
    month = [
        _trip(start=START, distance_km=100.0, energy_kwh=15.0),  # 15/100km (best)
        _trip(start=START + timedelta(days=1), distance_km=100.0, energy_kwh=25.0),
    ]
    result = summary.driving_summary(month)
    assert result["best_trip"]["consumption"] == 15.0
    assert result["worst_trip"]["consumption"] == 25.0
    assert result["avg_consumption_kwh_per_100km"] == 20.0


def test_driving_summary_recuperation_and_style():
    month = [
        _trip(start=START, stats={"accel_stars": 4.0, "brake_stars": 2.0,
                                  "recuperation_kwh": 1.0}),
        _trip(start=START + timedelta(days=8), stats={"accel_stars": 5.0,
                                                      "recuperation_kwh": 2.0}),
    ]
    result = summary.driving_summary(month)
    assert result["recuperation_kwh"] == 3.0
    # trip1 score = mean(4,2)=3; trip2 score = 5 -> overall mean 4.0
    assert result["style_score"] == 4.0
    # two different ISO weeks -> two trend points, oldest first
    assert [pt["score"] for pt in result["style_trend"]] == [3.0, 5.0]


def test_driving_summary_top_destinations_skip_unknown():
    month = [
        _trip(start=START, end_place=place(zone="Work")),
        _trip(start=START + timedelta(days=1), end_place=place(zone="Work")),
        _trip(start=START + timedelta(days=2), end_place=place()),  # Unknown
    ]
    result = summary.driving_summary(month)
    assert result["top_destinations"] == [{"label": "Work", "count": 2}]


def test_driving_summary_month_over_month_and_cost():
    month = [_trip(start=START, distance_km=100.0)]
    prev = [_trip(start=START - timedelta(days=31), distance_km=80.0)]
    result = summary.driving_summary(
        month, prev_trips=prev, cost_per_100km=8.0, currency="EUR"
    )
    assert result["mom_delta_km"] == 20.0
    assert result["mom_delta_percent"] == 25.0
    assert result["estimated_cost"] == {"amount": 8.0, "currency": "EUR"}


def test_driving_summary_omits_cost_without_tariff():
    result = summary.driving_summary([_trip(distance_km=50.0)])
    assert result["estimated_cost"] is None


def test_trips_in_month_uses_start():
    inside = _trip(start=datetime(2026, 7, 31, 23, 0, tzinfo=timezone.utc))
    outside = _trip(start=datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc))
    found = summary.trips_in_month([inside, outside], year=2026, month=7)
    assert found == [inside]


# --- geocode formatting ----------------------------------------------------


def test_format_address_prefers_road_and_city():
    payload = {"address": {"road": "Marienplatz", "city": "München"}}
    assert geocoding.format_address(payload) == "Marienplatz, München"


def test_format_address_falls_back_to_display_name():
    payload = {"display_name": "1, Some Road, District, City, Country"}
    assert geocoding.format_address(payload) == "1, Some Road"


def test_format_address_none_when_empty():
    assert geocoding.format_address(None) is None
    assert geocoding.format_address({}) is None
