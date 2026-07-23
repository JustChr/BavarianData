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
