"""Charging, the SoC estimate and restarts, run through the real coordinator.

Each scenario states the behaviour it is about in plain assertions, and pins
its whole timeline to a snapshot: the assertions say what matters, the snapshot
catches everything else that moves. A change that alters a timeline fails with
a readable diff -- approve an intended one with
``python -m pytest tests/test_scenarios_charging.py --snapshot-update`` and read
the diff, as with the card snapshots.

Times are minutes after 08:00 UTC. The car is a 75 kWh pack charging at
3.48 kW, which is the 4.64 %/h the maintainer's i5 showed on 26 September 2026.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness, store_mod

RATE = 3.48 / 75 * 100  # %/h
ORDERS = ["descriptors-first", "derived-first"]


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def _start_charge(h: CoordinatorHarness, minute: float = 10, soc: float = 38) -> None:
    h.send(minute, target=100, step="target 100")
    h.send(minute, soc=soc, status="CHARGINGACTIVE", power=3480)


# --- the estimate -----------------------------------------------------------


def test_a_silent_charge_keeps_the_estimate_climbing(h, snapshot):
    """BMW went silent for two hours mid-charge; the estimate is the live SoC."""

    _start_charge(h)
    h.advance_to(142)

    expected = 38 + RATE * (142 - 10) / 60
    assert h.estimate() == pytest.approx(expected, abs=0.05)
    assert h.render() == snapshot


def test_a_real_reading_replaces_the_estimate(h, snapshot):
    _start_charge(h)
    h.advance_to(142)
    h.send(142.4, soc=47, status="CHARGINGACTIVE", power=3520, step="app wakes the car")

    assert h.estimate() == 47
    assert h.render() == snapshot


def test_the_estimate_stops_at_the_charge_target(h, snapshot):
    h.send(10, target=50, step="target 50")
    h.send(10, soc=45, status="CHARGINGACTIVE", power=3480)
    h.advance_to(300)

    assert h.estimate() == 50
    assert h.render() == snapshot


def test_the_estimate_never_passes_full(h):
    h.send(10, soc=97, status="CHARGINGACTIVE", power=11000)
    h.advance_to(300)

    assert h.estimate() == 100


def test_without_a_capacity_there_is_no_rate_to_extrapolate(snapshot):
    with CoordinatorHarness(capacity_kwh=None) as h:
        h.send(10, soc=38, status="CHARGINGACTIVE", power=3480)
        h.advance_to(100)

        assert h.rate() is None
        assert h.estimate() == 38
        assert h.render() == snapshot


def test_a_stopped_charge_freezes_the_estimate(h):
    _start_charge(h)
    h.send(40, status="NOCHARGING", power=0, step="unplugged")
    frozen = h.estimate()
    h.advance_to(200)

    assert h.estimate() == frozen
    assert h.rate() is None


# --- sessions -----------------------------------------------------------------


def test_an_unplug_files_the_session_after_the_debounce(h, snapshot):
    _start_charge(h)
    h.send(40, soc=40, status="CHARGINGACTIVE", power=3480)
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    assert h.has_open_session()  # the flap debounce holds it open...
    h.advance_to(63)

    sessions = h.sessions()
    assert len(sessions) == 1  # ...and then it is filed
    assert sessions[0].end_reason == "NOCHARGING"
    assert [name for name, _ in h.events()] == [
        "bavariandata_charging_started",
        "bavariandata_charging_stopped",
    ]
    assert h.render() == snapshot


def test_every_event_names_the_car(h):
    """With two cars, "Charging complete" alone doesn't say which one."""

    h.coordinator.names[h.vin] = "i5 eDrive40"
    _start_charge(h)
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(63)

    assert [data["vehicle"] for _name, data in h.events()] == ["i5 eDrive40"] * 2


def test_an_unnamed_car_is_never_named_by_its_vin(h):
    h.coordinator.names[h.vin] = h.vin  # what a car without basic data is called
    _start_charge(h)

    assert [data["vehicle"] for _name, data in h.events()] == [None]


def test_a_status_flap_inside_the_debounce_is_one_session(h, snapshot):
    _start_charge(h)
    h.send(30, status="NOCHARGING", step="flap off")
    h.send(31, status="CHARGINGACTIVE", step="flap on")
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(65)

    assert len(h.sessions()) == 1
    assert h.render() == snapshot


