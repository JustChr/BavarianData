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
    # Without a zone resolver, only the (unresolved) zone slot is kept -- raw
    # coordinates are never persisted.
    assert session.location == {"zone": None}


def test_zone_fn_resolves_location():
    session = session_from_cardata("WBY1", _raw(), zone_fn=lambda lat, lon: "Home")
    assert session.location == {"zone": "Home"}


def test_location_dropped_without_coordinates():
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


def test_distinct_charges_stay_separate():
    first = session_from_cardata("WBY1", _raw())
    later = session_from_cardata(
        "WBY1",
        _raw(startTime=START_EPOCH + 6 * 3600, endTime=START_EPOCH + 7 * 3600),
    )
    result, added, updated = merge_cardata_sessions([], [first, later])
    assert (added, updated) == (2, 0)
    assert len(result) == 2
