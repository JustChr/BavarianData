"""Unit tests for attributing charging energy to PV, house battery and grid.

The feature answers "how much of my charging was sun?", so the failure mode to
guard against is not a crash -- it is a plausible-looking number that flatters
the roof. Every test here is really the same assertion: the split must follow
the meters, and where the meters cannot say, it must admit that instead of
inventing a share.
"""

from __future__ import annotations

import json

from .conftest import load_module

mix = load_module("history.energy_mix")

PV = mix.SOURCE_PV
BATTERY = mix.SOURCE_BATTERY
GRID = mix.SOURCE_GRID
UNKNOWN = mix.SOURCE_UNKNOWN


def _shares(**kwargs):
    return mix.supply_shares(**kwargs)


# --- the instantaneous mix -------------------------------------------------


def test_pure_sunshine_is_all_pv():
    """Everything on site is coming off the roof, and some is exported."""

    shares = _shares(pv_w=6000.0, grid_w=-2000.0, battery_w=0.0)
    assert shares == {PV: 1.0, BATTERY: 0.0, GRID: 0.0}


def test_night_charging_is_all_grid():
    shares = _shares(pv_w=0.0, grid_w=7000.0, battery_w=0.0)
    assert shares == {PV: 0.0, BATTERY: 0.0, GRID: 1.0}


def test_a_mixed_instant_splits_proportionally():
    """The car gets the same mix as every other load at that moment.

    4 kW of PV reaching loads, 2 kW out of the battery, 2 kW imported: the site
    is 50 % sun, 25 % battery, 25 % grid, and so is the car.
    """

    shares = _shares(pv_w=4000.0, grid_w=2000.0, battery_w=2000.0)
    assert shares[PV] == 0.5
    assert shares[BATTERY] == 0.25
    assert shares[GRID] == 0.25
    assert sum(shares.values()) == 1.0


def test_pv_charging_the_house_battery_is_not_yet_serving_loads():
    """It will be attributed later, as battery -- never twice.

    6 kW of PV with 4 kW going into the battery leaves 2 kW for loads, all of
    which the grid is not needed for.
    """

    shares = _shares(pv_w=6000.0, grid_w=0.0, battery_w=-4000.0)
    assert shares == {PV: 1.0, BATTERY: 0.0, GRID: 0.0}

    # And with an import alongside it, the stored part still doesn't count.
    shares = _shares(pv_w=6000.0, grid_w=2000.0, battery_w=-4000.0)
    assert shares[PV] == 0.5
    assert shares[GRID] == 0.5


def test_exported_pv_does_not_count_as_supplying_the_car():
    """Otherwise a sunny day would credit the car with power it never drew."""

    # 9 kW generated, 8 kW exported: only the remaining 1 kW is serving the
    # site, and that kilowatt is the whole of the site's supply.
    shares = _shares(pv_w=9000.0, grid_w=-8000.0, battery_w=None)
    assert shares == {PV: 1.0, BATTERY: 0.0, GRID: 0.0}


def test_a_house_without_storage_gets_a_two_way_split():
    shares = _shares(pv_w=3000.0, grid_w=1000.0, battery_w=None)
    assert shares[PV] == 0.75
    assert shares[BATTERY] == 0.0
    assert shares[GRID] == 0.25


# --- when it must refuse to answer ----------------------------------------


def test_a_missing_sensor_yields_no_mix():
    assert _shares(pv_w=None, grid_w=1000.0) is None
    assert _shares(pv_w=1000.0, grid_w=None) is None


def test_a_sleeping_house_yields_no_mix():
    """Below the noise floor a few watts of meter offset would decide it all."""

    assert _shares(pv_w=0.0, grid_w=10.0, battery_w=0.0) is None


# --- accumulating over a session ------------------------------------------


def test_energy_is_split_as_it_arrives_not_at_the_end():
    """The point of the whole design: a charge that starts in sun and ends at
    night is half and half, not whatever the last sample said."""

    accumulator = mix.MixAccumulator()
    accumulator.add(5.0, _shares(pv_w=6000.0, grid_w=-1000.0, battery_w=0.0))
    accumulator.add(5.0, _shares(pv_w=0.0, grid_w=7000.0, battery_w=0.0))

    record = accumulator.as_record()
    assert record[PV] == 5.0
    assert record[GRID] == 5.0
    assert record["solar_percent"] == 50.0


