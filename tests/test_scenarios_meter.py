"""The wallbox meter driving the SoC estimate, run through the real coordinator.

With a wallbox energy meter bound and enough home charges in the ledger, the
estimate moves by what the meter counted -- ``points per kWh`` read off those
charges (``history/meter_rate.py``) -- instead of extrapolating BMW's sparse
charging power. These scenarios pin when the meter may drive, and that every
way of losing it hands the estimate back to the stream without a jump.

Times are minutes after 08:00 UTC. The car is a 75 kWh pack; BMW reports
3.48 kW (4.64 %/h) while the wallbox really delivers 7.4 kW, which is the
gap the meter exists to close. The seeded charges gain 30 points for 24 kWh:
1.25 points per kWh, a 94 % efficient charge.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from .conftest import load_module
from .harness import START, CoordinatorHarness, store_mod

PricingConfig = load_module("history.pricing").PricingConfig

METER = "sensor.wallbox_energy"
STREAM_RATE = 3.48 / 75 * 100  # %/h, what BMW's power implies
PER_KWH = 30 / 24  # points per wallbox kWh in the seeded ledger
ORDERS = ["descriptors-first", "derived-first"]


def _seed(h: CoordinatorHarness, count: int = 3, *, location=None) -> None:
    """Home charges from the days before, each 30 -> 60 % for 24 kWh."""

    for day in range(1, count + 1):
        start = START - timedelta(days=day)
        h.coordinator.history.add_session(
            store_mod.ChargingSession(
                vin=h.vin,
                start=start,
                end=start + timedelta(hours=4),
                soc_start=30.0,
                soc_end=60.0,
                energy_kwh=22.0,
                grid_kwh=24.0,
                grid_source="meter",
                location=location,
            )
        )


def _charge(h: CoordinatorHarness, minute: float = 10, soc: float = 38) -> None:
    h.send(minute, target=100, step="target 100")
    h.meter(minute, 1000.0)
    h.send(minute, soc=soc, status="CHARGINGACTIVE", power=3480)


@pytest.fixture
def h():
    harness = CoordinatorHarness(wallbox_meter=METER)
    _seed(harness)
    yield harness
    harness.close()


def _source(h: CoordinatorHarness) -> str:
    return h.coordinator.soc_estimate_attributes(h.vin).get("estimate_source")


# --- the meter drives -------------------------------------------------------


def test_the_meter_drives_a_silent_charge(h, snapshot):
    """BMW says 3.48 kW and then nothing; the wallbox counted 14.8 kWh."""

    _charge(h)
    h.meter(70, 1007.4)
    h.meter(130, 1014.8)
    h.advance_to(140)

    assert _source(h) == "meter"
    assert h.estimate() == pytest.approx(38 + 14.8 * PER_KWH, abs=0.01)
    # The stream alone would have said 47.3.
    assert h.estimate() > 38 + STREAM_RATE * 130 / 60 + 8
    assert h.render() == snapshot


def test_the_charging_rate_follows_the_meter(h):
    """BMW says 4.64 %/h; the wallbox delivers 7.4 kW, which is 9.25 %/h."""

    _charge(h)
    assert h.rate() == pytest.approx(STREAM_RATE, abs=0.01)
    for minute in range(11, 40):
        h.meter(minute, 1000.0 + 7.4 * (minute - 10) / 60)
    h.advance_to(40)

    assert h.rate() == pytest.approx(7.4 * PER_KWH, abs=0.1)


def test_a_meter_reporting_once_an_hour_gives_its_hourly_rate(h):
    """Not one hour's energy packed into the few minutes after it arrived."""

    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(71)

    assert h.rate() == pytest.approx(7.4 * PER_KWH, abs=0.1)


def test_a_paused_charge_shows_no_rate(h):
    _charge(h)
    h.meter(20, 1001.0)
    h.advance_to(40)  # the wallbox paused: nothing flowed for twenty minutes

    assert h.rate() is None


