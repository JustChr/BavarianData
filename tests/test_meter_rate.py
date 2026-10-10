"""The wallbox-meter model, learned from the ledger, and the tracker following it.

``history/meter_rate.py`` fits ``points = a x (kWh - overhead x hours)`` to the
home charges the meter measured; ``SocTracking.follow_meter`` turns the
meter's advance into the estimate with it. Both are HA-free and pinned here;
how the coordinator gates them is in ``test_scenarios_meter.py``.

The synthetic car is the maintainer's i5 as measured on 2026-10-10: 1.30
points per kWh after a 0.30 kW overhead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import load_module

meter_rate_mod = load_module("history.meter_rate")
models = load_module("history.models")
soc = load_module("soc_tracking")

VIN = "WBATEST0000000001"
T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
A = 1.30
OVERHEAD = 0.30


def _charge(day: int, kw: float, hours: float, *, soc_start: float = 30.0, **kw_args):
    """A home charge at ``kw`` for ``hours``, with the SoC the model predicts."""

    kwh = kw * hours
    gained = A * (kwh - OVERHEAD * hours)
    start = T0 + timedelta(days=day)
    fields = {
        "vin": VIN,
        "start": start,
        "end": start + timedelta(hours=hours),
        "soc_start": soc_start,
        "soc_end": soc_start + gained,
        "grid_kwh": kwh,
        "grid_source": "meter",
    }
    fields.update(kw_args)
    return models.ChargingSession(**fields)


def _mixed(count: int = 4):
    """Solar-surplus charges and an 11 kW one, like a PV household's week."""

    plan = [(2.0, 6.0), (11.0, 4.0), (3.5, 5.0), (2.5, 8.0), (11.0, 2.0), (1.8, 6.0)]
    return [_charge(day, kw, hours) for day, (kw, hours) in enumerate(plan[:count])]


def _rate(sessions, home="Home"):
    return meter_rate_mod.meter_rate(sessions, home)


# --- what the ledger teaches ------------------------------------------------


def test_mixed_charges_separate_efficiency_from_overhead():
    learned = _rate(_mixed())
    assert learned.fitted
    assert learned.percent_per_kwh == pytest.approx(A, abs=0.005)
    assert learned.overhead_kw == pytest.approx(OVERHEAD, abs=0.01)


def test_the_model_predicts_a_power_it_never_saw():
    """Fitted on solar charges only, it still gets an 11 kW charge right."""

    solar = [_charge(d, kw, h) for d, (kw, h) in enumerate([(2.0, 6.0), (3.5, 5.0), (1.8, 6.0)])]
    learned = _rate(solar)
    fast = _charge(10, 11.0, 5.0)
    assert learned.points(fast.grid_kwh, 5.0) == pytest.approx(fast.soc_delta, abs=0.1)


def test_charges_all_at_one_power_fall_back_to_the_ratio():
    """kWh and hours are then proportional: no fit can split them."""

    learned = _rate([_charge(day, 3.5, 4.0) for day in range(3)])
    assert not learned.fitted
    assert learned.overhead_kw == 0.0
    expected = A * (3.5 * 4 - OVERHEAD * 4) / (3.5 * 4)
    assert learned.percent_per_kwh == pytest.approx(expected, abs=0.005)


def test_two_charges_are_not_enough():
    assert _rate(_mixed(2)) is None


def test_an_outlier_is_left_out_of_the_second_fit():
    odd = _charge(9, 2.2, 4.3, soc_start=82.0)
    odd.soc_end = 97.0  # BMW's SoC near the top: 15 points for 9.5 kWh
    learned = _rate(_mixed(5) + [odd])
    assert learned.samples == 5
    assert learned.percent_per_kwh == pytest.approx(A, abs=0.005)


def test_only_the_newest_ten_count():
    old = [_charge(d, 3.5, 4.0, grid_kwh=20.0) for d in range(10)]  # a lossier wallbox
    new = [_charge(20 + d, kw, h) for d, (kw, h) in enumerate([(2.0, 6.0), (11.0, 4.0)] * 5)]
    learned = _rate(old + new)
    assert learned.samples == 10
    assert learned.percent_per_kwh == pytest.approx(A, abs=0.005)


@pytest.mark.parametrize(
    "spoiler",
    [
        {"late_start": True},  # the SoC span missed the start
        {"grid_source": "bmw"},  # BMW's history measured it, not this meter
        {"grid_source": None, "enriched": True},  # enriched before the source was kept
        {"end": None},  # still open
        {"grid_kwh": None},  # no meter
        {"soc_end": 33.0},  # a 3-point top-up: whole-percent noise
        {"location": {"zone": "Work"}},  # not the wallbox at home
        {"location": {"zone": None, "address": "Somewhere 1"}},  # public charger
        {"grid_kwh": 1.0},  # 20 points for 1 kWh: not this meter
    ],
)
def test_a_charge_that_cannot_teach_is_left_out(spoiler):
    sessions = _mixed(2) + [_charge(5, 2.5, 8.0, **spoiler)]
    assert _rate(sessions) is None


def test_an_enriched_charge_that_kept_the_meters_figure_still_teaches():
    """Merging BMW's history no longer costs a home charge its place."""

    sessions = _mixed(2) + [_charge(5, 2.5, 8.0, enriched=True)]
    assert _rate(sessions) is not None


def test_an_interrupted_charge_still_teaches():
    """A restart leaves a hole in the battery side; the meter's total is whole."""

    sessions = _mixed(2) + [_charge(5, 2.5, 8.0, interrupted=True)]
    assert _rate(sessions) is not None


