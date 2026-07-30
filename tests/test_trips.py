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


def test_unknown_endpoint_takes_the_default_type():
    # Whether we happen to know where the car was says nothing about the trip's
    # purpose, so an unplaceable trip is filed as the default like any other
    # non-commute -- correctable on the card either way.
    for start, end in (
        (place(zone="Home"), place(address="Somewhere")),
        (None, None),
    ):
        assert classify.classify_trip(start, end, home="Home", work="Work") == "private"


def test_default_type_is_configurable():
    home, gym = place(zone="Home"), place(zone="Gym")
    assert (
        classify.classify_trip(home, gym, home="Home", work="Work", default_class="business")
        == "business"
    )
    # "unclassified" (and an unrecognised value) restores the leave-it-blank
    # behaviour rather than writing a class nothing understands.
    for value in ("unclassified", None, "nonsense"):
        assert (
            classify.classify_trip(
                home, gym, home="Home", work="Work", default_class=value
            )
            is None
        )


def test_default_type_never_overrides_a_commute():
    home, work = place(zone="Home"), place(zone="Work")
    assert (
        classify.classify_trip(
            home, work, home="Home", work="Work", default_class="business"
        )
        == "commute"
    )


def test_trip_class_setting_maps_the_option_value():
    assert classify.trip_class_setting("private") == "private"
    assert classify.trip_class_setting("business") == "business"
    assert classify.trip_class_setting("unclassified") is None
    assert classify.trip_class_setting(None) is None


def test_classifier_never_invents_business():
    # Without the user choosing it as their default, business is only ever
    # reachable through the manual override.
    result = classify.classify_trip(
        place(zone="Home"), place(zone="Work"), home="Home", work=None
    )
    assert result != "business"


def test_identical_home_and_work_is_not_a_commute():
    # A misconfigured work zone pointing at home would otherwise make every
    # drive home a commute.
    assert (
        classify.classify_trip(
            place(zone="Home"), place(zone="Home"), home="Home", work="Home"
        )
        == "private"
    )


# --- commute chains (a stop on the way) ------------------------------------


def _leg(minutes_in: int, minutes_out: int, start_zone, end_zone, **overrides) -> Trip:
    """One leg of a chain, placed relative to ``START`` by minute offsets."""

    data = {
        "vin": "WBY1",
        "start": START + timedelta(minutes=minutes_in),
        "end": START + timedelta(minutes=minutes_out),
        "distance_km": 8.0,
        "start_place": None if start_zone is None else place(zone=start_zone),
        "end_place": None if end_zone is None else place(zone=end_zone),
        "classification": "private",
        "classification_source": "auto",
    }
    data.update(overrides)
    return Trip(**data)


def _chain(trip, previous, *, gap_min=30, home="Home", work="Work"):
    return classify.commute_chain(
        trip, previous, home=home, work=work, gap_s=gap_min * 60
    )


def test_stop_on_the_way_to_work_chains_into_a_commute():
    # Home -> supermarket (22 min stop) -> Work: two records, one commute.
    leg1 = _leg(0, 12, "Home", None)  # supermarket is in no zone
    leg2 = _leg(34, 49, None, "Work")
    promoted = _chain(leg2, [leg1])
    assert promoted is not None
    assert [leg.id for leg in promoted] == [leg1.id]


def test_a_long_stop_breaks_the_chain():
    leg1 = _leg(0, 12, "Home", None)
    leg2 = _leg(60, 75, None, "Work")  # stood 48 min
    assert _chain(leg2, [leg1]) is None


def test_a_round_trip_from_home_is_not_a_commute():
    # Home -> bakery -> Home: the chain begins and ends at home.
    leg1 = _leg(0, 8, "Home", None)
    leg2 = _leg(20, 28, None, "Home")
    assert _chain(leg2, [leg1]) is None


def test_an_errand_from_work_is_not_a_commute():
    # Home -> Work, then Work -> lunch -> Work with short gaps. The morning
    # commute must not drag the lunch run in with it.
    commute = _leg(0, 25, "Home", "Work", classification="commute")
    out = _leg(50, 58, "Work", None)
    back = _leg(75, 83, None, "Work")
    assert _chain(back, [commute, out]) is None


