"""Unit tests for the history layer's Home Assistant-free maths.

Wrong numbers here would be worse than no numbers -- a charging cost that is
quietly understated is indistinguishable from a correct one -- so the record
shape, the session builder and the cost arithmetic are all covered without
needing an HA install. ``history.store`` is excluded on purpose: it is the one
module that imports Home Assistant.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .conftest import load_module

models = load_module("history.models")
sessions = load_module("history.sessions")
pricing = load_module("history.pricing")
summary = load_module("history.summary")
health = load_module("history.health")

ChargingSession = models.ChargingSession
SessionBuilder = sessions.SessionBuilder

START = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)


def _session(**overrides) -> ChargingSession:
    data = {"vin": "WBY1", "start": START, "end": START + timedelta(hours=2)}
    data.update(overrides)
    return ChargingSession(**data)


# --- records ---------------------------------------------------------------


def test_round_trips_through_json():
    original = _session(
        soc_start=40.0,
        soc_end=80.0,
        energy_kwh=12.5,
        power_curve=[[0, 11.0], [60, 10.5]],
        location={"zone": "home", "lat": None, "lon": None},
        cost={"amount": 3.75, "currency": "EUR", "source": "tariff"},
    )
    # Via a real dumps/loads: the store persists JSON, so anything that isn't
    # JSON-native (a stray datetime) has to fail here rather than at runtime.
    restored = ChargingSession.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored is not None
    assert restored.to_dict() == original.to_dict()
    assert restored.start == original.start


def test_unusable_records_are_dropped_not_half_built():
    assert ChargingSession.from_dict({"start": START.isoformat()}) is None
    assert ChargingSession.from_dict({"vin": "WBY1"}) is None
    assert ChargingSession.from_dict({"vin": "WBY1", "start": "not-a-date"}) is None


def test_naive_stored_timestamp_is_treated_as_utc():
    restored = ChargingSession.from_dict(
        {"vin": "WBY1", "start": "2026-07-01T18:00:00"}
    )
    assert restored is not None
    assert restored.start == START


def test_derived_values():
    session = _session(soc_start=40.0, soc_end=80.0, energy_kwh=12.0)
    assert session.duration_s == 7200
    assert session.soc_delta == 40.0
    assert session.avg_power_kw == 6.0
    assert session.id == "WBY1-2026-07-01T18:00:00+00:00"


def test_derived_values_are_none_when_unknowable():
    open_session = _session(end=None, energy_kwh=None, soc_end=None)
    assert open_session.duration_s is None
    assert open_session.avg_power_kw is None
    assert open_session.soc_delta is None


# --- retention -------------------------------------------------------------


def test_prune_drops_sessions_outside_the_window():
    now = START + timedelta(days=400)
    kept = _session(start=now - timedelta(days=10))
    dropped = _session(start=now - timedelta(days=300))
    result = models.prune_sessions(
        [kept, dropped], now=now, retain_months=6, max_entries=100
    )
    assert [item.start for item in result] == [kept.start]


def test_prune_enforces_the_cap_even_when_keeping_forever():
    now = START + timedelta(days=10)
    many = [_session(start=START + timedelta(hours=i)) for i in range(50)]
    result = models.prune_sessions(
        many, now=now, retain_months=None, max_entries=10
    )
    assert len(result) == 10
    # Newest kept, and ordered newest-first.
    assert result[0].start == max(item.start for item in many)
    assert result == sorted(result, key=lambda item: item.start, reverse=True)


def test_merge_replaces_same_id_rather_than_duplicating():
    first = _session(energy_kwh=10.0)
    enriched = _session(energy_kwh=10.0, grid_kwh=11.2, enriched=True)
    result = models.merge_session([first], enriched)
    assert len(result) == 1
    assert result[0].grid_kwh == 11.2


# --- session builder -------------------------------------------------------


def test_builder_downsamples_but_keeps_the_true_peak():
    builder = SessionBuilder("WBY1", START, soc_start=20.0, target_soc=80.0)
    # One sample every 10 s for an hour, with a one-off spike between the
    # minute marks that downsampling would otherwise discard.
    for step in range(360):
        at = START + timedelta(seconds=10 * step)
        power = 50.0 if step == 7 else 11.0
        builder.sample(at, power)

    session = builder.close(
        START + timedelta(hours=1), soc_end=75.0, energy_kwh=11.0, reason="target"
    )
    assert session.peak_power_kw == 50.0
    assert len(session.power_curve) <= sessions.MAX_CURVE_POINTS
    # A point a minute over an hour, plus the closing point.
    assert 55 <= len(session.power_curve) <= 62


def test_builder_bounds_the_curve_for_a_very_long_session():
    builder = SessionBuilder("WBY1", START)
    for minute in range(60 * 24):  # a full day of charging
        builder.sample(START + timedelta(minutes=minute), 2.3)
    session = builder.close(START + timedelta(hours=24), energy_kwh=55.0)
    assert len(session.power_curve) <= sessions.MAX_CURVE_POINTS


def test_builder_carries_the_last_reading_to_the_end():
    builder = SessionBuilder("WBY1", START)
    builder.sample(START, 11.0)
    builder.sample(START + timedelta(seconds=30), 7.0)  # inside the debounce
    session = builder.close(START + timedelta(seconds=90))
    # The curve must reach the end rather than stopping at the last stored point.
    assert session.power_curve[-1] == [90, 7.0]


def test_builder_ignores_missing_power_and_clamps_skewed_timestamps():
    builder = SessionBuilder("WBY1", START)
    builder.sample(START - timedelta(seconds=30), 11.0)  # BMW clock behind ours
    builder.sample(START + timedelta(minutes=1), None)
    session = builder.close(START + timedelta(minutes=2), energy_kwh=0.4)
    assert session.power_curve[0][0] == 0
    assert all(point[0] >= 0 for point in session.power_curve)


def test_builder_records_soc_and_target():
    builder = SessionBuilder("WBY1", START, soc_start=30.0, target_soc=80.0)
    builder.note_soc(55.0)
    session = builder.close(START + timedelta(hours=1), energy_kwh=9.0)
    assert (session.soc_start, session.soc_end, session.target_soc) == (30.0, 55.0, 80.0)


# --- pricing ---------------------------------------------------------------


def test_cost_accumulates_at_the_price_in_force():
    acc = pricing.CostAccumulator(currency="EUR")
    acc.add(5.0, 0.30)  # cheap window
    acc.add(5.0, 0.10)  # price dropped mid-session
    cost = acc.as_cost()
    assert cost == {"amount": 2.0, "currency": "EUR", "source": "tariff"}


def test_unpriced_energy_marks_the_total_partial():
    acc = pricing.CostAccumulator(currency="EUR")
    acc.add(5.0, 0.30)
    acc.add(5.0, None)  # price entity unavailable
    cost = acc.as_cost()
    assert cost["partial"] is True
    assert cost["unpriced_kwh"] == 5.0
    assert cost["amount"] == 1.5


def test_a_trivial_gap_does_not_flag_the_session():
    acc = pricing.CostAccumulator(currency="EUR")
    acc.add(100.0, 0.30)
    acc.add(1.0, None)
    assert "partial" not in acc.as_cost()


def test_no_priced_energy_yields_no_cost_at_all():
    acc = pricing.CostAccumulator(currency="EUR")
    acc.add(5.0, None)
    assert acc.as_cost() is None
    assert pricing.fixed_cost(5.0, None, "EUR") is None
    assert pricing.fixed_cost(0.0, 0.30, "EUR") is None


def test_accumulated_cost_does_not_drift_over_many_samples():
    acc = pricing.CostAccumulator(currency="EUR")
    for _ in range(10_000):
        acc.add(0.001, 0.37)
    # 10 kWh at 0.37 -- rounding each delta instead of the total would lose this.
    assert acc.as_cost()["amount"] == 3.70


def test_bmw_cost_wins_over_our_tariff_maths():
    bmw = pricing.bmw_cost(
        {"calculatedChargingCost": 14.2, "currency": "EUR", "calculatedSavings": 1.1}
    )
    ours = {"amount": 9.0, "currency": "EUR", "source": "tariff"}
    assert bmw["source"] == "bmw"
    assert bmw["savings"] == 1.1
    assert pricing.resolve_cost(bmw=bmw, accumulated=ours) == bmw
    assert pricing.resolve_cost(bmw=None, accumulated=ours) == ours
    assert pricing.resolve_cost(bmw=None, accumulated=None) is None


def test_incomplete_bmw_payload_is_ignored():
    assert pricing.bmw_cost({}) is None
    assert pricing.bmw_cost({"calculatedChargingCost": 5.0}) is None
    assert pricing.bmw_cost({"currency": "EUR"}) is None


def test_billable_energy_prefers_a_measured_grid_figure():
    assert pricing.billable_energy(battery_kwh=10.0, grid_kwh=11.2) == (11.2, "grid")
    assert pricing.billable_energy(battery_kwh=10.0) == (10.0, "battery")
    assert pricing.billable_energy(battery_kwh=None) == (None, "none")


def test_battery_energy_is_only_grossed_up_when_asked():
    value, source = pricing.billable_energy(battery_kwh=9.0, loss_percent=10.0)
    assert source == "battery_adjusted"
    assert value == 10.0


def test_pricing_config_treats_half_configured_as_unconfigured():
    assert not pricing.PricingConfig().enabled
    assert not pricing.PricingConfig(mode="fixed").enabled
    assert not pricing.PricingConfig(mode="entity").enabled
    assert pricing.PricingConfig(mode="fixed", fixed_price=0.30).enabled
    assert pricing.PricingConfig(mode="entity", price_entity="sensor.x").enabled


def test_pricing_config_parses_options_defensively():
    config = pricing.PricingConfig.from_options(
        {"price_mode": "fixed", "price_fixed": "0.42", "charging_loss_percent": ""}
    )
    assert (config.mode, config.fixed_price, config.loss_percent) == ("fixed", 0.42, 0.0)
    assert config.currency == "EUR"
    # Junk must not raise during setup.
    assert pricing.PricingConfig.from_options({"price_fixed": "abc"}).fixed_price is None


# --- summaries -------------------------------------------------------------


def _costed(start: datetime, amount: float, **overrides) -> ChargingSession:
    data = {
        "vin": "WBY1",
        "start": start,
        "end": start + timedelta(hours=1),
        "energy_kwh": 10.0,
        "cost": {"amount": amount, "currency": "EUR", "source": "tariff"},
    }
    data.update(overrides)
    return ChargingSession(**data)


def test_month_bucketing_uses_local_time_not_utc():
    # 23:30 UTC on 31 July is already 01:30 on 1 August in CEST, so the session
    # belongs to August from the user's point of view.
    late = _costed(datetime(2026, 7, 31, 23, 30, tzinfo=timezone.utc), 5.0)

    def as_cest(value):
        return value.astimezone(timezone(timedelta(hours=2)))

    # Without a localizer it stays in July, matching its UTC timestamp.
    assert summary.sessions_in_month([late], year=2026, month=7) == [late]
    assert (
        summary.sessions_in_month([late], year=2026, month=8, localize=as_cest) == [late]
    )
    assert summary.sessions_in_month([late], year=2026, month=7, localize=as_cest) == []


def test_summary_totals_cost_and_energy():
    result = summary.summarise(
        [
            _costed(START, 3.0),
            _costed(START + timedelta(days=2), 4.5),
        ]
    )
    assert result["sessions"] == 2
    assert result["cost"] == 7.5
    assert result["currency"] == "EUR"
    assert result["energy_kwh"] == 20.0
    assert result["partial"] is False


def test_summary_refuses_to_add_up_mixed_currencies():
    mixed = [
        _costed(START, 3.0),
        _costed(
            START + timedelta(days=1),
            4.0,
            cost={"amount": 4.0, "currency": "GBP", "source": "bmw"},
        ),
    ]
    result = summary.summarise(mixed)
    assert result["cost"] is None
    assert result["currency"] is None
    # Energy is currency-free, so it still totals.
    assert result["energy_kwh"] == 20.0


def test_summary_propagates_a_partial_session():
    result = summary.summarise(
        [
            _costed(START, 3.0),
            _costed(
                START + timedelta(days=1),
                1.0,
                cost={
                    "amount": 1.0,
                    "currency": "EUR",
                    "source": "tariff",
                    "partial": True,
                },
            ),
        ]
    )
    assert result["partial"] is True


def test_summary_ignores_sessions_that_were_never_costed():
    result = summary.summarise([_costed(START, 3.0), _session(energy_kwh=5.0)])
    assert result["sessions"] == 2
    assert result["cost"] == 3.0
    assert result["energy_kwh"] == 15.0


def test_summary_counts_grid_only_imported_sessions():
    # An imported BMW session carries only ``grid_kwh`` (never battery-side
    # ``energy_kwh``); it must still land in the monthly energy total, and the
    # measured grid figure wins when both are present.
    result = summary.summarise(
        [
            _session(grid_kwh=11.2),
            _session(energy_kwh=10.0, grid_kwh=12.0),
            _session(energy_kwh=5.0),
        ]
    )
    assert result["sessions"] == 3
    assert result["energy_kwh"] == 28.2


def test_cost_per_distance_needs_two_odometer_readings():
    one = summary.summarise([_costed(START, 3.0, mileage_km=1000.0)])
    assert one["distance_km"] is None
    assert one["cost_per_100km"] is None

    two = summary.summarise(
        [
            _costed(START, 3.0, mileage_km=1000.0),
            _costed(START + timedelta(days=5), 7.0, mileage_km=1400.0),
        ]
    )
    assert two["distance_km"] == 400.0
    assert two["cost_per_100km"] == 2.5


def test_a_stationary_odometer_yields_no_distance():
    result = summary.summarise(
        [
            _costed(START, 3.0, mileage_km=1000.0),
            _costed(START + timedelta(days=1), 3.0, mileage_km=1000.0),
        ]
    )
    assert result["distance_km"] is None
    assert result["cost_per_100km"] is None


def test_empty_summary_is_all_none_not_zero_cost():
    result = summary.summarise([])
    assert result["sessions"] == 0
    assert result["cost"] is None
    assert result["cost_per_100km"] is None


# --- battery health --------------------------------------------------------


def _charge(soc_from: float, soc_to: float, energy_kwh: float, **overrides):
    """A charge that adds ``energy_kwh`` while raising SoC from -> to."""

    data = {
        "vin": "WBY1",
        "start": START,
        "end": START + timedelta(hours=2),
        "soc_start": soc_from,
        "soc_end": soc_to,
        "energy_kwh": energy_kwh,
    }
    data.update(overrides)
    return ChargingSession(**data)


def test_capacity_from_a_wide_charge():
    # 40 kWh added over 50 SoC points implies an 80 kWh usable pack.
    result = health.usable_capacity([_charge(20.0, 70.0, 40.0)] * 10)
    assert result.usable_kwh == 80.0
    assert result.samples == 10
    assert result.confident is True


def test_narrow_charges_are_ignored_as_capacity_samples():
    # A 10-point top-up says nothing reliable about capacity and must not count.
    result = health.usable_capacity([_charge(60.0, 70.0, 8.0)] * 10)
    assert result.samples == 0
    assert result.usable_kwh is None
    assert result.confident is False


def test_stays_in_learning_until_enough_samples():
    result = health.usable_capacity([_charge(20.0, 80.0, 48.0)] * 3)
    assert result.samples == 3
    # We have a number, but not enough confidence to present it.
    assert result.usable_kwh == 80.0
    assert result.confident is False


def test_median_shrugs_off_one_bad_session():
    good = [_charge(20.0, 80.0, 48.0)] * 10  # 80 kWh each
    outlier = _charge(20.0, 80.0, 120.0)  # nonsense 200 kWh reading
    result = health.usable_capacity(good + [outlier])
    # The median ignores the single wild sample; a mean would be dragged up.
    assert result.usable_kwh == 80.0
    assert result.confident is True


def test_vs_new_percentage_needs_a_nominal_size():
    result = health.usable_capacity(
        [_charge(10.0, 90.0, 60.0)] * 10, nominal_kwh=80.0
    )
    assert result.usable_kwh == 75.0
    assert result.vs_new_percent == 93.8  # 75 / 80
    assert health.usable_capacity([_charge(10.0, 90.0, 60.0)]).vs_new_percent is None


def test_a_wild_divergence_from_bmws_figure_is_distrusted():
    # BMW says the pack is ~80 kWh but our maths lands at 40: an input is wrong,
    # so refuse to present the number even with plenty of samples.
    result = health.usable_capacity(
        [_charge(20.0, 80.0, 24.0)] * 12, sanity_kwh=80.0
    )
    assert result.usable_kwh == 40.0
    assert result.suspicious is True
    assert result.confident is False


def test_a_close_match_to_bmws_figure_is_trusted():
    result = health.usable_capacity(
        [_charge(20.0, 80.0, 46.0)] * 12, sanity_kwh=78.0, nominal_kwh=80.0
    )
    assert result.suspicious is False
    assert result.confident is True


def test_degradation_series_is_capacity_against_mileage():
    charges = [
        _charge(20.0, 80.0, 48.0, mileage_km=30000.0),
        _charge(20.0, 80.0, 46.0, mileage_km=10000.0),
        _charge(20.0, 80.0, 44.0, mileage_km=50000.0),
        _charge(60.0, 70.0, 8.0, mileage_km=40000.0),  # too narrow: excluded
        _charge(20.0, 80.0, 45.0),  # no odometer: excluded
    ]
    series = health.degradation_series(charges)
    # Oldest mileage first, narrow/odometer-less charges dropped.
    assert series == [[10000.0, 76.7], [30000.0, 80.0], [50000.0, 73.3]]


def test_degradation_series_keeps_only_the_most_recent_points():
    charges = [
        _charge(20.0, 80.0, 48.0, mileage_km=float(km))
        for km in range(1000, 6000, 1000)
    ]
    series = health.degradation_series(charges, limit=2)
    assert [point[0] for point in series] == [4000.0, 5000.0]


# --- energy balance --------------------------------------------------------
#
# The plug-side consumption figure, and the one the card leads with. It exists
# because the per-trip route cannot beat the resolution of the signal it is
# built from: BMW streams SoC as a whole percent, so trip energy is quantised at
# ~0.8 kWh on a 78 kWh pack. This reads the charging ledger instead -- odometer
# and SoC at each session end -- and never touches a trip.

CAP = 78.0


def _charge_point(start: datetime, *, odo: float, soc: float, kwh: float = 10.0):
    """A charging session as the balance sees it: a reading plus what it delivered."""

    return _session(
        start=start,
        end=start + timedelta(hours=2),
        mileage_km=odo,
        soc_end=soc,
        energy_kwh=kwh,
    )


def test_energy_balance_measures_between_two_charging_readings():
    charges = [
        _charge_point(START, odo=10000.0, soc=80.0, kwh=5.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=15.0),
    ]
    result = summary.energy_balance(charges, battery_capacity_kwh=CAP)
    # The opening session's own 5 kWh landed before the window opened.
    assert result["charged_kwh"] == 15.0
    # The pack ended 20 % emptier, so 15.6 kWh of the driving came out of it.
    assert result["battery_delta_kwh"] == -15.6
    assert result["used_kwh"] == 30.6
    assert result["distance_km"] == 100.0
    assert result["kwh_per_100km"] == 30.6


def test_energy_balance_ignores_the_trip_record_entirely():
    """Its whole value is surviving a month whose drives weren't all detected.

    Trip detection shipped after charging history did. Real data: a month with
    charging from the 1st but trips only from the 26th read 86.8 kWh/100 km off
    the trip distance and 20.4 off the odometer.
    """

    charges = [
        _charge_point(START, odo=17416.0, soc=55.0, kwh=8.0),
        _charge_point(START + timedelta(days=28), odo=17917.0, soc=95.0, kwh=133.2),
    ]
    result = summary.energy_balance(charges, battery_capacity_kwh=CAP)
    assert result["distance_km"] == 501.0
    assert result["kwh_per_100km"] == 20.4  # not the 86.8 a partial trip log gives


def test_energy_balance_needs_two_odometer_readings():
    one = [_charge_point(START, odo=10000.0, soc=80.0)]
    assert summary.energy_balance(one, battery_capacity_kwh=CAP) is None
    # A session with no odometer can't bound anything, however much it delivered.
    blind = one + [_session(start=START + timedelta(days=2), energy_kwh=40.0)]
    assert summary.energy_balance(blind, battery_capacity_kwh=CAP) is None


def test_energy_balance_refuses_a_span_too_short_to_mean_anything():
    """Below ~50 km the whole-percent SoC correction outweighs the energy used."""

    charges = [
        _charge_point(START, odo=10000.0, soc=80.0),
        _charge_point(START + timedelta(hours=6), odo=10010.0, soc=76.0, kwh=3.0),
    ]
    assert summary.energy_balance(charges, battery_capacity_kwh=CAP) is None


def test_energy_balance_needs_a_capacity_to_scale_the_correction():
    charges = [
        _charge_point(START, odo=10000.0, soc=80.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=15.0),
    ]
    assert summary.energy_balance(charges, battery_capacity_kwh=None) is None
    assert summary.energy_balance(charges, battery_capacity_kwh=0.0) is None


def test_energy_balance_prefers_the_measured_grid_figure():
    """Same rule as every other aggregate: grid_kwh outranks our estimate."""

    charges = [
        _charge_point(START, odo=10000.0, soc=60.0),
        _session(
            start=START + timedelta(days=3),
            end=START + timedelta(days=3, hours=2),
            mileage_km=10100.0,
            soc_end=60.0,
            energy_kwh=20.0,
            grid_kwh=23.0,
        ),
    ]
    result = summary.energy_balance(charges, battery_capacity_kwh=CAP)
    assert result["charged_kwh"] == 23.0
    assert result["kwh_per_100km"] == 23.0


def test_energy_balance_discards_an_impossible_result():
    """A corrupt odometer or a capacity from another car yields no figure."""

    # 100 km on 2 kWh: below anything a road vehicle does.
    thrifty = [
        _charge_point(START, odo=10000.0, soc=60.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=2.0),
    ]
    assert summary.energy_balance(thrifty, battery_capacity_kwh=CAP) is None
    # 100 km on 200 kWh: likewise.
    thirsty = [
        _charge_point(START, odo=10000.0, soc=60.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=200.0),
    ]
    assert summary.energy_balance(thirsty, battery_capacity_kwh=CAP) is None


def test_energy_balance_refuses_a_window_that_consumed_nothing():
    """More charge aboard at the close than went in means the window is wrong."""

    charges = [
        _charge_point(START, odo=10000.0, soc=10.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=90.0, kwh=1.0),
    ]
    assert summary.energy_balance(charges, battery_capacity_kwh=CAP) is None


def test_energy_balance_says_which_side_of_the_charger_it_measured():
    """``energy_kwh`` is battery-side; only a measured ``grid_kwh`` is at the plug.

    Presenting one as the other overstates consumption by the charging losses --
    or, when both figures are really battery-side, invents a loss between them
    that isn't there.
    """

    estimated = [
        _charge_point(START, odo=10000.0, soc=60.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=20.0),
    ]
    assert summary.energy_balance(estimated, battery_capacity_kwh=CAP)["source"] == (
        "battery"
    )

    measured = [
        _session(start=START, end=START + timedelta(hours=2),
                 mileage_km=10000.0, soc_end=60.0, energy_kwh=10.0, grid_kwh=11.0),
        _session(start=START + timedelta(days=3),
                 end=START + timedelta(days=3, hours=2),
                 mileage_km=10100.0, soc_end=60.0, energy_kwh=20.0, grid_kwh=23.0),
    ]
    result = summary.energy_balance(measured, battery_capacity_kwh=CAP)
    assert result["source"] == "grid"
    assert result["charged_kwh"] == 23.0


def test_one_estimated_session_makes_the_whole_balance_battery_side():
    """A mixture is not "at the plug" -- the estimated part never saw a meter."""

    mixed = [
        _charge_point(START, odo=10000.0, soc=60.0),
        _session(start=START + timedelta(days=1), end=START + timedelta(days=1, hours=2),
                 mileage_km=10050.0, soc_end=60.0, energy_kwh=10.0, grid_kwh=11.0),
        _charge_point(START + timedelta(days=3), odo=10100.0, soc=60.0, kwh=10.0),
    ]
    assert summary.energy_balance(mixed, battery_capacity_kwh=CAP)["source"] == "battery"


# --- late-started charges --------------------------------------------------
#
# A charge already running when the integration noticed it: both the energy
# integration and soc_start begin late, by different amounts, so the record is a
# floor on the energy and its SoC span covers only the watched part. Real data:
# a session opened at 56 % when the pack was last seen at 42 %, and the 11.045
# kWh it caught over a recorded 9 % span implied a 123 kWh pack on a 78 kWh car.

builders = load_module("history.sessions")


def test_a_charge_running_before_we_noticed_is_flagged():
    late = builders.SessionBuilder("WBY1", START, soc_start=56.0, soc_before=42.0)
    assert late.close(START + timedelta(hours=2), soc_end=65.0,
                      energy_kwh=11.045).late_start is True


def test_an_ordinary_charge_is_not_flagged():
    """Session SoC normally matches the last pre-charge reading exactly.

    Measured across 29 real sessions the median gap was +0.4 points, so the
    margin has to tolerate ordinary jitter without swallowing a real miss.
    """

    for before, start in ((42.0, 42.0), (51.0, 52.0), (53.0, 54.0), (58.0, 60.0)):
        session = builders.SessionBuilder(
            "WBY1", START, soc_start=start, soc_before=before
        ).close(START + timedelta(hours=2), soc_end=start + 30.0, energy_kwh=24.0)
        assert session.late_start is False, (before, start)


def test_no_pre_charge_reading_means_no_accusation():
    """The first charge after a restart has nothing to compare against."""

    session = builders.SessionBuilder(
        "WBY1", START, soc_start=56.0, soc_before=None
    ).close(START + timedelta(hours=2), soc_end=90.0, energy_kwh=26.0)
    assert session.late_start is False


def test_late_started_charges_are_not_capacity_samples():
    good = [_charge(20.0, 80.0, 46.8) for _ in range(10)]
    assert health.usable_capacity(good, sanity_kwh=78.0).confident is True

    # One wide-but-late session would imply a 123 kWh pack; it must not count.
    poisoned = good + [_charge(56.0, 90.0, 42.0, late_start=True)]
    result = health.usable_capacity(poisoned, sanity_kwh=78.0)
    assert result.samples == 10  # the late one contributed nothing
    assert result.usable_kwh == 78.0


def test_late_started_charges_stay_out_of_the_degradation_trend():
    charges = [
        _charge(20.0, 80.0, 46.8, mileage_km=10000.0),
        _charge(56.0, 90.0, 42.0, mileage_km=20000.0, late_start=True),
    ]
    assert [point[0] for point in health.degradation_series(charges)] == [10000.0]


def test_the_capacity_gate_admits_a_realistic_charge():
    """40 % was unreachable for a top-up-often owner; 25 % is not.

    A real car went 39 sessions without one charge spanning 40 %, so the sensor
    sat at "Learning (0/10)" permanently rather than slowly.
    """

    assert health.MIN_SOC_DELTA == 25.0
    # A 21 % charge is still too narrow to trust...
    assert health.usable_capacity([_charge(39.0, 60.0, 16.25)]).samples == 0
    # ...while a 30 % one now counts, and lands within 1 % of BMW's own figure.
    result = health.usable_capacity([_charge(39.0, 69.0, 23.4)], sanity_kwh=78.0)
    assert result.samples == 1
    assert result.usable_kwh == 78.0
    assert result.suspicious is False


# --- the energy ceiling ----------------------------------------------------
#
# BMW sends charging power in bursts with hour-long gaps, while the integrator
# holds the last sample and runs on a watchdog tick. One real session held a
# 3.54 kW reading for 162 minutes and claimed 11.10 kWh where the battery took
# 5.46. SoC bounds that: the pack cannot absorb more than its charge level rose.

CEIL_MARGIN = builders.SOC_CEILING_MARGIN_PERCENT


def test_ceiling_is_the_soc_rise_plus_a_margin():
    # 10 points of a 78 kWh pack = 7.8 kWh, plus 2 points of slack = 9.36.
    assert builders.energy_ceiling_kwh(40.0, 50.0, 78.0) == 9.36


def test_ceiling_never_clips_an_honest_session():
    """A real 21-point charge integrated 16.25 kWh; SoC implies 16.38."""

    ceiling = builders.energy_ceiling_kwh(39.0, 60.0, 78.0)
    assert ceiling > 16.25


def test_ceiling_catches_the_runaway_that_prompted_it():
    """The 09:15 session: 7 points of SoC, 11.10 kWh claimed."""

    ceiling = builders.energy_ceiling_kwh(45.0, 52.0, 78.0)
    assert min(11.10, ceiling) == 7.02  # was 11.10; the battery took ~5.46


def test_ceiling_is_a_bound_not_a_correction():
    """A session that under-read is left exactly as it is.

    Nothing here can tell an under-read from a slow charge, so raising a total
    to meet the ceiling would invent energy rather than withhold it.
    """

    ceiling = builders.energy_ceiling_kwh(32.0, 40.0, 78.0)   # 8 points -> 7.8
    assert min(3.71, ceiling) == 3.71


def test_ceiling_needs_soc_and_capacity_or_it_does_not_bind():
    assert builders.energy_ceiling_kwh(None, 50.0, 78.0) is None
    assert builders.energy_ceiling_kwh(40.0, None, 78.0) is None
    assert builders.energy_ceiling_kwh(40.0, 50.0, None) is None
    assert builders.energy_ceiling_kwh(40.0, 50.0, 0.0) is None


def test_a_falling_soc_still_allows_the_margin():
    """SoC dipping mid-charge must not produce a negative ceiling."""

    assert builders.energy_ceiling_kwh(50.0, 48.0, 78.0) == CEIL_MARGIN / 100 * 78.0


def test_bounding_the_total_lets_it_catch_up_when_soc_lands():
    """Why the bound applies to the running total, not to each increment.

    SoC arrives a whole percent at a time, so it always lags the charge. Capping
    each increment would write off the energy that flowed while the reading was
    pending; bounding the total lets it recover the moment the reading lands.
    """

    raw = 0.0
    exposed = []
    # A steady 1 kWh a step. Six of them is 6 kWh, which on a 78 kWh pack is
    # ~7.7 points of SoC -- but the reading only lands once, halfway through.
    for step in range(6):
        raw += 1.0
        soc_now = 40.0 if step < 3 else 48.0
        ceiling = builders.energy_ceiling_kwh(40.0, soc_now, 78.0)
        exposed.append(min(raw, ceiling))
    # Held back while the reading was pending...
    assert exposed[2] < 3.0
    # ...and fully recovered once it landed, rather than lost for good.
    assert exposed[5] == 6.0


# --- SoC that never moves --------------------------------------------------
#
# Issue #6: an iX xDrive40 that does not stream ``batteryManagement.header``.
# Its only SoC is whatever the REST bootstrap left there, so every session
# opened and closed on the same stale 38 %. Four DC charges, each peaking above
# 100 kW over ~25 minutes, were filed as "38 -> 38%" and 1.4 kWh -- which is
# exactly the ceiling's bare margin, 2 % of the pack, and nothing to do with the
# charge. Duration and peak power were right throughout, because neither goes
# anywhere near SoC.


def test_a_reading_from_during_the_session_counts():
    assert builders.soc_is_from_session(START + timedelta(minutes=5), START) is True


def test_a_reading_from_days_before_the_session_does_not():
    """The iX's case: the bootstrap value, three days old and never replaced."""

    assert builders.soc_is_from_session(START - timedelta(days=3), START) is False


