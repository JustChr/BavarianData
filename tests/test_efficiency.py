"""Unit tests for the efficiency / real-range maths.

A range figure is something people plan a journey on, so the interesting cases
here are all about *refusing*: refusing to mix the two sides of the charger,
refusing to scale BMW's estimate up from a nearly-empty battery, refusing to
report a month whose charging couldn't bracket enough distance to mean anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

models = load_module("history.models")
summary = load_module("history.summary")
efficiency = load_module("history.efficiency")

ChargingSession = models.ChargingSession

# The live car this was calibrated against: a 78 kWh i5 that measured
# 17.5 kWh/100 km battery-side over a 451 km window.
CAP = 78.0
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _charge(days_ago: float, *, odo: float, soc: float, kwh=None, grid=None):
    start = NOW - timedelta(days=days_ago)
    return ChargingSession(
        vin="WBY1",
        start=start,
        end=start + timedelta(hours=2),
        mileage_km=odo,
        soc_end=soc,
        energy_kwh=kwh,
        grid_kwh=grid,
    )


# --- picking a window ------------------------------------------------------


def test_consumption_uses_the_shortest_window_that_can_answer():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0),
    ]
    result = efficiency.consumption(charges, battery_capacity_kwh=CAP, now=NOW)
    assert result["window_days"] == 30
    assert result["kwh_per_100km"] == 20.0
    assert result["source"] == "battery"


def test_consumption_reaches_further_back_when_the_recent_window_is_too_thin():
    """A month with one charge in it can't bracket anything; the year can."""

    charges = [
        _charge(300, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(3, odo=12000.0, soc=80.0, kwh=400.0),
    ]
    result = efficiency.consumption(charges, battery_capacity_kwh=CAP, now=NOW)
    assert result["window_days"] == 365
    assert result["kwh_per_100km"] == 20.0


def test_consumption_falls_back_to_the_whole_ledger_and_says_so():
    charges = [
        _charge(900, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(800, odo=12000.0, soc=80.0, kwh=400.0),
    ]
    result = efficiency.consumption(charges, battery_capacity_kwh=CAP, now=NOW)
    # ``None`` is not "no window" -- it is "it took everything on file".
    assert result["window_days"] is None
    assert result["kwh_per_100km"] == 20.0


def test_consumption_gives_up_rather_than_inventing_a_window():
    assert efficiency.consumption([], battery_capacity_kwh=CAP, now=NOW) is None


# --- the two sides of the charger ------------------------------------------


def test_battery_side_refuses_a_window_containing_an_imported_charge():
    """An import carries only a grid figure, so the battery sum would have a hole.

    Understating the energy would understate consumption and *overstate* the
    range -- the one direction that strands someone.
    """

    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(10, odo=10100.0, soc=80.0, grid=25.0),  # BMW import: no energy_kwh
        _charge(2, odo=10200.0, soc=80.0, kwh=20.0),
    ]
    assert (
        summary.energy_balance(charges, battery_capacity_kwh=CAP, side=summary.SIDE_BATTERY) is None
    )
    # Auto still answers, counting the import -- that is the ledger's usual rule.
    assert summary.energy_balance(charges, battery_capacity_kwh=CAP) is not None


def test_an_old_import_does_not_poison_the_recent_figure():
    """The shortest-window-first chain is what rescues this case."""

    charges = [
        _charge(200, odo=9000.0, soc=80.0, kwh=10.0),
        _charge(180, odo=9500.0, soc=80.0, grid=100.0),
        _charge(25, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0),
    ]
    result = efficiency.consumption(charges, battery_capacity_kwh=CAP, now=NOW)
    assert result["window_days"] == 30
    assert result["kwh_per_100km"] == 20.0


def test_grid_side_refuses_a_window_with_an_unmetered_charge():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0, grid=11.0),
        _charge(10, odo=10100.0, soc=80.0, kwh=20.0),  # no meter behind it
        _charge(2, odo=10200.0, soc=80.0, kwh=20.0, grid=22.0),
    ]
    assert summary.energy_balance(charges, battery_capacity_kwh=CAP, side=summary.SIDE_GRID) is None


def test_the_charging_loss_comes_from_one_window_or_not_at_all():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0, grid=11.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0, grid=45.0),
    ]
    profile = efficiency.efficiency_profile(charges, battery_capacity_kwh=CAP, now=NOW)
    assert profile["consumption"]["kwh_per_100km"] == 20.0
    assert profile["grid_consumption"]["kwh_per_100km"] == 22.5
    assert profile["grid_consumption"]["window_days"] == 30
    assert profile["measured_loss_percent"] == 11.1


def test_no_loss_is_reported_when_only_one_side_can_be_read():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0),
    ]
    profile = efficiency.efficiency_profile(charges, battery_capacity_kwh=CAP, now=NOW)
    assert profile["grid_consumption"] is None
    assert profile["measured_loss_percent"] is None


def test_a_grid_figure_below_the_battery_one_is_not_a_negative_loss():
    """Charging cannot create energy; such a pair means the sums disagree."""

    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0, grid=9.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0, grid=36.0),
    ]
    profile = efficiency.efficiency_profile(charges, battery_capacity_kwh=CAP, now=NOW)
    assert profile["measured_loss_percent"] is None