def test_the_same_batch_twice_changes_nothing(h):
    _start_charge(h)
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480, stamped=20)
    before = (h.estimate(), h.rate(), list(h.hass.events))
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480, stamped=20, step="duplicate")

    assert (h.estimate(), h.rate(), list(h.hass.events)) == before


def test_the_started_event_knows_what_arrived_alongside_the_status(h):
    """Found by this harness: the event fired mid-batch and missed the target.

    ``charging.status`` used to open the session and fire the event while the
    batch was still being read, so a target -- or the SoC -- later in the same
    batch was not known yet. Both are acted on once the batch is read now.
    """

    h.send(10, status="CHARGINGACTIVE", power=3480, target=80, soc=38)

    (_name, data), *_ = h.events()
    assert data["target_soc"] == 80
    assert data["soc"] == 38


# --- restarts -----------------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS)
def test_a_restart_mid_charge_keeps_the_estimate_climbing(order, snapshot):
    """The 0.9.12 fix, end to end: entities restore in either order."""

    with CoordinatorHarness() as h:
        _start_charge(h)
        h.advance_to(27.7)
        before = h.estimate()
        h.restart(downtime_s=80, order=order)
        h.advance_to(60)

        assert h.estimate() > before + 1.5
        assert h.estimate() == pytest.approx(38 + RATE * 50 / 60, abs=0.1)
        assert h.render() == snapshot