def test_letting_go_of_the_meter_gives_the_stream_rate_back(h):
    _charge(h)
    for minute in range(11, 30):
        h.meter(minute, 1000.0 + 7.4 * (minute - 10) / 60)
    h.meter(30, None)
    h.advance_to(31)

    assert h.rate() == pytest.approx(STREAM_RATE, abs=0.01)


def test_too_few_learned_charges_leave_the_stream_driving():
    with CoordinatorHarness(wallbox_meter=METER) as h:
        _seed(h, count=2)
        _charge(h)
        h.meter(130, 1014.8)

        assert _source(h) == "stream"
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_without_a_meter_bound_nothing_changes():
    with CoordinatorHarness() as h:
        _seed(h)
        h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
        h.advance_to(130)

        assert h.coordinator.soc_estimate_attributes(h.vin) == {}
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_the_attributes_say_what_was_learned(h):
    _charge(h)
    h.meter(40, 1002.0)
    h.advance_to(41)

    attrs = h.coordinator.soc_estimate_attributes(h.vin)
    assert attrs["meter_sessions"] == 3
    assert attrs["meter_percent_per_kwh"] == pytest.approx(PER_KWH)
    # All three seeded charges ran at one power: no overhead can be told apart.
    assert attrs["meter_overhead_kw"] == 0.0
    assert attrs["meter_reading_kwh"] == 1002.0


def test_the_meter_stops_at_the_charge_target(h):
    h.send(10, target=50, step="target 50")
    h.meter(10, 1000.0)
    h.send(10, soc=45, status="CHARGINGACTIVE", power=3480)
    h.meter(100, 1010.0)  # 12.5 points' worth, past the target
    h.advance_to(101)

    assert h.estimate() == 50


# --- corrections ------------------------------------------------------------


def test_a_real_reading_restarts_the_count_from_the_meter(h):
    _charge(h)
    h.meter(130, 1014.8)
    h.advance_to(130.5)
    h.send(131, soc=55, status="CHARGINGACTIVE", step="app wakes the car")
    assert h.estimate() == 55

    h.meter(160, 1016.8)
    h.advance_to(161)
    assert h.estimate() == pytest.approx(55 + 2.0 * PER_KWH, abs=0.01)


def test_a_meter_reset_is_a_new_baseline_not_a_negative_charge(h):
    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(71)
    before = h.estimate()
    h.meter(80, 3.0)  # replaced or reset
    h.advance_to(81)
    assert h.estimate() == before

    h.meter(90, 4.0)
    h.advance_to(91)
    assert h.estimate() == pytest.approx(before + 1.0 * PER_KWH, abs=0.01)


def test_a_jump_no_wallbox_can_make_is_not_added(h):
    _charge(h)
    h.meter(20, 1500.0)  # 500 kWh in ten minutes
    h.advance_to(21)

    assert h.estimate() == 38


def test_an_unavailable_meter_hands_back_to_the_stream_without_a_jump(h, snapshot):
    _charge(h)
    h.meter(70, 1007.4)
    h.advance_to(70.5)
    at_handover = h.estimate()
    h.meter(70, None)
    h.advance_to(130)

    assert _source(h) == "stream"
    assert h.estimate() == pytest.approx(at_handover + STREAM_RATE * 59.5 / 60, abs=0.05)
    assert h.render() == snapshot


# --- when the meter may not drive -------------------------------------------


def test_a_charge_away_from_home_never_reads_the_meter():
    home = (48.1, 11.5)
    with CoordinatorHarness(wallbox_meter=METER, home=home) as h:
        h.hass.add_zone("Home", *home, radius_m=100)
        _seed(h)
        h.fix(5, 48.3, 11.9)  # somewhere else, inside no zone
        _charge(h)
        h.meter(130, 1014.8)  # someone else charging on the wallbox at home

        assert _source(h) == "stream"
        assert h.estimate() == pytest.approx(38 + STREAM_RATE * 120 / 60, abs=0.05)