# --- range -----------------------------------------------------------------


def test_real_range_is_capacity_over_consumption():
    result = efficiency.real_range(kwh_per_100km=17.5, capacity_kwh=78.0)
    assert result["full_km"] == 445.7
    # Without a state of charge there is no "from here" figure, only a full one.
    assert result["now_km"] is None


def test_real_range_scales_to_the_current_charge():
    result = efficiency.real_range(kwh_per_100km=20.0, capacity_kwh=80.0, soc_percent=50.0)
    assert result["full_km"] == 400.0
    assert result["now_km"] == 200.0


def test_real_range_needs_both_halves_of_the_arithmetic():
    assert efficiency.real_range(kwh_per_100km=20.0, capacity_kwh=None) is None
    assert efficiency.real_range(kwh_per_100km=None, capacity_kwh=80.0) is None
    assert efficiency.real_range(kwh_per_100km=0.0, capacity_kwh=80.0) is None


def test_real_range_compares_itself_with_the_cars_own_estimate():
    result = efficiency.real_range(
        kwh_per_100km=20.0,
        capacity_kwh=80.0,
        soc_percent=50.0,
        bmw_range_km=180.0,
    )
    assert result["bmw_km"] == 180.0
    assert result["bmw_full_km"] == 360.0
    # 200 km against BMW's 180: the car goes 11.1 % further than it promises.
    assert result["vs_bmw_percent"] == 11.1


def test_bmw_range_is_not_scaled_up_from_a_nearly_empty_battery():
    """At 5 % SoC, dividing by 0.05 multiplies BMW's own error by twenty."""

    result = efficiency.real_range(
        kwh_per_100km=20.0,
        capacity_kwh=80.0,
        soc_percent=5.0,
        bmw_range_km=15.0,
    )
    assert result["bmw_full_km"] is None
    # The direct comparison at the current charge is still fair game.
    assert result["vs_bmw_percent"] is not None


# --- the profile -----------------------------------------------------------


def _month_pair(year: int, month: int, *, odo: float, kwh: float):
    """Two charges inside one calendar month, bracketing 200 km."""

    first = datetime(year, month, 2, 8, 0, tzinfo=timezone.utc)
    second = datetime(year, month, 20, 8, 0, tzinfo=timezone.utc)
    return [
        ChargingSession(
            vin="WBY1",
            start=first,
            end=first + timedelta(hours=2),
            mileage_km=odo,
            soc_end=80.0,
            energy_kwh=5.0,
        ),
        ChargingSession(
            vin="WBY1",
            start=second,
            end=second + timedelta(hours=2),
            mileage_km=odo + 200.0,
            soc_end=80.0,
            energy_kwh=kwh,
        ),
    ]


def test_monthly_trend_runs_oldest_first_and_skips_what_it_cannot_measure():
    charges = (
        _month_pair(2026, 7, odo=10000.0, kwh=40.0)
        + _month_pair(2026, 9, odo=10400.0, kwh=60.0)
        # August: one charge only, so the month cannot bracket a distance.
        + [
            ChargingSession(
                vin="WBY1",
                start=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
                mileage_km=10300.0,
                soc_end=80.0,
                energy_kwh=30.0,
            )
        ]
    )
    trend = efficiency.monthly_consumption(charges, battery_capacity_kwh=CAP, now=NOW, months=6)
    assert [entry["month"] for entry in trend] == ["2026-07", "2026-09"]
    assert trend[0]["kwh_per_100km"] == 20.0
    assert trend[1]["kwh_per_100km"] == 30.0
    assert trend[1]["distance_km"] == 200.0


def test_profile_prefers_a_measured_capacity_over_the_nameplate():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0),
    ]
    bmw = efficiency.efficiency_profile(charges, battery_capacity_kwh=78.0, now=NOW)
    assert bmw["capacity_source"] == "bmw"
    assert bmw["range"]["full_km"] == 390.0

    measured = efficiency.efficiency_profile(
        charges,
        battery_capacity_kwh=78.0,
        usable_capacity_kwh=72.0,
        now=NOW,
    )
    assert measured["capacity_source"] == "measured"
    assert measured["range"]["full_km"] == 360.0


def test_profile_says_which_input_it_is_missing():
    charges = [
        _charge(20, odo=10000.0, soc=80.0, kwh=10.0),
        _charge(2, odo=10200.0, soc=80.0, kwh=40.0),
    ]
    assert (
        efficiency.efficiency_profile(charges, battery_capacity_kwh=CAP, now=NOW)["status"]
        == efficiency.STATUS_OK
    )
    # No capacity: the balance itself can't be computed either, so the honest
    # answer is still that there is nothing to divide.
    thin = efficiency.efficiency_profile([], battery_capacity_kwh=CAP, now=NOW)
    assert thin["status"] == efficiency.STATUS_NO_CONSUMPTION
    assert thin["range"] is None
    assert thin["trend"] == []