def test_bmw_timestamp_skew_at_the_plug_in_is_tolerated():
    """BMW's own timestamps trail ours by seconds; that is not staleness."""

    assert builders.soc_is_from_session(START - timedelta(seconds=30), START) is True
    assert builders.soc_is_from_session(START - timedelta(minutes=10), START) is False


def test_a_missing_reading_or_start_never_counts():
    assert builders.soc_is_from_session(None, START) is False
    assert builders.soc_is_from_session(START, None) is False


def test_a_stale_soc_grants_no_ceiling_rather_than_the_margin_alone():
    """Why the guard has to gate the ceiling and not just the arc.

    With a frozen reading at both ends the rise is zero, and the ceiling
    collapses to the margin: 1.42 kWh on the iX's 71 kWh pack, against a real
    charge of ~30. Withholding the ceiling entirely leaves the figure on the
    power integration -- imperfect, but the right order of magnitude.
    """

    frozen = builders.energy_ceiling_kwh(38.0, 38.0, 71.0)
    assert round(frozen, 2) == 1.42
    assert min(30.0, frozen) == frozen  # the bug: a 30 kWh charge filed as 1.42


def test_an_unwatched_session_records_no_soc_arc_at_all():
    """No reading during the charge means no claim about the pack.

    Not even the start: on the iX that is a bootstrap value from a previous day,
    describing some other charge. Left empty, BMW's own charging history can
    fill both ends in on the next import.
    """

    session = builders.SessionBuilder("WBY1", START, soc_start=38.0).close(
        START + timedelta(minutes=25), energy_kwh=29.8
    )
    assert session.soc_start is None
    assert session.soc_end is None
    assert session.soc_delta is None
    # The half of the record that never depended on SoC is untouched.
    assert session.energy_kwh == 29.8


def test_a_watched_session_still_records_both_ends():
    builder = builders.SessionBuilder("WBY1", START, soc_start=38.0)
    builder.note_soc(52.0)
    session = builder.close(START + timedelta(minutes=25), soc_end=61.0,
                            energy_kwh=17.5)
    assert (session.soc_start, session.soc_end) == (38.0, 61.0)
    assert session.soc_delta == 23.0