def test_two_cars_charging_leave_the_meter_to_neither(h):
    _charge(h)
    other = {
        "vehicle.drivetrain.electricEngine.charging.status": {"value": "CHARGINGACTIVE"},
        "vehicle.drivetrain.batteryManagement.header": {"value": 50},
    }
    h._run(h.coordinator.async_handle_message({"vin": "WBATEST0000000002", "data": other}))
    h.meter(130, 1014.8)

    assert _source(h) == "stream"


def test_a_second_car_on_its_own_meter_leaves_this_one_on_its_meter(h):
    """Two wallboxes, a meter each: the other car's charge counts on its meter.

    Only a meter two charging cars share is a meter neither may claim.
    """

    h.coordinator.vehicle_pricing = {
        "WBATEST0000000002": PricingConfig(grid_energy_entity="sensor.wallbox_drive_energy")
    }
    _charge(h)
    other = {
        "vehicle.drivetrain.electricEngine.charging.status": {"value": "CHARGINGACTIVE"},
        "vehicle.drivetrain.batteryManagement.header": {"value": 50},
    }
    h._run(h.coordinator.async_handle_message({"vin": "WBATEST0000000002", "data": other}))
    h.meter(70, 1007.4)
    h.meter(130, 1014.8)
    h.advance_to(140)

    assert _source(h) == "meter"
    assert h.estimate() == pytest.approx(38 + 14.8 * PER_KWH, abs=0.01)


def test_a_car_with_its_own_meter_reads_that_one():
    """Not the account-wide meter, which belongs to the other wallbox."""

    harness = CoordinatorHarness(wallbox_meter="sensor.wallbox_drive_energy")
    try:
        harness.coordinator.pricing = PricingConfig(grid_energy_entity=METER)
        harness.coordinator.vehicle_pricing = {
            harness.vin: PricingConfig(grid_energy_entity="sensor.wallbox_drive_energy")
        }
        harness.hass.set_state(METER, "500.0", unit_of_measurement="kWh")
        _seed(harness)
        _charge(harness)
        harness.meter(70, 1007.4)
        harness.meter(130, 1014.8)
        harness.advance_to(140)

        assert _source(harness) == "meter"
        assert harness.estimate() == pytest.approx(38 + 14.8 * PER_KWH, abs=0.01)
    finally:
        harness.close()


def test_an_unplugged_car_never_follows_the_meter(h):
    """BMW forgot to end the charge; the cable is out, so the wallbox's kWh
    belong to whatever is plugged in now -- another car."""

    _charge(h)
    h.send(20, plug="DISCONNECTED", step="unplugged, status left on")
    h.meter(80, 1008.0)
    h.advance_to(81)

    assert _source(h) == "stream"


def test_a_plugged_car_follows_the_meter(h):
    _charge(h)
    h.send(11, plug="CONNECTED")
    h.meter(70, 1007.4)
    h.advance_to(71)

    assert _source(h) == "meter"


def test_a_charge_that_ends_lets_go_of_the_meter(h):
    _charge(h)
    h.meter(70, 1007.4)
    h.send(71, status="NOCHARGING", power=0, step="unplugged")
    frozen = h.estimate()
    h.meter(130, 1020.0)  # another car on the wallbox

    assert _source(h) == "stream"
    assert h.estimate() == frozen


# --- restarts ---------------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS)
def test_a_restart_adds_what_the_meter_counted_while_ha_was_down(order, snapshot):
    with CoordinatorHarness(wallbox_meter=METER) as h:
        _seed(h)
        _charge(h)
        h.meter(70, 1007.4)
        h.advance_to(70.5)
        # Half an hour down; the wallbox kept counting.
        h.hass.set_state(METER, 1011.1, unit_of_measurement="kWh")
        h.restart(downtime_s=1800, order=order)
        h.advance_to(101)

        assert _source(h) == "meter"
        assert h.estimate() == pytest.approx(38 + 11.1 * PER_KWH, abs=0.01)
        assert h.render() == snapshot


# --- learning, end to end ---------------------------------------------------


