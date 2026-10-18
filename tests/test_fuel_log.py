"""The fuel log of a petrol or diesel car, and BMW's lifetime figures.

A combustion car has no charging ledger, so its Fuel view is built from the
tank reading alone (``history/fuel.py``): a refuel is a jump of several litres
while the car stands still, and what the tank lost between two refuels, over
the odometer's kilometres, is the consumption. Where BMW sends its OBFCM
counters (``history/obfcm.py``) the view adds the car's lifetime figure; on a
plug-in hybrid those counters are the one honest consumption there is.

The shapes come from #130 (an X5 40d and a 118i: whole litres every few
minutes while driving, ±1 L jitter, a stale lower odometer mid-trip) and the
F87 M2 of #8 (litres in ``l``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .conftest import load_module
from .harness import CoordinatorHarness

fuel = load_module("history.fuel")
obfcm = load_module("history.obfcm")

START = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _drive(log, minutes, litres, km, percent=None):
    return fuel.note_reading(
        log, START + timedelta(minutes=minutes), litres=litres, percent=percent, km=km
    )


def test_a_refuel_is_a_jump_while_parked():
    log = {}
    _drive(log, 0, 50, 10_000)
    _drive(log, 60, 15, 10_500)  # 35 L over 500 km
    assert (
        fuel.note_reading(log, START + timedelta(minutes=120), litres=62, km=10_500)
        == fuel.CHANGED_REFUEL
    )
    (refuel,) = log["refuels"]
    assert (refuel["before_l"], refuel["after_l"], refuel["km"]) == (15, 62, 10_500)


def test_jitter_and_a_drive_are_not_refuels():
    log = {}
    _drive(log, 0, 40, 10_000)
    _drive(log, 5, 41, 10_003)  # the float wandering by a litre
    _drive(log, 10, 40, 10_006)
    # A 6 L rise across a 300 km gap is a reading missed around a drive.
    _drive(log, 400, 46, 10_306)
    assert log.get("refuels") == []


def test_the_level_settles_after_the_pump():
    log = {}
    _drive(log, 0, 12, 10_000)
    _drive(log, 30, 40, 10_000)  # mid-fill, or the float still rising
    _drive(log, 40, 58, 10_001)
    _drive(log, 45, 57, 10_002)  # jitter on the way out
    (refuel,) = log["refuels"]
    assert refuel["after_l"] == 58


def test_a_replay_and_a_stale_odometer_change_nothing():
    log = {}
    _drive(log, 60, 30, 10_000)
    # A REST catch-up hands back an older reading.
    assert _drive(log, 30, 55, 9_990) is None
    # #130: a 118i repeated a lower odometer mid-trip.
    _drive(log, 70, 29, 9_000)
    assert log["last"]["km"] == 10_000


def test_consumption_between_refuels():
    log = {}
    _drive(log, 0, 10, 10_000)
    _drive(log, 10, 60, 10_000)  # refuel 1: brim
    _drive(log, 600, 25, 10_500)
    _drive(log, 610, 61, 10_500)  # refuel 2: 35 L over 500 km
    _drive(log, 1200, 31, 10_900)
    _drive(log, 1210, 60, 10_900)  # refuel 3: 30 L over 400 km
    windows = fuel.windows(log)
    assert [w["l_per_100km"] for w in windows] == [7.0, 7.5]
    report = fuel.report(log, START + timedelta(days=2))
    # Weighted by distance: 65 L over 900 km.
    assert report["average"] == {"l_per_100km": 7.2, "km": 900.0, "litres": 65.0, "windows": 2}
    newest = report["refuels"][0]
    assert (newest["added_l"], newest["l_per_100km"], newest["window_km"]) == (29.0, 7.5, 400.0)
    # The first refuel closed no window it could measure.
    assert report["refuels"][-1]["l_per_100km"] is None


def test_a_driven_off_gap_is_left_out_of_both_windows():
    log = {}
    _drive(log, 0, 60, 10_000)
    _drive(log, 10, 61, 10_000)
    _drive(log, 500, 25, 10_500)
    # Not reported again until 12 km after the pump: the old window ends at
    # the reading before (10 500 km), the next begins at this one.
    _drive(log, 560, 60, 10_512)
    refuel = log["refuels"][-1]
    assert (refuel["km_before"], refuel["km"]) == (10_500, 10_512)


def test_short_or_implausible_windows_are_dropped():
    log = {}
    _drive(log, 0, 20, 10_000)
    _drive(log, 10, 60, 10_000)
    _drive(log, 100, 52, 10_100)
    _drive(log, 110, 61, 10_100)  # only 100 km
    _drive(log, 500, 10, 10_400)
    _drive(log, 510, 60, 10_400)  # 51 L over 300 km: 17 l/100 km, kept
    _drive(log, 900, 59, 10_700)
    _drive(log, 905, 66, 10_700)  # 1 L over 300 km: a missed refuel
    assert [w["l_per_100km"] for w in fuel.windows(log)] == [17.0]


def test_since_the_last_refuel():
    log = {}
    _drive(log, 0, 10, 10_000)
    _drive(log, 10, 60, 10_000)
    _drive(log, 100, 50, 10_120)
    since = fuel.report(log, START + timedelta(days=1))["since_refuel"]
    # 120 km is too short for a figure, but the distance and litres stand.
    assert since == {"km": 120.0, "litres": 10.0, "l_per_100km": None}
    _drive(log, 200, 44, 10_220)
    assert fuel.report(log, START + timedelta(days=1))["since_refuel"]["l_per_100km"] == 7.3


def test_a_percent_only_car_logs_refuels_without_litres():
    # The M240i streams a tank percentage and no litres.
    log = {}
    _drive(log, 0, None, 10_000, percent=18)
    _drive(log, 20, None, 10_000, percent=95)
    report = fuel.report(log, START + timedelta(days=1))
    (refuel,) = report["refuels"]
    assert (refuel["added_percent"], refuel["added_l"]) == (77.0, None)
    assert report["average"] is None


# --- BMW's lifetime counters -------------------------------------------------


def _reader(values):
    return lambda descriptor: values.get(descriptor)


def test_lifetime_fuel_figure():
    figures = obfcm.lifetime(
        _reader({obfcm.FUEL_OVERALL: 1_450.0, obfcm.DISTANCE_OVERALL: 20_000.0})
    )
    assert figures == {"fuel_l_per_100km": 7.2, "distance_km": 20_000.0}


def test_a_hybrid_gets_its_electric_figures():
    figures = obfcm.lifetime(
        _reader(
            {
                obfcm.FUEL_OVERALL: 300.0,
                obfcm.DISTANCE_OVERALL: 20_000.0,
                obfcm.GRID_ENGINE_OFF: 1_800.0,
                obfcm.GRID_DISTANCE_ENGINE_OFF: 9_000.0,
                obfcm.GRID_OVERALL: 2_000.0,
                obfcm.GRID_DISTANCE_OVERALL: 20_000.0,
            }
        ),
        hybrid=True,
    )
    assert figures == {
        "fuel_l_per_100km": 1.5,
        "distance_km": 20_000.0,
        "electric_kwh_per_100km": 20.0,
        "electric_km": 9_000.0,
        "grid_kwh_per_100km": 10.0,
        "electric_share_percent": 45.0,
    }


def test_no_counters_or_too_little_distance_says_nothing():
    assert obfcm.lifetime(_reader({})) is None
    assert (
        obfcm.lifetime(_reader({obfcm.FUEL_OVERALL: 20.0, obfcm.DISTANCE_OVERALL: 300.0})) is None
    )
    # A counter not filled in yet: no litres over thousands of km.
    assert (
        obfcm.lifetime(_reader({obfcm.FUEL_OVERALL: 0.0, obfcm.DISTANCE_OVERALL: 9_000.0})) is None
    )
    # The grid counters are never read on a car that is not a hybrid.
    values = {obfcm.GRID_ENGINE_OFF: 1_800.0, obfcm.GRID_DISTANCE_ENGINE_OFF: 9_000.0}
    assert obfcm.lifetime(_reader(values)) is None


# --- through the coordinator ---------------------------------------------------

FUEL = "vehicle.drivetrain.fuelSystem.remainingFuel"
ENGINE = "vehicle.drivetrain.internalCombustionEngine.engine.ect"


def test_a_diesel_logs_its_refuel_through_the_stream():
    h = CoordinatorHarness(capacity_kwh=None)
    try:
        # A coolant temperature marks the engine; no battery ever reports.
        h.send(0, odometer=10_000, extra={FUEL: 14, ENGINE: 20})
        h.send(30, odometer=10_000, extra={FUEL: 61})
        h.send(600, odometer=10_480, extra={FUEL: 27})
        h.send(620, odometer=10_480, extra={FUEL: 63})
        report = h.coordinator.fuel_report(h.vin)
        assert [r["added_l"] for r in report["refuels"]] == [36.0, 47.0]
        assert report["refuels"][0]["l_per_100km"] == 7.1
        assert report["tank"]["litres"] == 63
        assert report["bmw_lifetime"] is None
    finally:
        h.close()


def test_a_battery_car_streaming_a_tank_logs_nothing():
    # An iX3 streams fuel litres too; a "refuel" there would be noise. Its basic
    # data says BEV (without it, battery plus tank reads as a hybrid, #53).
    h = CoordinatorHarness()
    try:
        h.coordinator.apply_basic_data(h.vin, {"driveTrain": "BEV"})
        h.send(0, soc=50, odometer=10_000, extra={FUEL: 10})
        h.send(30, soc=50, odometer=10_000, extra={FUEL: 40})
        assert h.coordinator.fuel_report(h.vin)["refuels"] == []
    finally:
        h.close()
