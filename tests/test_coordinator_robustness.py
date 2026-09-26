"""The coordinator must survive whatever arrives, in whatever order.

Two nets under the scenario tests:

* **Malformed input.** BMW's stream is untrusted input: values arrive as
  strings, lists arrive JSON-encoded, timestamps go missing, descriptors appear
  that the catalogue never listed. A message handler that raises drops the rest
  of the batch -- and on the MQTT thread, every batch after it.
* **Random timelines.** A seeded fuzzer strings together sends, silences and
  restarts and GPS random walks in random order, with
  :func:`harness.check_invariants` run after every step (estimate within 0-100,
  no negative energy or distance, no overlapping or backwards sessions or trips,
  and no error the integration logged and swallowed). A failure prints the seed and the whole timeline, so it
  replays exactly: ``_fuzz(seed)``.
"""

from __future__ import annotations

import random

import pytest

from .harness import DESCRIPTORS, CoordinatorHarness

MALFORMED = [
    pytest.param({"soc": "abc"}, id="soc-not-a-number"),
    pytest.param({"soc": None}, id="soc-null"),
    pytest.param({"soc": -5}, id="soc-negative"),
    pytest.param({"soc": 180}, id="soc-over-100"),
    pytest.param({"soc": [1, 2]}, id="soc-a-list"),
    pytest.param({"power": "n/a"}, id="power-not-a-number"),
    pytest.param({"power": -3000}, id="power-negative"),
    pytest.param({"status": 42}, id="status-a-number"),
    pytest.param({"status": ""}, id="status-empty"),
    pytest.param({"status": "SOMETHING_NEW"}, id="status-unknown-token"),
    pytest.param({"capacity": 0}, id="capacity-zero"),
    pytest.param({"capacity": "big"}, id="capacity-not-a-number"),
    pytest.param({"target": "full"}, id="target-not-a-number"),
]


@pytest.mark.parametrize("values", MALFORMED)
def test_a_malformed_value_neither_raises_nor_breaks_the_estimate(values):
    with CoordinatorHarness() as h:
        h.send(10, soc=50, status="CHARGINGACTIVE", power=3480)
        h.send(20, step="malformed", **values)
        h.advance_to(40)
        h.send(40, soc=52, status="CHARGINGACTIVE", power=3480, step="healthy again")

        assert h.estimate() == 52  # the next real reading still lands


@pytest.mark.parametrize(
    "timestamp",
    [None, "", "yesterday", "2026-13-45T99:99:99Z", "1970-01-01T00:00:00Z", 1727337600],
    ids=["missing", "empty", "words", "impossible-date", "epoch", "a-number"],
)
def test_a_broken_timestamp_neither_raises_nor_breaks_the_estimate(timestamp):
    with CoordinatorHarness() as h:
        h.send(10, soc=50, status="CHARGINGACTIVE", power=3480)
        path, unit = DESCRIPTORS["soc"]
        h._run(
            h.coordinator.async_handle_message(
                {"vin": h.vin, "data": {path: {"value": 51, "unit": unit, "timestamp": timestamp}}}
            )
        )
        h.advance_to(30)

        assert 0 <= h.estimate() <= 100


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"vin": None, "data": {}},
        {"vin": "WBATEST0000000001"},
        {"vin": "WBATEST0000000001", "data": None},
        {"vin": "WBATEST0000000001", "data": []},
        {"vin": "WBATEST0000000001", "data": {"vehicle.unknown.descriptor": {"value": 1}}},
        {"vin": "WBATEST0000000001", "data": {"vehicle.cabin.x": "not-a-dict"}},
        {"vin": "WBATEST0000000001", "data": {"vehicle.cabin.x": {}}},
    ],
    ids=[
        "empty",
        "vin-null",
        "no-data",
        "data-null",
        "data-a-list",
        "unknown-descriptor",
        "descriptor-not-a-dict",
        "descriptor-without-value",
    ],
)
def test_a_malformed_batch_is_dropped_quietly(payload):
    with CoordinatorHarness() as h:
        h._run(h.coordinator.async_handle_message(payload))
        h.send(10, soc=50, status="NOCHARGING", step="healthy")

        assert h.estimate() == 50


def test_a_second_car_in_the_same_account_is_tracked_apart():
    with CoordinatorHarness() as h:
        other = "WBATEST0000000002"
        h.send(10, soc=40, status="CHARGINGACTIVE", power=3480)
        path_soc, _ = DESCRIPTORS["soc"]
        path_status, _ = DESCRIPTORS["status"]
        h._run(
            h.coordinator.async_handle_message(
                {
                    "vin": other,
                    "data": {
                        path_soc: {"value": 80, "timestamp": "2026-09-26T08:10:00Z"},
                        path_status: {"value": "NOCHARGING", "timestamp": "2026-09-26T08:10:00Z"},
                    },
                }
            )
        )
        h.advance_to(70)

        assert h.coordinator.get_soc_estimate(other) == 80  # parked car stays put
        assert h.estimate() > 40  # the charging one climbs
        assert h.coordinator.get_soc_rate(other) is None


# --- random timelines ---------------------------------------------------------

STATUSES = ["CHARGINGACTIVE", "NOCHARGING", "CHARGINGPAUSED", "CHARGINGENDED", "INITIALIZATION"]


def _fuzz(seed: int) -> CoordinatorHarness:
    rng = random.Random(seed)
    h = CoordinatorHarness(home=(48.1, 16.3) if rng.random() < 0.7 else None)
    minute = 0.0
    soc = rng.uniform(5, 90)
    lat, lon = 48.1, 16.3
    try:
        for _ in range(rng.randint(5, 40)):
            minute += rng.choice([0, 0.5, 1, 2, 5, 15, 60, 240])
            action = rng.random()
            if action < 0.55:
                values = {}
                if rng.random() < 0.7:
                    soc = min(100.0, max(0.0, soc + rng.uniform(-3, 8)))
                    values["soc"] = round(soc)
                if rng.random() < 0.6:
                    values["status"] = rng.choice(STATUSES)
                if rng.random() < 0.6:
                    values["power"] = rng.choice([0, 1400, 3480, 7400, 11000])
                if rng.random() < 0.1:
                    values["target"] = rng.choice([50, 80, 100])
                stamped = minute - rng.choice([0, 0, 0, 5, 120]) if rng.random() < 0.3 else None
                h.send(minute, stamped=max(stamped, 0) if stamped is not None else None, **values)
            elif action < 0.72:
                # A random walk: parked jitter, city hops, motorway jumps.
                hop = rng.choice([0.0, 0.0001, 0.003, 0.01, 0.05])
                lat += rng.uniform(-hop, hop)
                lon += rng.uniform(-hop, hop)
                h.fix(minute, lat, lon)
                minute = h.minute
            elif action < 0.8:
                h.advance_to(minute)
            elif action < 0.9:
                h.restart(
                    downtime_s=rng.choice([10, 60, 600, 7200]),
                    order=rng.choice(["descriptors-first", "derived-first"]),
                )
                minute = h.minute
            else:
                h.connection(rng.choice(["connected", "disconnected", "unauthorized"]))
    except AssertionError as err:
        raise AssertionError(f"seed {seed}: {err}") from None
    return h


@pytest.mark.parametrize("seed", range(300))
def test_random_timelines_keep_every_invariant(seed):
    h = _fuzz(seed)
    h.close()