def test_three_metered_home_charges_teach_the_fourth():
    """No seeded ledger: the rate comes from charges this harness ran."""

    with CoordinatorHarness(wallbox_meter=METER) as h:
        meter = 1000.0
        minute = 10.0
        for _ in range(3):
            h.meter(minute, meter)
            h.send(minute, soc=30, status="CHARGINGACTIVE", power=7400)
            meter += 24.0
            h.meter(minute + 180, meter)
            h.send(minute + 181, soc=60, status="CHARGINGACTIVE", power=7400)
            h.send(minute + 182, status="NOCHARGING", power=0, step="done")
            minute += 400
        h.advance_to(minute)
        assert h.coordinator.learned_meter_rate(h.vin).samples == 3

        h.meter(minute, meter)
        h.send(minute, soc=40, status="CHARGINGACTIVE", power=3480)
        h.meter(minute + 60, meter + 8.0)
        h.advance_to(minute + 61)

        assert _source(h) == "meter"
        assert h.estimate() == pytest.approx(40 + 8.0 * PER_KWH, abs=0.5)


# --- two cars on one meter: billing ------------------------------------------


OTHER = "WBATEST0000000002"


def _other_charging(h: CoordinatorHarness, vin: str = OTHER) -> None:
    other = {
        "vehicle.drivetrain.electricEngine.charging.status": {"value": "CHARGINGACTIVE"},
        "vehicle.drivetrain.batteryManagement.header": {"value": 50},
    }
    h._run(h.coordinator.async_handle_message({"vin": vin, "data": other}))


def _wallbox_counts(h: CoordinatorHarness) -> None:
    """Two hours at BMW's 3.48 kW, the wallbox counting 3.7 kW (94 %)."""

    for minute in range(20, 140, 10):
        h.meter(minute, 1000.0 + 3.7 * (minute - 10) / 60)
        h.send(minute, power=3480, soc=round(38 + STREAM_RATE * (minute - 10) / 60, 1))