def test_a_charge_with_no_known_position_still_teaches():
    """The ledger's own rule: only a known elsewhere vetoes the meter."""

    assert _rate([_charge(d, 3.5, 4.0, location=None) for d in range(3)]) is not None


def test_the_home_zone_matches_by_name():
    sessions = [_charge(d, 3.5, 4.0, location={"zone": "Zuhause"}) for d in range(3)]
    assert _rate(sessions, home="Zuhause") is not None
    assert _rate(sessions, home="Home") is None


# --- the model's arithmetic -------------------------------------------------


def _model(a=A, overhead=OVERHEAD):
    return meter_rate_mod.MeterRate(percent_per_kwh=a, overhead_kw=overhead, samples=3, fitted=True)


def test_points_take_the_overhead_off_for_the_time_it_ran():
    assert _model().points(2.0, 1.0) == pytest.approx(A * (2.0 - OVERHEAD))


def test_a_pause_is_not_charged_overhead():
    """Two hours idle then 0.5 kWh: only the time 0.5 kWh needs at 1 kW counts."""

    assert _model().points(0.5, 2.5) == pytest.approx(A * (0.5 - OVERHEAD * 0.5))


def test_points_are_never_negative():
    assert _model(overhead=1.0).points(0.1, 1.0) >= 0.0
    assert _model().points(0.0, 1.0) == 0.0


# --- the tracker following the meter ----------------------------------------


def _tracking(percent: float = 40.0):
    tracking = soc.SocTracking()
    tracking.update_max_energy(75.0)
    tracking.update_actual_soc(percent, T0)
    tracking.update_status("CHARGINGACTIVE")
    tracking.update_power(3480.0, T0)
    return tracking


RATIO = meter_rate_mod.MeterRate(percent_per_kwh=1.25, overhead_kw=0.0, samples=3, fitted=False)


def test_the_first_reading_is_only_a_baseline():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    assert tracking.estimated_percent == 40.0
    assert tracking.meter_kwh == 1000.0


def test_the_estimate_moves_by_what_the_meter_counted():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.follow_meter(1008.0, RATIO, T0 + timedelta(hours=1))
    assert tracking.estimated_percent == pytest.approx(50.0)


def test_the_overhead_is_taken_over_the_time_between_readings():
    tracking = _tracking()
    tracking.follow_meter(1000.0, _model(), T0)
    tracking.follow_meter(1002.0, _model(), T0 + timedelta(hours=1))
    assert tracking.estimated_percent == pytest.approx(40.0 + A * (2.0 - OVERHEAD))


def test_while_the_meter_drives_the_stream_power_adds_nothing():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.update_power(11000.0, T0 + timedelta(minutes=30))
    assert tracking.estimate(T0 + timedelta(hours=2)) == 40.0


def test_letting_go_hands_the_stream_the_time_from_now_only():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.follow_meter(1008.0, RATIO, T0 + timedelta(hours=1))
    tracking.follow_meter(None, None, T0 + timedelta(hours=1))
    assert tracking.meter_model is None
    later = tracking.estimate(T0 + timedelta(hours=2))
    assert later == pytest.approx(50.0 + 3.48 / 75 * 100)


def test_taking_over_from_the_stream_keeps_what_it_had_added():
    tracking = _tracking()
    tracking.estimate(T0)
    tracking.follow_meter(1000.0, RATIO, T0 + timedelta(hours=1))
    assert tracking.estimated_percent == pytest.approx(40.0 + 3.48 / 75 * 100)


def test_a_backwards_meter_is_a_new_baseline():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.follow_meter(2.0, RATIO, T0 + timedelta(minutes=10))
    assert tracking.estimated_percent == 40.0
    tracking.follow_meter(4.0, RATIO, T0 + timedelta(minutes=30))
    assert tracking.estimated_percent == pytest.approx(42.5)


def test_a_step_faster_than_any_wallbox_is_refused():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.follow_meter(1010.0, RATIO, T0 + timedelta(minutes=10))  # 60 kW
    assert tracking.estimated_percent == 40.0


def test_the_meter_stops_at_target_and_full():
    tracking = _tracking(95.0)
    tracking.update_target_soc(100.0)
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.follow_meter(1010.0, RATIO, T0 + timedelta(hours=1))
    assert tracking.estimated_percent == 100.0

    capped = _tracking(45.0)
    capped.update_target_soc(50.0)
    capped.follow_meter(1000.0, RATIO, T0)
    capped.follow_meter(1010.0, RATIO, T0 + timedelta(hours=1))
    assert capped.estimated_percent == 50.0


def test_a_real_reading_drops_the_baseline():
    tracking = _tracking()
    tracking.follow_meter(1000.0, RATIO, T0)
    tracking.update_actual_soc(47.0, T0 + timedelta(hours=1))
    assert tracking.meter_kwh is None
    tracking.follow_meter(1008.0, RATIO, T0 + timedelta(hours=1))  # new baseline
    assert tracking.estimated_percent == 47.0


def test_a_restored_reading_carries_the_downtime():
    tracking = soc.SocTracking()
    tracking.update_max_energy(75.0)
    tracking.update_actual_soc(40.0, T0, restored=True)
    assert tracking.adopt_estimate(45.0, T0 + timedelta(hours=1), meter_kwh=1004.0)
    tracking.restore_status("chargingactive", T0)
    tracking.follow_meter(1008.0, RATIO, T0 + timedelta(hours=1, minutes=30))
    assert tracking.estimated_percent == pytest.approx(50.0)
