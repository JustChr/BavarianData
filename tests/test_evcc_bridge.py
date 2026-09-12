"""The evcc / wallbox bridge: what we publish, and what we accept back.

Two halves, both Home Assistant-free and therefore testable here (the
publishing glue in ``bridge.py`` needs ``hass`` and is verified on the live
instance instead):

- **Outbound** -- ``evcc.py`` turns a vehicle snapshot into MQTT payloads and
  generates the evcc ``custom`` vehicle config that reads them. The rule under
  test throughout is that *unknown publishes nothing*: a charge controller acts
  on these numbers, so a confident zero is worse than a gap.
- **Inbound** -- ``history/sessions.py`` accepts a bound wallbox meter's reading
  as a session's measured ``grid_kwh``, but only where it is physically
  consistent with what the pack absorbed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from .conftest import load_module

evcc = load_module("evcc")
sessions = load_module("history.sessions")

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
VIN = "WBAEXAMPLE0000001"


def snap(**kwargs) -> "evcc.BridgeSnapshot":
    return evcc.BridgeSnapshot(vin=VIN, **kwargs)


# --------------------------------------------------------------------------
# Topic prefix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bavariandata", "bavariandata"),
        # What a user actually types.
        ("bavariandata/", "bavariandata"),
        ("/evcc/bmw/", "evcc/bmw"),
        ("  cars  ", "cars"),
        # Nothing usable -> the default, never an empty topic level.
        ("", evcc.DEFAULT_PREFIX),
        ("   ", evcc.DEFAULT_PREFIX),
        ("/", evcc.DEFAULT_PREFIX),
        (None, evcc.DEFAULT_PREFIX),
        (17, evcc.DEFAULT_PREFIX),
        # MQTT wildcards: a broker rejects a publish to these outright, so
        # accepting one would mean publishing into a void.
        ("home/#", evcc.DEFAULT_PREFIX),
        ("home/+/car", evcc.DEFAULT_PREFIX),
    ],
)
def test_the_topic_prefix_is_always_usable(raw, expected) -> None:
    assert evcc.normalize_prefix(raw) == expected


def test_the_default_prefix_is_the_one_the_options_flow_offers() -> None:
    """Two spellings of the same default is how a topic layout drifts."""

    const = load_module("const")
    assert evcc.DEFAULT_PREFIX == const.DEFAULT_BRIDGE_PREFIX


# --------------------------------------------------------------------------
# evcc's A/B/C
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("charging", "plugged", "expected"),
    [
        (True, True, evcc.STATUS_CHARGING),
        # Charging outranks the plug: a car that is charging is connected,
        # whatever a missing or lagging port descriptor says.
        (True, None, evcc.STATUS_CHARGING),
        (True, False, evcc.STATUS_CHARGING),
        (False, True, evcc.STATUS_CONNECTED),
        (False, False, evcc.STATUS_DISCONNECTED),
        (None, True, evcc.STATUS_CONNECTED),
        (None, False, evcc.STATUS_DISCONNECTED),
    ],
)
def test_status_maps_onto_evccs_alphabet(charging, plugged, expected) -> None:
    assert evcc.evcc_status(charging=charging, plugged=plugged) == expected


def test_a_car_that_reports_neither_gets_no_status() -> None:
    """The one case where guessing would actively break charging.

    ``A`` means "no vehicle connected". Told that, an identifying charger stops
    charging the car it is plugged into -- so a car that streams neither a
    charging status nor a charging port must produce no status at all.
    """

    assert evcc.evcc_status(charging=None, plugged=None) is None
    assert snap(soc=80.0).status is None
    assert evcc.TOPIC_STATUS not in evcc.bridge_payloads(snap(soc=80.0))


# --------------------------------------------------------------------------
# Payloads: unknown publishes nothing
# --------------------------------------------------------------------------


def test_nothing_known_publishes_nothing() -> None:
    assert evcc.bridge_payloads(snap()) == {}
    assert snap().is_empty is True


def test_an_unknown_field_is_omitted_not_zeroed() -> None:
    """The house rule, with teeth: a published 0 would charge a full battery."""

    payloads = evcc.bridge_payloads(
        snap(soc=86.4, charging=True, plugged=True, charge_power_kw=10.82)
    )
    for absent in (evcc.TOPIC_RANGE, evcc.TOPIC_ODOMETER, evcc.TOPIC_LIMIT_SOC):
        assert absent not in payloads
    assert payloads[evcc.TOPIC_SOC] == "86.4"
    assert payloads[evcc.TOPIC_STATUS] == evcc.STATUS_CHARGING
    assert payloads[evcc.TOPIC_CHARGE_POWER] == "10.82"


def test_a_known_zero_is_published() -> None:
    """Absent and zero are different facts, and both are publishable.

    A car sitting at 0 % or drawing 0 kW is a real reading. Conflating it with
    "we don't know" in either direction would be the same mistake twice.
    """

    payloads = evcc.bridge_payloads(snap(soc=0.0, charging=False, plugged=True))
    assert payloads[evcc.TOPIC_SOC] == "0"
    assert payloads[evcc.TOPIC_STATUS] == evcc.STATUS_CONNECTED
    assert payloads[evcc.TOPIC_CHARGING] == "false"
    assert payloads[evcc.TOPIC_PLUGGED] == "true"


def test_numbers_are_rendered_for_people_as_well_as_for_evcc() -> None:
    payloads = evcc.bridge_payloads(
        snap(
            soc=86.0,
            range_km=378.6,
            odometer_km=10820.0,
            limit_soc=80.0,
            charging=True,
            charge_power_kw=11.0,
        )
    )
    assert payloads[evcc.TOPIC_SOC] == "86"
    # Distances are whole kilometres: the car's own display has no decimal and
    # neither does evcc's.
    assert payloads[evcc.TOPIC_RANGE] == "379"
    assert payloads[evcc.TOPIC_ODOMETER] == "10820"
    assert payloads[evcc.TOPIC_LIMIT_SOC] == "80"
    assert payloads[evcc.TOPIC_CHARGE_POWER] == "11"


def test_the_json_topic_carries_the_same_values_plus_an_epoch() -> None:
    """One topic for consumers that template rather than subscribe per field."""

    payloads = evcc.bridge_payloads(
        snap(
            soc=86.4,
            charging=True,
            plugged=True,
            range_km=379.0,
            limit_soc=80.0,
            updated=NOW,
        )
    )
    document = json.loads(payloads[evcc.TOPIC_STATE])
    assert document["vin"] == VIN
    assert document["soc"] == 86.4
    assert document["status"] == evcc.STATUS_CHARGING
    assert document["range"] == 379.0
    assert document["plugged"] is True
    assert document["updated"] == NOW.isoformat()
    # openWB's MQTT SoC module wants seconds since the epoch, not ISO 8601.
    assert document["updated_ts"] == int(NOW.timestamp())
    # Nothing the snapshot didn't know leaks into the document either.
    assert "odometer" not in document


def test_the_timestamp_is_normalised_to_utc() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=2)))
    payloads = evcc.bridge_payloads(snap(soc=50.0, updated=local))
    assert payloads[evcc.TOPIC_UPDATED] == NOW.isoformat()


def test_every_published_topic_is_one_the_bridge_admits_to_owning() -> None:
    """``ALL_TOPICS`` is what a switch-off clears, so it must be complete.

    A topic published but missing from that list would survive being switched
    off and keep a charge controller charging against a frozen value -- the one
    genuinely dangerous failure mode of pushing data out.
    """

    payloads = evcc.bridge_payloads(
        snap(
            soc=50.0,
            charging=True,
            plugged=True,
            range_km=200.0,
            odometer_km=1000.0,
            limit_soc=80.0,
            charge_power_kw=7.4,
            updated=NOW,
        )
    )
    assert set(payloads) == set(evcc.ALL_TOPICS)


def test_topics_are_laid_out_per_vehicle() -> None:
    topics = evcc.bridge_topics("evcc/bmw/", VIN, {evcc.TOPIC_SOC: "86.4"})
    assert topics == {f"evcc/bmw/{VIN}/soc": "86.4"}


# --------------------------------------------------------------------------
# The generated evcc config
# --------------------------------------------------------------------------


def test_the_generated_config_is_paste_ready() -> None:
    yaml = evcc.evcc_yaml(
        prefix="bavariandata",
        vin=VIN,
        title="BMW i5",
        capacity_kwh=81.2,
    )
    assert yaml.startswith("vehicles:\n")
    assert "  - name: bmw_000001\n" in yaml
    assert "    type: custom\n" in yaml
    assert '    title: "BMW i5"\n' in yaml
    assert "    capacity: 81.2\n" in yaml
    assert "      source: mqtt\n" in yaml
    assert f"      topic: bavariandata/{VIN}/soc\n" in yaml
    assert f"      topic: bavariandata/{VIN}/limitSoc\n" in yaml
    assert yaml.endswith("\n")


def test_the_generated_config_sets_no_timeout() -> None:
    """A parked car says nothing for days and its SoC is no less true for it.

    The bridge republishes on a heartbeat precisely so evcc need not be told to
    distrust a value that stops arriving. A ``timeout`` here would blank the
    state of charge of every car that is merely parked.
    """

    yaml = evcc.evcc_yaml(prefix="bavariandata", vin=VIN)
    assert "timeout" not in yaml


def test_the_generated_config_only_references_live_topics() -> None:
    """evcc logs a read error every cycle for a topic nothing publishes."""

    yaml = evcc.evcc_yaml(
        prefix="bavariandata",
        vin=VIN,
        topics=(evcc.TOPIC_SOC, evcc.TOPIC_STATUS),
    )
    assert "    soc:\n" in yaml
    assert "    status:\n" in yaml
    for absent in ("    range:", "    odometer:", "    limitSoc:"):
        assert absent not in yaml


def test_unknown_details_are_left_out_of_the_config() -> None:
    yaml = evcc.evcc_yaml(prefix="bavariandata", vin=VIN)
    assert "title:" not in yaml
    assert "capacity:" not in yaml


def test_the_vehicle_name_identifies_the_car_without_spelling_out_the_vin() -> None:
    """evcc keys its database on this name, so it must be stable and unique.

    The tail of a VIN is its serial: unique within anyone's garage, and short of
    writing the whole VIN into a config file people paste into forum threads.
    """

    assert evcc.evcc_vehicle_name(VIN) == "bmw_000001"
    assert VIN not in evcc.evcc_vehicle_name(VIN)
    assert evcc.evcc_vehicle_name("WBA-123 456") == "bmw_123456"
    assert evcc.evcc_vehicle_name("") == "bmw"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_the_bridge_is_off_until_it_is_switched_on() -> None:
    config = evcc.BridgeConfig.from_options({})
    assert config.enabled is False
    assert config.prefix == evcc.DEFAULT_PREFIX
    # Retain defaults on: it is what lets a charge controller find the SoC the
    # moment it starts instead of waiting for the car to speak again.
    assert config.retain is True


def test_settings_are_normalised_once_at_the_edge() -> None:
    config = evcc.BridgeConfig.from_options(
        {
            "bridge_enabled": True,
            "bridge_topic_prefix": "/evcc/bmw/",
            "bridge_retain": False,
        }
    )
    assert (config.enabled, config.prefix, config.retain) == (True, "evcc/bmw", False)


# --------------------------------------------------------------------------
# Inbound: the bound wallbox meter
# --------------------------------------------------------------------------


def test_a_meter_with_only_one_end_measures_nothing() -> None:
    assert sessions.measured_grid_kwh(None, 42.0) is None
    assert sessions.measured_grid_kwh(42.0, None) is None
    assert sessions.measured_grid_kwh(None, None) is None


@pytest.mark.parametrize("end", [100.0, 99.5, 0.0])
def test_a_meter_that_did_not_go_up_measures_nothing(end) -> None:
    """A reset, a restart at zero, or a meter that never counted this car.

    Never a charge that drew nothing: a session only exists because charging was
    reported in the first place.
    """

    assert sessions.measured_grid_kwh(100.0, end) is None


def test_a_plausible_delta_is_taken_as_measured() -> None:
    # 11.5 kWh from the grid against 10.4 into the pack: a 10 % charging loss,
    # which is what an ordinary AC charge looks like.
    assert sessions.measured_grid_kwh(1000.0, 1011.5, battery_kwh=10.4) == 11.5


def test_the_grid_cannot_have_delivered_less_than_the_pack_absorbed() -> None:
    """Not a tolerance -- a physical impossibility, so the reading is wrong.

    In practice it means the wrong entity was bound, or a meter that was not
    counting this car.
    """

    assert sessions.measured_grid_kwh(1000.0, 1002.0, battery_kwh=10.4) is None


def test_a_whole_house_meter_is_rejected_rather_than_believed() -> None:
    """The commonest misconfiguration by far, and the most expensive.

    Binding the house import meter instead of the wallbox's would inflate every
    cost in the ledger by whatever else the house was doing for those hours --
    silently, and with nothing about the number looking wrong.
    """

    assert sessions.measured_grid_kwh(1000.0, 1040.0, battery_kwh=10.4) is None


def test_a_tiny_top_up_is_not_cross_checked() -> None:
    """Below a fifth of a kilowatt-hour the ratio is the meter's resolution."""

    assert sessions.measured_grid_kwh(1000.0, 1000.4, battery_kwh=0.1) == 0.4