def test_the_evening_commute_chains_too():
    morning = _leg(0, 25, "Home", "Work", classification="commute")
    leg1 = _leg(500, 515, "Work", None)  # leaves work, stops at the bakery
    leg2 = _leg(530, 545, None, "Home")
    promoted = _chain(leg2, [morning, leg1])
    assert promoted is not None
    assert [leg.id for leg in promoted] == [leg1.id]


def test_several_stops_chain_up_to_the_leg_cap():
    # Four legs in: still one commute with errands (the cap is five, arriving
    # leg included).
    legs = [
        _leg(0, 10, "Home", None),
        _leg(20, 30, None, None),
        _leg(40, 50, None, None),
    ]
    promoted = _chain(_leg(60, 70, None, "Work"), legs)
    assert promoted is not None
    assert len(promoted) == 3

    # Six legs is a day of running around, not a commute: with no chain origin
    # reachable inside the cap, the default type stands.
    legs = [_leg(i * 20, i * 20 + 10, "Home" if i == 0 else None, None) for i in range(5)]
    assert _chain(_leg(100, 110, None, "Work"), legs) is None


def test_a_hand_classified_leg_keeps_its_class_but_still_chains():
    leg1 = _leg(0, 12, "Home", None, classification="business", classification_source="user")
    leg2 = _leg(34, 49, None, "Work")
    promoted = _chain(leg2, [leg1])
    assert promoted == []  # a commute chain, but nothing to rewrite


def test_chaining_off_and_missing_work_zone_do_nothing():
    leg1 = _leg(0, 12, "Home", None)
    leg2 = _leg(34, 49, None, "Work")
    assert _chain(leg2, [leg1], gap_min=0) is None
    assert _chain(leg2, [leg1], work=None) is None


def test_a_chain_needs_to_arrive_somewhere_that_matters():
    leg1 = _leg(0, 12, "Home", None)
    leg2 = _leg(34, 49, None, "Gym")
    assert _chain(leg2, [leg1]) is None


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


def test_silence_implies_stop_reads_the_gap_as_a_whole():
    stop = lambda gap_s, step_km: trip_builder.silence_implies_stop(  # noqa: E731
        gap_s, step_km, min_gap_s=300
    )
    # 12 minutes of silence, 80 m covered: the car stood in a garage.
    assert stop(720, 0.08) is True
    # 12 minutes of silence, 9 km covered: it was driving the whole time.
    assert stop(720, 9.0) is False
    # A gap no longer than the debounce never had a close pending to settle.
    assert stop(200, 0.02) is False
    assert stop(None, 0.02) is False
    assert stop(720, None) is False


def test_noise_trip_detection():
    assert trip_builder.is_noise_trip(_trip(distance_km=0.2)) is True
    assert trip_builder.is_noise_trip(_trip(distance_km=5.0)) is False
    # Unknown distance falls back to a duration floor.
    brief = _trip(distance_km=None, end=START + timedelta(seconds=30))
    assert trip_builder.is_noise_trip(brief) is True


# --- GPS-derived distance --------------------------------------------------


def test_haversine_km_matches_known_distance():
    # Two points ~1.11 km apart (0.01 deg of latitude at the equator meridian).
    d = trip_builder.haversine_km(48.0, 11.0, 48.01, 11.0)
    assert round(d, 2) == 1.11
    # Identical points are zero, never a rounding artefact.
    assert trip_builder.haversine_km(48.0, 11.0, 48.0, 11.0) == 0.0


def test_gps_tracker_reports_steps_between_fixes():
    tracker = trip_builder.GpsTracker()
    # The first fix has no predecessor, so its step is zero.
    assert tracker.step(48.0, 11.0) == 0.0
    step = tracker.step(48.01, 11.0)
    assert round(step, 2) == 1.11
    # A jitter-sized hop is below the movement threshold; a real one is above.
    assert trip_builder.is_gps_movement(0.02) is False
    assert trip_builder.is_gps_movement(step) is True


def test_builder_uses_gps_track_when_no_odometer():
    # No odometer and no BMW distance: the summed GPS track is all there is.
    builder = TripBuilder("WBY1", START, soc_start=80.0)
    builder.add_gps_km(0.7)
    builder.add_gps_km(0.5)
    builder.add_gps_km(-1.0)  # a bad hop is ignored, never subtracted
    trip = builder.close(START + timedelta(minutes=20), soc_end=76.0)
    assert trip.distance_km == 1.2