def test_a_restart_mid_charge_that_the_catch_up_confirms_stays_one_session(h, snapshot):
    """What happened live on 0.9.13-beta.2: the REST answer resumes the charge."""

    _start_charge(h)
    h.send(12.8, power=3480)
    h.advance_to(27.7)
    h.restart(downtime_s=80)
    # Two minutes later the catch-up returns BMW's cached last message.
    h.send(31, stamped=12.8, soc=38, status="CHARGINGACTIVE", power=3480, step="catch-up")
    h.advance_to(120)
    h.send(120, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(125)

    sessions = h.sessions()
    assert len(sessions) == 1
    assert sessions[0].end_reason == "NOCHARGING"
    assert "bavariandata_charging_stopped" in [name for name, _ in h.events()][-1:]
    assert h.render() == snapshot


def test_a_charge_that_ended_during_the_restart_is_filed_where_it_was_last_seen(h, snapshot):
    _start_charge(h)
    h.send(20, soc=39, status="CHARGINGACTIVE", power=3480)
    h.advance_to(27.7)
    h.restart(downtime_s=600)
    h.send(40, stamped=33, status="NOCHARGING", power=0, step="catch-up: it ended")

    sessions = h.sessions()
    assert len(sessions) == 1
    assert sessions[0].end <= h.at(29)  # not stretched to when we found out
    assert h.render() == snapshot


def test_a_charge_that_reached_its_target_during_the_restart_is_complete(h):
    """Both stop paths judge "complete" by one rule and say so on the event.

    The blueprint used to judge it itself and called a charge complete that
    the integration, closing it after a restart, never fired complete for.
    """

    h.send(10, target=80, step="target 80")
    h.send(10, soc=78, status="CHARGINGACTIVE", power=3480)
    h.advance_to(27.7)
    h.restart(downtime_s=600)
    h.send(40, stamped=33, soc=80, status="CHARGINGENDED", power=0, step="catch-up: done")
    h.advance_to(45)

    events = h.events()
    assert [name for name, _ in events][-2:] == [
        "bavariandata_charging_stopped",
        "bavariandata_charging_complete",
    ]
    assert events[-2][1]["complete"] is True


def test_a_charge_stopped_short_of_its_target_is_not_complete(h):
    _start_charge(h)
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(63)

    stopped = [data for name, data in h.events() if name.endswith("_stopped")]
    assert stopped and stopped[0]["complete"] is False


def test_a_restart_the_stream_never_answers_splits_the_charge(h, snapshot):
    """26 September before the catch-up existed: filed as ended, then reopened."""

    _start_charge(h)
    h.advance_to(27.7)
    h.restart(downtime_s=80)
    h.advance_to(142)
    h.send(142.4, soc=47, status="CHARGINGACTIVE", power=3520, step="app wakes the car")

    sessions = h.sessions()
    assert [s.end_reason for s in sessions] == ["restart"]
    assert h.has_open_session()  # the rest of the charge is a new session
    assert h.render() == snapshot


def test_a_restart_loop_mid_charge_never_duplicates_the_session(h):
    _start_charge(h)
    for minute in (20, 22, 24, 26):
        h.advance_to(minute)
        h.restart(downtime_s=30)
    h.send(30, stamped=10, soc=38, status="CHARGINGACTIVE", power=3480, step="catch-up")
    h.send(90, status="NOCHARGING", power=0)
    h.advance_to(95)

    assert len(h.sessions()) == 1


def test_a_restart_while_parked_changes_nothing(h):
    h.send(10, soc=60, status="NOCHARGING", power=0)
    h.restart(downtime_s=3600)
    h.advance_to(300)

    assert h.estimate() == 60
    assert h.rate() is None
    assert h.sessions() == []


def test_without_history_a_restart_does_not_extrapolate():
    """No restored session to vouch for the charge, so the estimate waits."""

    with CoordinatorHarness(history=False) as h:
        _start_charge(h)
        h.advance_to(27.7)
        held = h.estimate()
        h.restart(downtime_s=80)
        h.advance_to(100)

        assert h.estimate() == pytest.approx(held, abs=0.01)


# --- efficiency -----------------------------------------------------------------

FUEL_LEVEL = "vehicle.drivetrain.fuelSystem.level"


def _two_charges_around_a_drive(h: CoordinatorHarness, *, fuel: bool) -> None:
    """Charge, drive 100 km, charge back to where it was.

    The charging ledger alone then brackets 100 km of driving -- enough for the
    efficiency balance -- with ~11 kWh put back into the pack. ``fuel`` adds the
    one thing that makes the car a plug-in hybrid: its tank reporting.
    """

    extra = {FUEL_LEVEL: 55} if fuel else None
    h.send(0, odometer=10000, soc=40, status="NOCHARGING", extra=extra, step="parked")
    _charge(h, 10, 190, soc_from=40, soc_to=80)
    h.send(300, odometer=10100, soc=25, step="back from 100 km")
    _charge(h, 400, 580, soc_from=25, soc_to=80)


def _charge(h: CoordinatorHarness, start: int, end: int, *, soc_from: float, soc_to: float) -> None:
    """A charge that reports power and SoC every ten minutes, as a car does.

    Both are needed: the integrated energy is held to what the SoC says the
    pack has absorbed so far (``_session_energy_ceiling_wh``).
    """

    h.send(start, soc=soc_from, status="CHARGINGACTIVE", power=3700, step=f"plug in {start}")
    for minute in range(start + 10, end, 10):
        soc = round(soc_from + (soc_to - soc_from) * (minute - start) / (end - start))
        h.send(minute, soc=soc, power=3700, step=f"charging {minute}")
    h.send(end, soc=soc_to, status="NOCHARGING", power=0, step=f"charge done {end}")
    h.advance_to(end + 5)


def test_the_ledger_measures_an_electric_cars_consumption():
    """The control for the hybrid case below: same charges, a real figure."""

    with CoordinatorHarness(capacity_kwh=19.7) as h:
        _two_charges_around_a_drive(h, fuel=False)
        profile = h.coordinator.efficiency(h.vin)

        assert len(h.sessions()) == 2
        assert profile["status"] == "ok"
        assert profile["consumption"]["kwh_per_100km"] > 5
        assert profile["range"]["full_km"] is not None


def test_a_plug_in_hybrid_gets_no_consumption_or_range(snapshot):
    """Issue #25: the odometer also counts what the engine drove.

    The same charges on a car whose tank reports: dividing them by the odometer
    would quote a frugal EV and a real range far past what the battery reaches,
    so the profile withholds both and says why -- and keeps the capacity, which
    describes the pack alone.
    """

    with CoordinatorHarness(capacity_kwh=19.7) as h:
        _two_charges_around_a_drive(h, fuel=True)
        profile = h.coordinator.efficiency(h.vin)

        assert len(h.sessions()) == 2
        assert profile["status"] == "plug_in_hybrid"
        assert profile["consumption"] is None
        assert profile["range"] is None
        assert profile["trend"] == []
        assert profile["capacity_kwh"] == 19.7
        assert h.render() == snapshot


# --- the house's sensors and tariff -------------------------------------------


def _house_harness(home: tuple[float, float]) -> CoordinatorHarness:
    pricing = load_module("history.pricing")
    harness = CoordinatorHarness(home=home)
    harness.hass.add_zone("Home", *home, radius_m=100)
    harness.coordinator.pricing = pricing.PricingConfig(
        mode=pricing.MODE_FIXED,
        fixed_price=0.30,
        pv_power_entity="sensor.pv",
        grid_power_entity="sensor.grid",
    )
    # Sunshine at home: 4 kW off the roof, 2 kW imported.
    harness.hass.set_state("sensor.pv", 4000, unit_of_measurement="W")
    harness.hass.set_state("sensor.grid", 2000, unit_of_measurement="W")
    return harness


def _charge_and_unplug(h: CoordinatorHarness) -> None:
    _start_charge(h)
    h.send(70, soc=42, status="CHARGINGACTIVE", power=3480)
    h.send(130, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(133)


def test_a_charge_elsewhere_gets_no_house_mix_and_no_home_tariff():
    """#97: at a public charger the house's meters say nothing about the car."""

    with _house_harness((48.1, 11.5)) as h:
        h.fix(5, 48.3, 11.9)  # a public charger, inside no zone
        _charge_and_unplug(h)

        (session,) = h.sessions()
        assert session.location == {"zone": None}
        assert session.energy_kwh  # the energy itself still counts
        assert session.energy_mix is None
        assert session.cost is None


def test_a_charge_at_home_keeps_the_house_mix_and_tariff():
    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.fix(5, *home)
        _charge_and_unplug(h)

        (session,) = h.sessions()
        assert session.location == {"zone": "Home"}
        assert session.energy_mix["solar_percent"] > 0
        assert session.cost["source"] == "tariff"


def test_a_night_charge_keeps_its_mix_when_the_inverter_sleeps():
    """The maintainer's i5, 2026-10-22: every night charge filed with no mix.

    The inverter drops off at dusk and its power sensor reads ``unknown`` until
    morning, so each increment was booked as unknown and the month's solar share
    counted only the daytime charges (31.6 % instead of about 14 %). With the
    sun down the roof makes nothing: the charge is all grid.
    """

    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.hass.set_state("sun.sun", "below_horizon")
        h.hass.set_state("sensor.pv", "unknown", unit_of_measurement="W")
        h.fix(5, *home)
        _charge_and_unplug(h)

        (session,) = h.sessions()
        assert session.energy_mix == {"grid": session.energy_kwh, "solar_percent": 0.0}
        assert session.cost["source"] == "tariff"


def test_a_daytime_gap_in_the_pv_reading_stays_unknown():
    """Only the dark excuses a missing PV reading: by day it could be anything."""

    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.hass.set_state("sun.sun", "above_horizon")
        h.hass.set_state("sensor.pv", "unavailable", unit_of_measurement="W")
        h.fix(5, *home)
        _charge_and_unplug(h)

        (session,) = h.sessions()
        assert session.energy_mix is None


def _asleep_through_the_charge(h: CoordinatorHarness, *, sunrise: float, restart_at=None) -> None:
    """#180's MINI: SoC at the start, then nothing until the batch that ends it.

    Grid-only until ``sunrise``, the roof alone after it.
    """

    h.hass.set_state("sensor.pv", 0, unit_of_measurement="W")
    h.hass.set_state("sensor.grid", 3600, unit_of_measurement="W")
    _start_charge(h)
    if restart_at is not None:
        h.advance_to(restart_at)
        pricing = h.coordinator.pricing
        h.restart(downtime_s=60)
        h.coordinator.pricing = pricing
        # The car is still asleep: the stream's first word is BMW's cached one.
        h.send(stamped=10, soc=38, status="CHARGINGACTIVE", power=3480, step="catch-up")
    h.advance_to(sunrise)
    h.hass.set_state("sensor.pv", 4000, unit_of_measurement="W")
    h.hass.set_state("sensor.grid", 0, unit_of_measurement="W")
    h.send(190, soc=52, status="FINISHEDFULLYCHARGED", power=0, step="woke, finished")
    h.advance_to(195)


def test_a_car_asleep_through_its_charge_files_the_whole_charge(h, snapshot):
    """#180: the final SoC rides in with the end of the charge, and still counts.

    Filed at the ceiling's bare margin before -- 2 % of the pack, 1.5 kWh here,
    0.6 kWh on the reporter's MINI -- because the batch that raised the ceiling
    was also the one that stopped the integration.
    """

    _start_charge(h)
    h.send(190, soc=52, status="FINISHEDFULLYCHARGED", power=0, step="woke, finished")
    h.advance_to(195)

    (session,) = h.sessions()
    assert (session.soc_start, session.soc_end) == (38, 52)
    assert session.energy_kwh == pytest.approx(3.48 * 3, abs=0.05)
    assert h.coordinator.get_lifetime_energy_kwh(h.vin) == pytest.approx(session.energy_kwh)
    assert h.render() == snapshot


def test_energy_released_at_the_end_keeps_the_mix_it_came_at():
    """The night's kWh stay grid when the release comes after sunrise (#180)."""

    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.fix(5, *home)
        _asleep_through_the_charge(h, sunrise=180)

        (session,) = h.sessions()
        mix = session.energy_mix
        assert session.energy_kwh == pytest.approx(3.48 * 3, abs=0.05)
        assert mix["grid"] + mix.get("pv", 0) == pytest.approx(session.energy_kwh, abs=0.05)
        assert mix["solar_percent"] == pytest.approx(10 / 180 * 100, abs=1)
        # No solar price set, so the roof is billed at the import price too.
        assert session.cost["amount"] == pytest.approx(0.30 * session.energy_kwh, abs=0.02)


def test_held_energy_survives_a_restart():
    """What the ceiling held before a restart is still released with its mix."""

    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.fix(5, *home)
        _asleep_through_the_charge(h, sunrise=180, restart_at=100)

        (session,) = h.sessions()
        mix = session.energy_mix
        assert session.energy_kwh > 10  # three hours at 3.48 kW, less the downtime
        assert mix["grid"] + mix.get("pv", 0) == pytest.approx(session.energy_kwh, abs=0.05)
        assert mix["solar_percent"] < 10


def test_a_remembered_charge_keeps_its_point():
    """*Remember charging locations* on: the charge keeps where it was made."""

    with _house_harness((48.1, 11.5)) as h:
        h.coordinator.history.remember_locations = True
        h.fix(5, 48.3, 11.9)
        _charge_and_unplug(h)

        (session,) = h.sessions()
        assert session.location == {"zone": None, "lat": 48.3, "lon": 11.9}


def test_turning_remembering_off_takes_the_points_with_it():
    """Off means gone: stored and still-open charges both lose their coordinates."""

    with _house_harness((48.1, 11.5)) as h:
        h.coordinator.history.remember_locations = True
        h.fix(5, 48.3, 11.9)
        _charge_and_unplug(h)
        h.send(140, soc=60, status="CHARGINGACTIVE", power=3480, step="second charge")
        assert h.coordinator.history.open_sessions()[h.vin]["location"]["lat"] == 48.3

        h.coordinator.history.remember_locations = False
        assert h.coordinator.history.forget_locations() == 2

        (session,) = h.sessions()
        assert session.location == {"zone": None}
        assert "lat" not in h.coordinator.history.open_sessions()[h.vin]["location"]


def test_a_connected_dc_socket_marks_the_charge_dc():
    """The car's own plug descriptor beats the peak-power guess."""

    with _house_harness((48.1, 11.5)) as h:
        h.fix(5, 48.3, 11.9)
        _start_charge(h)
        h.send(20, step="dc socket", extra={"vehicle.body.chargingPort.dcStatus": "CONNECTED"})
        h.send(70, soc=42, status="CHARGINGACTIVE", power=3480)
        h.send(130, status="NOCHARGING", power=0, step="unplugged")
        h.advance_to(133)

        (session,) = h.sessions()
        assert session.charge_method == "dc"


def test_the_evcc_bridge_knows_a_charge_elsewhere():
    """The bridge withholds evcc's status from a car charging away (#97 follow-up)."""

    home = (48.1, 11.5)
    with _house_harness(home) as h:
        h.fix(5, 48.3, 11.9)
        _start_charge(h)
        assert h.coordinator.bridge_snapshot(h.vin).away is True
        h.send(70, status="NOCHARGING", power=0, step="unplugged")
        h.advance_to(73)
        h.fix(90, *home)  # driven home
        assert h.coordinator.bridge_snapshot(h.vin).away is False


def test_a_deleted_car_leaves_the_other_cars_history_alone(h):
    """Deleting a sold car's device empties its records, and only its."""

    _start_charge(h)
    h.send(60, status="NOCHARGING", power=0, step="unplugged")
    h.advance_to(63)
    store = h.coordinator.history
    other = store_mod.ChargingSession(
        vin="WBATEST0000000002", start=h.clock.now, end=h.clock.now, soc_start=40.0, soc_end=50.0
    )
    store.add_session(other)
    store.forget_vehicle(h.vin)
    h._run(store.async_save_now())
    h.restart()

    assert h.sessions() == []
    assert [s.vin for s in h.coordinator.history.sessions("WBATEST0000000002")] == [
        "WBATEST0000000002"
    ]
