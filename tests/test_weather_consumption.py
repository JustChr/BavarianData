"""Consumption per outside-temperature band, and the cold penalty."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

trips = load_module("history.trips")
wc = load_module("history.weather_consumption")

START = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)


def _trip(
    i: int, temp: float | None, km: float, kwh: float, *, soc_drop: float = 20.0, hybrid=False
):
    trip = trips.Trip(
        vin="VIN",
        start=START + timedelta(days=i),
        end=START + timedelta(days=i, hours=1),
        distance_km=km,
        soc_start=80.0,
        soc_end=80.0 - soc_drop,
        energy_kwh=kwh,
        hybrid=hybrid,
    )
    if temp is not None:
        trip.weather = {"source": "route", "temp_c": temp, "samples": []}
    return trip


def _band(result, low):
    return next(band for band in result["bands"] if band["from_c"] == low)


def test_trips_land_in_five_degree_bands_weighted_by_distance() -> None:
    result = wc.consumption_by_temperature(
        [_trip(0, 2.0, 100, 22.0), _trip(1, 4.9, 20, 6.0), _trip(2, 17.0, 50, 8.0)]
    )
    band = _band(result, 0)
    assert (band["to_c"], band["trips"], band["km"]) == (5, 2, 120.0)
    assert band["kwh_per_100km"] == 23.3  # 28 kWh over 120 km, not the mean of 22 and 30
    assert _band(result, 15)["kwh_per_100km"] == 16.0
    assert result["status"] == "ok" and result["trips"] == 3


def test_the_open_bands_take_everything_beyond_the_edges() -> None:
    result = wc.consumption_by_temperature([_trip(0, -12.0, 40, 12.0), _trip(1, 31.0, 40, 7.0)])
    assert _band(result, None) == {
        "from_c": None,
        "to_c": 0,
        "trips": 1,
        "km": 40.0,
        "kwh_per_100km": 30.0,
    }
    assert _band(result, 25)["to_c"] is None
    assert _band(result, 25)["trips"] == 1


def test_a_band_with_too_few_km_shows_no_figure() -> None:
    result = wc.consumption_by_temperature([_trip(0, 7.0, 12, 3.0)])
    assert _band(result, 5)["km"] == 12.0
    assert _band(result, 5)["kwh_per_100km"] is None


def test_the_cold_penalty_compares_below_five_with_the_mild_band() -> None:
    cold = [_trip(i, 1.0, 60, 13.8) for i in range(2)]  # 23 kWh/100 km
    mild = [_trip(10 + i, 18.0, 60, 9.6) for i in range(2)]  # 16 kWh/100 km
    result = wc.consumption_by_temperature(cold + mild)
    assert (result["cold_kwh_per_100km"], result["mild_kwh_per_100km"]) == (23.0, 16.0)
    assert result["cold_penalty_percent"] == 44


def test_no_penalty_until_both_sides_have_enough_km() -> None:
    result = wc.consumption_by_temperature([_trip(0, 1.0, 150, 30.0), _trip(1, 18.0, 60, 9.6)])
    assert result["cold_kwh_per_100km"] == 20.0
    assert result["mild_kwh_per_100km"] is None
    assert result["cold_penalty_percent"] is None


def test_trips_without_weather_or_a_consumption_figure_are_left_out() -> None:
    result = wc.consumption_by_temperature(
        [
            _trip(0, None, 100, 20.0),  # not looked up
            _trip(1, 3.0, 100, 20.0, soc_drop=1.0),  # too small a drop to divide by
            _trip(2, 3.0, 100, 20.0, hybrid=True),  # a hybrid's ratio is withheld
            _trip(3, 3.0, 100, 20.0),
        ]
    )
    assert result["trips"] == 1


def test_an_answer_without_a_temperature_counts_as_no_weather() -> None:
    trip = _trip(0, None, 100, 20.0)
    trip.weather = {"source": "home", "temp_c": None, "samples": []}
    result = wc.consumption_by_temperature([trip])
    assert result["status"] == "no_weather"
    assert result["trips"] == 0


def test_a_plug_in_hybrid_gets_no_figures() -> None:
    result = wc.consumption_by_temperature([_trip(0, 3.0, 100, 20.0)], hybrid=True)
    assert result["status"] == "plug_in_hybrid"
    assert result["bands"] == []
