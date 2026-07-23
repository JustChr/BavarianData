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
