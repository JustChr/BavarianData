"""Unit tests for importing BMW's REST charging history.

Covers the payload mapping and the enrich-in-place merge without an HA install
(``history.store``, which persists, is excluded on purpose). The merge is the
part that must not regress: importing a charge that also streamed live has to
update the existing session, never duplicate it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

models = load_module("history.models")
cardata_history = load_module("history.cardata_history")
pricing = load_module("history.pricing")

ChargingSession = models.ChargingSession
session_from_cardata = cardata_history.session_from_cardata
merge_cardata_sessions = cardata_history.merge_cardata_sessions

# 2026-07-19 09:14:04 UTC-ish; exact value only matters for round-tripping.
START_EPOCH = 1784628184
START_DT = datetime.fromtimestamp(START_EPOCH, tz=timezone.utc)


def _raw(**overrides) -> dict:
    raw = {
        "startTime": START_EPOCH,
        "endTime": START_EPOCH + 3600,
        "energyConsumedFromPowerGridKwh": 15.92,
        "displayedStartSoc": 66,
        "displayedSoc": 82,
        "mileage": 17796,
        "mileageUnits": "KM",
        "chargingLocation": {
            "municipality": "Stockerau",
            "formattedAddress": "Professor-Otto-Zeiller-Straße 24, 2000 Stockerau",
            "mapMatchedLatitude": 48.39092,
            "mapMatchedLongitude": 16.22995,
        },
        "chargingBlocks": [
            {"startTime": START_EPOCH, "endTime": START_EPOCH + 60, "averagePowerGridKw": 10.8},
            {"startTime": START_EPOCH + 60, "endTime": START_EPOCH + 120, "averagePowerGridKw": 11.2},
            {"startTime": START_EPOCH + 120, "endTime": START_EPOCH + 180, "averagePowerGridKw": 3.5},
        ],
    }
    raw.update(overrides)
    return raw


# --- mapping ---------------------------------------------------------------


def test_maps_core_fields():
    session = session_from_cardata("WBY1", _raw())
    assert session is not None
    assert session.vin == "WBY1"
    assert session.start == START_DT
    assert session.end == START_DT + timedelta(hours=1)
    assert session.grid_kwh == 15.92
    assert session.energy_kwh is None  # battery-side is never invented from REST
    assert session.soc_start == 66
    assert session.soc_end == 82
    assert session.mileage_km == 17796
    assert session.enriched is True
    # Without a zone resolver the point matches no zone, so BMW's own address is
    # kept as the label -- raw coordinates are never persisted.
    assert session.location == {
        "zone": None,
        "address": "Professor-Otto-Zeiller-Straße 24, 2000 Stockerau",
    }


def test_zone_fn_resolves_location():
    session = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: "Home")
    # A matching zone wins over (and replaces) the address; no coordinates kept.
    assert session.location == {"zone": "Home"}


def test_address_kept_when_no_zone_matches():
    session = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: None)
    assert session.location == {
        "zone": None,
        "address": "Professor-Otto-Zeiller-Straße 24, 2000 Stockerau",
    }


def test_location_dropped_without_coordinates_or_address():
    session = session_from_cardata(
        "WBY1", _raw(chargingLocation={"municipality": "Stockerau"})
    )
    assert session.location is None


def test_peak_from_all_blocks_and_curve_offsets():
    session = session_from_cardata("WBY1", _raw())
    assert session.peak_power_kw == 11.2
    # Offsets are seconds since the session start, not epochs.
    assert session.power_curve[0] == [0.0, 10.8]
    assert session.power_curve[1] == [60.0, 11.2]


def test_ongoing_session_is_skipped():
    # The top-of-list charge has no endTime and no grid energy yet.
    assert session_from_cardata("WBY1", _raw(endTime=None, energyConsumedFromPowerGridKwh=None)) is None


def test_miles_converted_to_km():
    session = session_from_cardata("WBY1", _raw(mileage=100, mileageUnits="MILES"))
    assert session.mileage_km == 160.9


def test_curve_downsampled_to_cap():
    blocks = [
        {"startTime": START_EPOCH + i, "averagePowerGridKw": 1.0 + i / 1000.0}
        for i in range(1000)
    ]
    session = session_from_cardata("WBY1", _raw(chargingBlocks=blocks))
    assert len(session.power_curve) <= cardata_history.MAX_CURVE_POINTS
    # Peak is taken before downsampling, so thinning never lowers it.
    assert session.peak_power_kw == round(1.0 + 999 / 1000.0, 3)


def test_cost_fn_backfills_from_grid_kwh():
    def cost_fn(grid_kwh):
        return pricing.fixed_cost(grid_kwh, 0.30, "EUR")

    session = session_from_cardata("WBY1", _raw(), cost_fn=cost_fn)
    assert session.cost == {"amount": round(15.92 * 0.30, 2), "currency": "EUR", "source": "tariff"}


# --- merge / enrichment ----------------------------------------------------


def _live_session(**overrides) -> ChargingSession:
    """A session as the live stream would have recorded it (battery-side only)."""
    data = {
        "vin": "WBY1",
        "start": START_DT + timedelta(minutes=2),  # stream lag vs BMW's startTime
        "end": START_DT + timedelta(minutes=58),
        "energy_kwh": 14.0,
        "soc_start": 66.0,
    }
    data.update(overrides)
    return ChargingSession(**data)


def test_new_charge_is_added():
    result, added, updated = merge_cardata_sessions([], [session_from_cardata("WBY1", _raw())])
    assert (added, updated) == (1, 0)
    assert len(result) == 1


def test_overlapping_live_session_is_enriched_in_place():
    live = _live_session()
    live_id = live.id
    imported = session_from_cardata("WBY1", _raw())

    result, added, updated = merge_cardata_sessions([live], [imported])

    assert (added, updated) == (0, 1)
    assert len(result) == 1
    survivor = result[0]
    # Kept the live session's identity/timeline (no duplicate on the card)...
    assert survivor.id == live_id
    assert survivor.start == live.start
    assert survivor.energy_kwh == 14.0  # battery-side figure preserved
    # ...but gained BMW's measured grid figure and metadata.
    assert survivor.grid_kwh == 15.92
    assert survivor.mileage_km == 17796
    assert survivor.enriched is True


def test_import_is_idempotent():
    live = _live_session()
    imported = session_from_cardata("WBY1", _raw())
    once, _, _ = merge_cardata_sessions([live], [imported])
    twice, added, updated = merge_cardata_sessions(once, [session_from_cardata("WBY1", _raw())])
    assert (added, updated) == (0, 1)
    assert len(twice) == 1


def test_reimport_upgrades_unresolved_location_to_zone():
    # A legacy record imported before the zone lookup existed: no zone.
    legacy = session_from_cardata("WBY1", _raw())
    assert legacy.location == {
        "zone": None,
        "address": "Professor-Otto-Zeiller-Straße 24, 2000 Stockerau",
    }
    # Re-importing now that the point resolves to a zone corrects it in place.
    fresh = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: "Home")
    result, added, updated = merge_cardata_sessions([legacy], [fresh])
    assert (added, updated) == (0, 1)
    assert result[0].location == {"zone": "Home"}


def test_reimport_does_not_downgrade_a_resolved_zone():
    resolved = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: "Home")
    # A later import whose point matched no zone must not blank the Home label.
    blank = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: None)
    result, _, updated = merge_cardata_sessions([resolved], [blank])
    assert updated == 1
    assert result[0].location == {"zone": "Home"}


def test_distinct_charges_stay_separate():
    first = session_from_cardata("WBY1", _raw())
    later = session_from_cardata(
        "WBY1",
        _raw(startTime=START_EPOCH + 6 * 3600, endTime=START_EPOCH + 7 * 3600),
    )
    result, added, updated = merge_cardata_sessions([], [first, later])
    assert (added, updated) == (2, 0)
    assert len(result) == 2


def test_status_flap_fragments_are_collapsed():
    # One physical plug-in that a charging.status flap split into two live
    # sessions: a short fragment then the bulk of the charge. Both fall inside
    # BMW's charge window.
    fragment = _live_session(
        start=START_DT + timedelta(minutes=2),
        end=START_DT + timedelta(minutes=5),
        energy_kwh=1.0,
        soc_start=66.0,
        soc_end=67.0,
        peak_power_kw=3.5,
    )
    bulk = _live_session(
        start=START_DT + timedelta(minutes=6),
        end=START_DT + timedelta(minutes=58),
        energy_kwh=13.0,
        soc_start=67.0,
        soc_end=82.0,
        peak_power_kw=11.0,
    )
    imported = session_from_cardata("WBY1", _raw())

    result, added, updated = merge_cardata_sessions([fragment, bulk], [imported])

    # Collapsed to a single enriched record -- not two live fragments plus an
    # import, which would triple-count the energy in the monthly total.
    assert (added, updated) == (0, 1)
    assert len(result) == 1
    survivor = result[0]
    assert survivor.enriched is True
    assert survivor.grid_kwh == 15.92
    # The one figure every aggregate counts is BMW's grid energy, once.
    assert survivor.effective_energy_kwh == 15.92
    # Timeline and SoC swing span the whole charge, not the one merge target.
    assert survivor.start == START_DT + timedelta(minutes=2)
    assert survivor.soc_start == 66.0
    assert survivor.soc_end == 82.0
    # Peak is the max across the fragments and BMW's own curve (11.2 kW here).
    assert survivor.peak_power_kw == 11.2


def test_reimport_heals_a_previously_split_charge():
    # The state a pre-fix build leaves behind: the import enriched one fragment
    # and orphaned the other as a live-only record.
    enriched = _live_session(
        start=START_DT + timedelta(minutes=2),
        end=START_DT + timedelta(minutes=5),
        energy_kwh=1.0,
        grid_kwh=15.92,
        enriched=True,
    )
    orphan = _live_session(
        start=START_DT + timedelta(minutes=6),
        end=START_DT + timedelta(minutes=58),
        energy_kwh=13.0,
    )

    result, added, updated = merge_cardata_sessions(
        [enriched, orphan], [session_from_cardata("WBY1", _raw())]
    )

    # A single re-import reconciles them: the enriched half survives, the
    # live-only orphan is absorbed.
    assert (added, updated) == (0, 1)
    assert len(result) == 1
    assert result[0].enriched is True
    assert result[0].effective_energy_kwh == 15.92


# --- repairing a frozen SoC arc (issue #6) ---------------------------------


def _frozen_session(**overrides) -> ChargingSession:
    """A charge as a pre-v0.9.6 build stored it: SoC never streamed."""
    data = {
        "soc_start": 38.0,
        "soc_end": 38.0,
        "energy_kwh": 1.4,  # the energy ceiling's bare margin
        "cost": {"amount": 0.42, "currency": "EUR", "source": "tariff"},
        "energy_mix": {"pv": 0.4, "grid": 1.0, "solar_percent": 28.6},
    }
    data.update(overrides)
    return _live_session(**data)


def test_import_repairs_a_frozen_soc_arc():
    frozen = _frozen_session()
    frozen_id = frozen.id

    result, added, updated = merge_cardata_sessions(
        [frozen], [session_from_cardata("WBY1", _raw())]
    )

    assert (added, updated) == (0, 1)
    survivor = result[0]
    assert survivor.id == frozen_id
    assert (survivor.soc_start, survivor.soc_end) == (66, 82)
    assert survivor.grid_kwh == 15.92
    # Everything that accrued from the capped figure is dropped, not kept wrong.
    assert survivor.energy_kwh is None
    assert survivor.energy_mix is None
    # A live-price cost can't be rebuilt: kept, but no longer passed off as whole.
    assert survivor.cost == {
        "amount": 0.42,
        "currency": "EUR",
        "source": "tariff",
        "partial": True,
    }


def test_repaired_charge_is_costed_again_on_a_fixed_tariff():
    def cost_fn(grid_kwh):
        return pricing.fixed_cost(grid_kwh, 0.30, "EUR")

    result, _, _ = merge_cardata_sessions(
        [_frozen_session()], [session_from_cardata("WBY1", _raw(), cost_fn=cost_fn)]
    )
    assert result[0].cost == {
        "amount": round(15.92 * 0.30, 2),
        "currency": "EUR",
        "source": "tariff",
    }


def test_a_rise_of_exactly_the_threshold_repairs():
    result, _, _ = merge_cardata_sessions(
        [_frozen_session()],
        [session_from_cardata("WBY1", _raw(displayedStartSoc=38, displayedSoc=40))],
    )
    assert (result[0].soc_start, result[0].soc_end) == (38, 40)
    assert result[0].energy_kwh is None


def test_frozen_soc_repair_is_idempotent():
    once, _, _ = merge_cardata_sessions(
        [_frozen_session()], [session_from_cardata("WBY1", _raw())]
    )
    twice, added, updated = merge_cardata_sessions(
        once, [session_from_cardata("WBY1", _raw())]
    )
    assert (added, updated) == (0, 1)
    assert len(twice) == 1
    assert (twice[0].soc_start, twice[0].soc_end) == (66, 82)
    assert twice[0].cost["partial"] is True


def _assert_untouched(existing: ChargingSession, raw: dict) -> None:
    before = existing.to_dict()
    result, _, _ = merge_cardata_sessions([existing], [session_from_cardata("WBY1", raw)])
    assert len(result) == 1
    survivor = result[0]
    assert (survivor.soc_start, survivor.soc_end) == (before["soc_start"], before["soc_end"])
    assert survivor.energy_kwh == before["energy_kwh"]
    assert survivor.energy_mix == before["energy_mix"]
    assert not (survivor.cost or {}).get("partial")


def test_flat_on_both_sides_is_left_alone():
    # Plugged in at full charge, pre-heating: grid energy flows, SoC really is flat.
    _assert_untouched(
        _frozen_session(soc_start=80.0, soc_end=80.0, energy_kwh=2.1),
        _raw(displayedStartSoc=80, displayedSoc=80, energyConsumedFromPowerGridKwh=2.3),
    )


def test_a_one_point_rise_is_left_alone():
    # A small top-up whose single step landed on BMW's side of the rounding only.
    _assert_untouched(
        _frozen_session(soc_start=80.0, soc_end=80.0, energy_kwh=0.9),
        _raw(displayedStartSoc=80, displayedSoc=81, energyConsumedFromPowerGridKwh=1.0),
    )


def test_a_real_arc_is_left_alone():
    _assert_untouched(
        _frozen_session(soc_start=66.0, soc_end=81.0, energy_kwh=14.0),
        _raw(),
    )


def test_a_restart_cut_record_is_left_alone():
    _assert_untouched(_frozen_session(late_start=True), _raw())
    _assert_untouched(_frozen_session(interrupted=True), _raw())


def test_a_record_reaching_outside_bmws_window_is_left_alone():
    # Overlaps BMW's charge through the merge tolerance but runs well past its
    # end, so it is a neighbouring charge, not a stale copy of this one.
    _assert_untouched(
        _frozen_session(
            start=START_DT + timedelta(minutes=40),
            end=START_DT + timedelta(minutes=100),
        ),
        _raw(),
    )


def test_bmw_without_soc_is_left_alone():
    _assert_untouched(_frozen_session(), _raw(displayedStartSoc=None, displayedSoc=None))