def _stop(h: CoordinatorHarness, minute: float) -> None:
    h.send(minute, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(minute + 5)


def test_one_car_on_the_meter_is_billed_from_it(h):
    """The baseline the two-car case below is measured against."""

    _charge(h)
    _wallbox_counts(h)
    _stop(h, 140)

    session = max(h.sessions(), key=lambda record: record.start)
    assert session.grid_source == "meter"
    assert session.grid_kwh == pytest.approx(3.7 * 2, abs=0.01)


def test_two_cars_charging_on_one_meter_bill_neither_from_it(h):
    """Both cars used to bill the whole advance: 7.4 kWh counted twice.

    Nothing the meter reports says which car it charged, so while both
    charge, each falls back to its battery-side figure, and the session keeps
    no meter verdict (a baseline taken after the other car stopped would cover
    only part of it).
    """

    _charge(h)
    _other_charging(h)
    _wallbox_counts(h)
    _stop(h, 140)

    session = max(h.sessions(), key=lambda record: record.start)
    assert session.grid_source != "meter"
    assert session.grid_kwh is None


def test_a_car_of_another_account_on_the_same_meter_counts_too(h):
    """A BMW and a MINI on two accounts, one wallbox: each coordinator has to
    see the other's charge, not only its own cars."""

    import types

    with CoordinatorHarness(vin=OTHER, wallbox_meter=METER) as mini:
        mini.send(10, soc=50, status="CHARGINGACTIVE", power=3480)
        entries = [
            types.SimpleNamespace(
                entry_id=f"entry_{n}", title=n, runtime_data=types.SimpleNamespace(coordinator=c)
            )
            for n, c in (("bmw", h.coordinator), ("mini", mini.coordinator))
        ]
        h.hass.config_entries = types.SimpleNamespace(
            async_entries=lambda _domain: entries, async_get_entry=lambda _id: None
        )
        _charge(h)
        _wallbox_counts(h)

        assert _source(h) == "stream"
        _stop(h, 140)
        session = max(h.sessions(), key=lambda record: record.start)
        assert session.grid_kwh is None


def test_a_restored_charge_no_one_confirms_stops_holding_the_meter(h):
    """A car charging at the restart counts as on the meter only while its
    restored charge is still open; once that is filed unconfirmed it no longer
    keeps another account's car off the meter for hours."""

    import types

    _charge(h)
    h.advance_to(27.7)
    h.restart(downtime_s=80)
    h.advance_to(60)  # past the restored session's grace: filed as "restart"
    assert max(h.sessions(), key=lambda record: record.start).end_reason == "restart"

    with CoordinatorHarness(vin=OTHER, wallbox_meter=METER) as mini:
        mini.hass.config_entries = types.SimpleNamespace(
            async_entries=lambda _domain: [
                types.SimpleNamespace(runtime_data=types.SimpleNamespace(coordinator=c))
                for c in (h.coordinator, mini.coordinator)
            ],
            async_get_entry=lambda _id: None,
        )
        _charge(mini, minute=60, soc=50)
        for minute in range(70, 190, 10):
            mini.meter(minute, 1000.0 + 3.7 * (minute - 60) / 60)
            mini.send(minute, power=3480, soc=round(50 + STREAM_RATE * (minute - 60) / 60, 1))
        _stop(mini, 190)

        session = max(mini.sessions(), key=lambda record: record.start)
        assert session.grid_source == "meter"


# --- the meter bounds the session's energy -----------------------------------


def _metered_charge(h: CoordinatorHarness, *, until: float, kw: float = 7.4) -> None:
    """BMW reports the wallbox's real power once; the meter follows to ``until``."""

    h.send(10, target=100, step="target 100")
    h.meter(10, 1000.0)
    h.send(10, soc=38, status="CHARGINGACTIVE", power=kw * 1000)
    for minute in range(11, int(until) + 1):
        h.meter(minute, 1000.0 + kw * (minute - 10) / 60)


def _today(h: CoordinatorHarness):
    """The charge under test, not the seeded ones before it."""

    (session,) = [s for s in h.sessions() if s.start >= START]
    return session


def test_the_meter_lifts_a_charge_the_soc_never_followed(h):
    """No SoC until the end: the SoC ceiling held this at 1.5 kWh; the meter counted 14.8."""

    _metered_charge(h, until=130)
    h.send(131, status="NOCHARGING", power=0, step="ended, no SoC")
    h.advance_to(135)

    session = _today(h)
    assert session.grid_kwh == pytest.approx(14.8, abs=0.01)
    assert session.energy_kwh == pytest.approx(14.8, abs=0.15)


def test_the_meter_trims_power_held_through_an_outage(h):
    """The live i5 on 21 Oct: the wallbox lost power at minute 40, the car said so at 70.

    Integrated at the last reported 7.4 kW, the hour claims 7.4 kWh; the SoC
    ceiling allowed 4.5 of it. The meter counted 3.7, and that is the charge.
    """

    _metered_charge(h, until=40)
    h.advance_to(70)
    h.send(70, soc=42, status="CHARGINGERROR", power=0, step="power back, car reports")
    h.advance_to(75)

    session = _today(h)
    assert session.grid_kwh == pytest.approx(3.7, abs=0.01)
    assert session.energy_kwh == pytest.approx(3.7, abs=0.01)


def test_a_meter_that_never_moves_leaves_the_soc_ceiling_in_charge(h):
    """A meter bound by mistake, or one that stopped counting, can't zero a charge."""

    h.send(10, target=100, step="target 100")
    h.meter(10, 1000.0)
    h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
    h.send(190, soc=52, status="FINISHEDFULLYCHARGED", power=0, step="woke, finished")
    h.advance_to(195)

    session = _today(h)
    assert session.energy_kwh == pytest.approx(3.48 * 3, abs=0.05)


def test_a_meter_counting_less_than_the_soc_proves_is_not_believed(h):
    """0.5 kWh against a 14-point rise: that meter measures something else."""

    h.send(10, target=100, step="target 100")
    h.meter(10, 1000.0)
    h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
    h.meter(100, 1000.5)
    h.send(190, soc=52, status="FINISHEDFULLYCHARGED", power=0, step="woke, finished")
    h.advance_to(195)

    session = _today(h)
    assert session.energy_kwh == pytest.approx(3.48 * 3, abs=0.05)
