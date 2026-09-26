"""The SoC estimate across a Home Assistant restart mid-charge.

Replays what the maintainer's i5 did on 2026-09-26: charging from 38 % at
08:10, the stream silent from 08:12 until someone opened the app at 10:22, and
HA restarted at 08:29 in between. The estimate climbed correctly until the
restart and then sat at 39.15 % for two hours, because the restored status came
back as the sensor's lowercase slug ``chargingactive`` and was matched against
BMW's ALL_CAPS token. BMW's reading at 10:22 was 47 %.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import load_module

soc = load_module("soc_tracking")
SocTracking = soc.SocTracking

POWER_W = 3480.0
MAX_ENERGY_KWH = 75.0  # 3.48 kW into 75 kWh is the 4.64 %/h the i5 showed
RATE = POWER_W / 1000.0 / MAX_ENERGY_KWH * 100.0

READING_AT = datetime(2026, 9, 26, 8, 12, 46, tzinfo=timezone.utc)
CHARGE_START = datetime(2026, 9, 26, 8, 9, 58, tzinfo=timezone.utc)
ESTIMATE_AT = datetime(2026, 9, 26, 8, 27, 40, tzinfo=timezone.utc)
APP_OPENED = datetime(2026, 9, 26, 10, 22, 23, tzinfo=timezone.utc)


def _restored(
    order: tuple[str, ...], *, since=CHARGE_START, estimate: float = 39.15
) -> SocTracking:
    """A tracker rebuilt the way entity restore rebuilds it, in ``order``."""

    tracking = SocTracking()
    steps = {
        "energy": lambda: tracking.update_max_energy(MAX_ENERGY_KWH),
        "power": lambda: tracking.update_power(POWER_W, READING_AT),
        "reading": lambda: tracking.update_actual_soc(38.0, READING_AT, restored=True),
        "estimate": lambda: tracking.adopt_estimate(estimate, ESTIMATE_AT),
        "status": lambda: tracking.restore_status("chargingactive", since),
    }
    for step in order:
        steps[step]()
    return tracking


def test_restored_lowercase_status_counts_as_charging() -> None:
    assert soc.is_charging_status("chargingactive")
    assert soc.is_charging_status("CHARGINGACTIVE")
    assert soc.is_charging_status(" charging_in_progress ")
    assert not soc.is_charging_status("nocharging")
    assert not soc.is_charging_status(None)


def test_estimate_keeps_climbing_across_a_restart() -> None:
    tracking = _restored(("energy", "power", "reading", "estimate", "status"))
    value = tracking.estimate(APP_OPENED)
    expected = 39.15 + RATE * (APP_OPENED - ESTIMATE_AT).total_seconds() / 3600.0
    assert abs(value - expected) < 0.01
    # BMW said 47 when the car woke; the frozen estimate said 39.15.
    assert 46.0 < value < 49.0


def test_restore_order_does_not_decide_the_anchor() -> None:
    """The newer of reading and estimate wins, whichever restored first.

    41 % is deliberately off the straight line from the 38 % reading: a real
    charge's power varies, so the restored estimate carries progress a re-run
    from the older reading at today's rate would not reproduce.
    """

    orders = [
        ("energy", "power", "reading", "estimate", "status"),
        ("estimate", "status", "reading", "energy", "power"),
        ("status", "energy", "estimate", "power", "reading"),
    ]
    expected = 41.0 + RATE * (APP_OPENED - ESTIMATE_AT).total_seconds() / 3600.0
    for order in orders:
        value = _restored(order, estimate=41.0).estimate(APP_OPENED)
        assert abs(value - expected) < 0.01, order


def test_a_newer_reading_beats_an_older_restored_estimate() -> None:
    tracking = SocTracking()
    tracking.adopt_estimate(39.15, ESTIMATE_AT)
    later = ESTIMATE_AT + timedelta(minutes=5)
    tracking.update_actual_soc(41.0, later, restored=True)
    assert tracking.estimated_percent == 41.0
    assert tracking.adopt_estimate(40.0, ESTIMATE_AT) is False


def test_no_extrapolation_without_a_restored_charge() -> None:
    """A restored status alone may be hours stale; it must not drive the SoC."""

    tracking = _restored(("energy", "power", "reading", "status"), since=None)
    assert tracking.estimate(APP_OPENED) == 38.0
    assert tracking.current_rate_per_hour() is None


def test_extrapolation_never_reaches_back_before_the_charge() -> None:
    """A reading from the night before must not be charged for the whole night."""

    tracking = SocTracking()
    tracking.update_max_energy(MAX_ENERGY_KWH)
    tracking.update_power(POWER_W, READING_AT)
    tracking.update_actual_soc(38.0, CHARGE_START - timedelta(hours=14), restored=True)
    tracking.restore_status("chargingactive", CHARGE_START)
    value = tracking.estimate(APP_OPENED)
    expected = 38.0 + RATE * (APP_OPENED - CHARGE_START).total_seconds() / 3600.0
    assert abs(value - expected) < 0.01


def test_restored_status_leaves_session_transitions_to_the_stream() -> None:
    """The first live CHARGINGACTIVE after a restart must still be a transition."""

    tracking = _restored(("energy", "power", "reading", "estimate", "status"))
    assert tracking.charging_active is False
    tracking.update_status("CHARGINGACTIVE")
    assert tracking.charging_active is True
    assert tracking.restored_charging is False


def test_live_nocharging_stops_the_restored_extrapolation() -> None:
    tracking = _restored(("energy", "power", "reading", "estimate", "status"))
    stopped = ESTIMATE_AT + timedelta(minutes=30)
    held = tracking.estimate(stopped)
    tracking.update_status("NOCHARGING")
    assert tracking.estimate(APP_OPENED) == held