def test_the_cross_check_is_skipped_when_our_own_figure_is_a_floor() -> None:
    """Exactly the sessions where the meter is worth the most.

    A ``late_start`` or ``interrupted`` session's battery-side energy is known
    to be short, so comparing against it would reject the one measurement that
    saw the part we missed.
    """

    assert (
        sessions.measured_grid_kwh(
            1000.0, 1040.0, battery_kwh=10.4, cross_check=False
        )
        == 40.0
    )


def test_the_builder_keeps_the_first_reading_as_its_baseline() -> None:
    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_grid_meter(1000.0)
    builder.note_grid_meter(1005.0)
    builder.note_grid_meter(1011.0)
    assert builder.grid_meter_start == 1000.0
    assert builder.grid_meter_last == 1011.0
    assert builder.measured_grid_kwh(battery_kwh=10.4) == 11.0


def test_a_meter_reset_mid_session_drops_the_baseline() -> None:
    """A delta across a discontinuity is not a measurement of anything."""

    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_grid_meter(1000.0)
    builder.note_grid_meter(1005.0)
    builder.note_grid_meter(0.0)  # the wallbox was power-cycled
    assert builder.grid_meter_start is None
    assert builder.measured_grid_kwh() is None
    # ...and it starts measuring again from the new baseline.
    builder.note_grid_meter(2.0)
    builder.note_grid_meter(4.0)
    assert builder.measured_grid_kwh() == 2.0