def test_odometer_and_bmw_distance_outrank_the_gps_track():
    builder = TripBuilder("WBY1", START, mileage_start=1000.0)
    builder.add_gps_km(5.0)
    # Odometer delta wins over the GPS track when it is available.
    trip = builder.close(START + timedelta(minutes=20), mileage_end=1023.4)
    assert trip.distance_km == 23.4


# --- route track (opt-in) --------------------------------------------------


def test_track_is_empty_unless_opted_in():
    # Default builder never stores a coordinate, even when fed fixes.
    builder = TripBuilder("WBY1", START)
    builder.add_track_point(48.1, 11.5)
    builder.add_track_point(48.2, 11.6)
    trip = builder.close(START + timedelta(minutes=20))
    assert trip.track == []


def test_track_records_fixes_when_opted_in():
    builder = TripBuilder("WBY1", START, record_track=True)
    builder.add_track_point(48.123456, 11.5)  # rounded to 5 dp
    builder.add_track_point(48.2, 11.6)
    builder.add_track_point(48.2, 11.6)  # a repeated fix is skipped
    trip = builder.close(START + timedelta(minutes=20))
    # A fix with no time stays the legacy two-element point.
    assert trip.track == [[48.12346, 11.5], [48.2, 11.6]]


def test_track_stamps_seconds_since_start_when_time_given():
    builder = TripBuilder("WBY1", START, record_track=True)
    builder.add_track_point(48.1, 11.5, START)  # t=0 at the opening fix
    builder.add_track_point(48.2, 11.6, START + timedelta(seconds=7))
    builder.add_track_point(48.3, 11.7, START + timedelta(minutes=2, seconds=30))
    trip = builder.close(START + timedelta(minutes=20))
    assert trip.track == [[48.1, 11.5, 0], [48.2, 11.6, 7], [48.3, 11.7, 150]]


def test_track_dedupes_on_coordinate_not_time():
    # A parked car streaming its unchanged position with advancing timestamps
    # must not fill the buffer: the coordinate dedupe ignores the differing t,
    # and the gap to the next moved point encodes the dwell.
    builder = TripBuilder("WBY1", START, record_track=True)
    builder.add_track_point(48.1, 11.5, START)
    builder.add_track_point(48.1, 11.5, START + timedelta(minutes=5))
    builder.add_track_point(48.1, 11.5, START + timedelta(minutes=12))
    builder.add_track_point(48.2, 11.6, START + timedelta(minutes=13))
    trip = builder.close(START + timedelta(minutes=20))
    assert trip.track == [[48.1, 11.5, 0], [48.2, 11.6, 780]]


def test_track_clamps_time_before_start_to_zero():
    builder = TripBuilder("WBY1", START, record_track=True)
    builder.add_track_point(48.1, 11.5, START - timedelta(seconds=3))
    trip = builder.close(START + timedelta(minutes=20))
    assert trip.track == [[48.1, 11.5, 0]]


def test_track_round_trips_through_json():
    # Mixed shapes -- a timestamped point and a legacy two-element one -- both
    # survive the round trip, so a store spanning the timestamp change reads back.
    original = _trip(track=[[48.1, 11.5, 0], [48.2, 11.6, 42], [48.3, 11.7]])
    restored = Trip.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored is not None
    assert restored.track == [[48.1, 11.5, 0], [48.2, 11.6, 42], [48.3, 11.7]]


def test_track_decimates_past_the_point_budget():
    builder = TripBuilder("WBY1", START, record_track=True)
    cap = trip_builder.MAX_TRACK_POINTS
    # Feed well past the cap with monotonically distinct points.
    for i in range(cap * 3):
        builder.add_track_point(48.0 + i * 1e-4, 11.0 + i * 1e-4)
    trip = builder.close(START + timedelta(hours=3))
    # Bounded, and still a real route (first fix preserved, order kept).
    assert len(trip.track) <= cap
    assert trip.track[0] == [48.0, 11.0]
    assert trip.track == sorted(trip.track)


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