def test_unattributable_energy_is_kept_separate():
    accumulator = mix.MixAccumulator()
    accumulator.add(4.0, _shares(pv_w=4000.0, grid_w=0.0, battery_w=0.0))
    accumulator.add(6.0, None)

    record = accumulator.as_record()
    assert record[PV] == 4.0
    assert record[UNKNOWN] == 6.0
    # Measured against what could be attributed, not the session total: with a
    # sensor missing for 6 kWh, "40 % solar" would be a claim about the sensor.
    assert record["solar_percent"] == 100.0
    assert accumulator.total_kwh == 10.0
    assert accumulator.known_kwh == 4.0


def test_the_buckets_always_add_back_up_to_what_was_delivered():
    accumulator = mix.MixAccumulator()
    for _ in range(10):
        accumulator.add(1.0, _shares(pv_w=3000.0, grid_w=1000.0, battery_w=2000.0))
    assert round(accumulator.total_kwh, 6) == 10.0


def test_a_session_with_nothing_attributable_records_no_mix():
    """ "We could not tell" must not look like "none of it was solar"."""

    accumulator = mix.MixAccumulator()
    accumulator.add(10.0, None)
    assert accumulator.as_record() is None
    assert accumulator.solar_percent() is None


def test_the_accumulator_survives_a_restart():
    accumulator = mix.MixAccumulator()
    accumulator.add(3.0, _shares(pv_w=4000.0, grid_w=0.0, battery_w=0.0))
    restored = mix.MixAccumulator.from_dict(json.loads(json.dumps(accumulator.to_dict())))
    assert restored.totals == accumulator.totals

    restored.add(1.0, _shares(pv_w=0.0, grid_w=5000.0, battery_w=0.0))
    assert restored.as_record() == {PV: 3.0, GRID: 1.0, "solar_percent": 75.0}


def test_a_corrupt_mix_snapshot_starts_clean():
    assert mix.MixAccumulator.from_dict(None).totals == {}
    assert mix.MixAccumulator.from_dict({"totals": "nonsense"}).totals == {}
    assert mix.MixAccumulator.from_dict({"totals": {PV: "some"}}).totals == {}


# --- monthly totals --------------------------------------------------------


def test_mixes_merge_across_sessions():
    merged = mix.merge_mix(
        [
            {PV: 8.0, GRID: 2.0, "solar_percent": 80.0},
            {PV: 0.0, GRID: 10.0, "solar_percent": 0.0},
        ]
    )
    assert merged[PV] == 8.0
    assert merged[GRID] == 12.0
    assert merged["solar_percent"] == 40.0


def test_a_session_without_a_mix_does_not_dilute_the_month():
    """A charge recorded before the sensors were set up contributes nothing."""

    merged = mix.merge_mix([{PV: 8.0, GRID: 2.0}, None, {}])
    assert merged[PV] == 8.0
    assert merged["solar_percent"] == 80.0
    assert mix.merge_mix([None, None]) is None


# --- how it surfaces ------------------------------------------------------


def _session(**overrides):
    from datetime import datetime, timedelta, timezone

    models = load_module("history.models")
    start = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    data = {
        "vin": "WBY1",
        "start": start,
        "end": start + timedelta(hours=2),
        "energy_kwh": 10.0,
    }
    data.update(overrides)
    return models.ChargingSession(**data)


def test_the_month_summary_carries_the_mix():
    summary = load_module("history.summary")
    result = summary.summarise(
        [
            _session(energy_mix={PV: 8.0, GRID: 2.0, "solar_percent": 80.0}),
            _session(energy_mix={GRID: 10.0, "solar_percent": 0.0}),
        ]
    )
    assert result["energy_mix"][PV] == 8.0
    assert result["energy_mix"]["solar_percent"] == 40.0


def test_a_month_with_no_attribution_reports_no_mix():
    summary = load_module("history.summary")
    assert summary.summarise([_session()])["energy_mix"] is None


def test_the_export_names_every_bucket():
    export = load_module("history.export")
    csv_text = export.sessions_csv(
        [_session(energy_mix={PV: 6.0, BATTERY: 1.5, GRID: 2.5, "solar_percent": 60.0})]
    )
    header, row = (line.split(",") for line in csv_text.splitlines()[:2])
    cells = dict(zip(header, row, strict=True))
    assert cells["pv_kwh"] == "6"
    assert cells["house_battery_kwh"] == "1.5"
    assert cells["grid_mix_kwh"] == "2.5"
    assert cells["solar_percent"] == "60"


def test_the_export_leaves_an_unattributed_session_blank_not_zero():
    """A charge from before the sensors existed must not read as "no sun"."""

    export = load_module("history.export")
    csv_text = export.sessions_csv([_session()])
    header = csv_text.splitlines()[0].split(",")
    row = csv_text.splitlines()[1].split(",")
    cells = dict(zip(header, row, strict=True))
    assert cells["pv_kwh"] == ""
    assert cells["solar_percent"] == ""