def test_an_absent_reading_never_disturbs_the_baseline() -> None:
    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_grid_meter(1000.0)
    builder.note_grid_meter(None)  # the sensor went unavailable
    builder.note_grid_meter(1008.0)
    assert builder.measured_grid_kwh() == 8.0


def test_the_meter_survives_a_restart_mid_charge() -> None:
    """The baseline rides the open-session snapshot, or the charge loses it."""

    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_grid_meter(1000.0)
    builder.note_grid_meter(1004.0)

    resumed = sessions.SessionBuilder.from_dict(builder.to_dict())
    assert resumed is not None
    assert resumed.grid_meter_start == 1000.0
    resumed.note_grid_meter(1012.0)
    # The span covers the gap too: the meter kept counting while we were away,
    # and the record's total should say so.
    assert resumed.measured_grid_kwh() == 12.0


def test_a_snapshot_written_before_the_meter_existed_still_loads() -> None:
    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    legacy = builder.to_dict()
    del legacy["grid_meter_start"]
    del legacy["grid_meter_last"]
    resumed = sessions.SessionBuilder.from_dict(legacy)
    assert resumed is not None
    assert resumed.measured_grid_kwh() is None


def test_a_closed_session_carries_the_measured_grid_figure() -> None:
    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_soc(75.0)
    builder.note_grid_meter(1000.0)
    builder.note_grid_meter(1030.0)
    session = builder.close(
        NOW + timedelta(hours=3), soc_end=75.0, energy_kwh=27.5
    )
    assert session.grid_kwh == 30.0
    # Battery-side stays battery-side; the measured figure is a separate field,
    # and everything showing a number has to say which it is.
    assert session.energy_kwh == 27.5
    # Monthly totals and statistics sum the measured figure where there is one.
    assert session.effective_energy_kwh == 30.0


def test_a_session_with_no_meter_bound_behaves_exactly_as_before() -> None:
    builder = sessions.SessionBuilder(VIN, NOW, soc_start=40.0)
    builder.note_soc(75.0)
    session = builder.close(
        NOW + timedelta(hours=3), soc_end=75.0, energy_kwh=27.5
    )
    assert session.grid_kwh is None
    assert session.effective_energy_kwh == 27.5


def test_an_interrupted_session_keeps_the_meters_larger_figure() -> None:
    """The restart hole is the meter's whole reason for being here.

    Our own integration stopped for the duration; the wallbox did not. Cross-
    checking against the short figure would throw that away.
    """

    builder = sessions.SessionBuilder(VIN, NOW, soc_start=20.0)
    builder.note_grid_meter(1000.0)
    resumed = sessions.SessionBuilder.from_dict(builder.to_dict())
    assert resumed is not None and resumed.interrupted is True
    resumed.note_soc(80.0)
    resumed.note_grid_meter(1048.0)
    session = resumed.close(
        NOW + timedelta(hours=5), soc_end=80.0, energy_kwh=0.3
    )
    assert session.grid_kwh == 48.0
