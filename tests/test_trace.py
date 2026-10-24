"""The refactoring gate's own tests: the trace sees everything, and has teeth.

``scripts/golden.py`` proves a refactor changed nothing by comparing traces
(``trace.py``). That proof is only as good as three properties, each pinned
here so it cannot quietly erode:

* **complete** -- every public method of the coordinator is either read on
  every step or named as not a read, so new surface can't go unobserved;
* **deterministic** -- the same timeline traces the same twice, or every
  later diff is noise;
* **sharp** -- a second car that leaks into the first car's state is caught,
  and a second car that doesn't is not flagged.

The full gate (every harness test, base against working tree, two twin
offsets) takes minutes and runs from the script; these take seconds and run in
CI on every push.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from .harness import CoordinatorHarness, _clocked_modules, coordinator_mod
from .test_coordinator_robustness import _fuzz_day
from .trace import ACCOUNT, NOT_PROBED, PER_CAR

_spec = importlib.util.spec_from_file_location(
    "golden", Path(__file__).resolve().parents[1] / "scripts" / "golden.py"
)
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

SEEDS = (3, 17, 42)


def _trace(tmp_path: Path, name: str, seed: int, **harness) -> Path:
    out = tmp_path / name
    out.mkdir(exist_ok=True)
    _fuzz_day(seed, trace=out / f"day-{seed}.trace", **harness).close()
    return out


def test_every_public_coordinator_method_is_read_or_named_as_not_a_read():
    public = {
        name
        for name, value in vars(coordinator_mod.CardataCoordinator).items()
        if not name.startswith("_") and callable(value)
    }
    accounted = set(PER_CAR) | set(ACCOUNT) | NOT_PROBED
    assert public - accounted == set(), (
        "new public coordinator methods: add a probe to tests/trace.py "
        "(PER_CAR or ACCOUNT), or name them in NOT_PROBED if they are no read"
    )
    assert {name for name in NOT_PROBED if name not in public} == set(), "stale NOT_PROBED"


@pytest.mark.parametrize("seed", SEEDS)
def test_the_same_day_traces_the_same_twice(tmp_path, seed):
    first = _trace(tmp_path, "first", seed)
    second = _trace(tmp_path, "second", seed)

    assert golden.compare(first, second)


@pytest.mark.parametrize("offset", [7.25, 0.0])
@pytest.mark.parametrize("seed", SEEDS)
def test_a_twin_car_leaves_the_first_cars_trace_alone(tmp_path, seed, offset):
    solo = _trace(tmp_path, "solo", seed)
    twin = _trace(tmp_path, "twin", seed, twin=offset)

    assert golden.compare(solo, twin, car="A")
    # The twin did live a day of its own; an idle twin would prove nothing.
    assert "  [B] ev " in (twin / f"day-{seed}.trace").read_text(encoding="utf-8")


def test_a_leak_between_cars_is_caught(tmp_path, monkeypatch):
    """Plant the bug the isolation check exists for, and watch it fail."""

    real = coordinator_mod.CardataCoordinator.get_soc_estimate

    def leaky(self, vin):
        # Whichever car spoke last answers for every car: one shared slot.
        latest = max(self.last_message_by_vin, key=self.last_message_by_vin.get, default=vin)
        return real(self, latest)

    seed = SEEDS[0]
    solo = _trace(tmp_path, "solo", seed)
    monkeypatch.setattr(coordinator_mod.CardataCoordinator, "get_soc_estimate", leaky)
    twin = _trace(tmp_path, "twin", seed, twin=7.25)

    assert not golden.compare(solo, twin, car="A")


def test_a_changed_payload_is_caught(tmp_path, monkeypatch):
    """A refactor that drops one field of one event must not pass the gate."""

    seed = SEEDS[0]
    before = _trace(tmp_path, "before", seed)
    real = coordinator_mod.CardataCoordinator._fire

    def lossy(self, event_type, payload):
        real(self, event_type, {k: v for k, v in payload.items() if k != "duration_s"})

    monkeypatch.setattr(coordinator_mod.CardataCoordinator, "_fire", lossy)
    after = _trace(tmp_path, "after", seed)

    assert not golden.compare(before, after)


def test_a_moved_timer_is_caught(tmp_path, monkeypatch):
    """A close timer armed a second late is a behaviour change too."""

    seed = SEEDS[1]
    before = _trace(tmp_path, "before", seed)
    # Every module that arms a timer (the coordinator's engines too).
    armers = [m for m in _clocked_modules() if hasattr(m, "async_call_later")]
    assert armers
    for module in armers:
        real = module.async_call_later

        def late(hass, delay, callback, real=real):
            return real(hass, delay + 1, callback)

        monkeypatch.setattr(module, "async_call_later", late)
    after = _trace(tmp_path, "after", seed)

    assert not golden.compare(before, after)


def test_twin_mode_tags_the_second_car_and_keeps_its_batches_apart():
    with CoordinatorHarness(twin=5) as h:
        h.send(1, soc=40, status="CHARGINGACTIVE", power=3480)
        h.advance_to(10)

        assert h.vins[:2] == [h.vin, h.twin_vin]
        assert h.alias(h.vin) == "A" and h.alias(h.twin_vin) == "B"
        assert h.coordinator.get_state(h.twin_vin, "vehicle.drivetrain.batteryManagement.header")
        # The twin's word arrived five minutes after the first car's, stamped so.
        stamp = h.coordinator.get_state(
            h.twin_vin, "vehicle.drivetrain.batteryManagement.header"
        ).timestamp
        assert stamp == "2026-09-26T08:06:00.000Z"
