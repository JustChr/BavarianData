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

import json
import random
from pathlib import Path
from typing import Any

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


# --- random days ----------------------------------------------------------------

HOME = (48.1, 16.3)
WORK = (48.2, 16.4)
DAY_METER = "sensor.fuzz_wallbox_energy"
DAY_PRICE = "sensor.fuzz_price"
# The descriptor lists of real cars (``tests/cars``), for batches of whatever
# else a car sends -- doors, windows, check-control, tyres -- beside the few
# descriptors the scenarios name.
CARS = {
    path.stem: [
        d["descriptor"] for d in json.loads(path.read_text(encoding="utf-8"))["descriptors"]
    ]
    for path in sorted((Path(__file__).parent / "cars").glob("*.json"))
}
BASIC_DATA = [
    {"modelName": "i5 eDrive40", "brand": "BMW", "driveTrain": "BEV", "puStep": "0723"},
    {"modelName": "530e", "brand": "BMW", "driveTrain": "PHEV", "bodyType": "G60"},
    {"modelName": "Cooper SE", "brand": "MINI", "driveTrain": "BEV"},
    {"modelName": 5, "brand": "BMW", "puStep": 723},
]
TYRES = {
    "passengerCar": {
        "mountedTyres": {
            "aggregatedQualityStatus": {"qualityStatus": "GOOD", "label": "Zustand"},
            "frontLeft": {"tyreWear": {"status": "OK", "statusColor": "GREEN"}},
        }
    }
}


def _corpus_value(rng: random.Random, descriptor: str) -> Any:
    """A value of the kind ``descriptor`` carries, now and then a wrong one."""

    roll = rng.random()
    if roll < 0.05:
        return rng.choice([None, "", "garbage", [1, 2], {"x": 1}, -1])
    leaf = descriptor.rsplit(".", 1)[-1]
    if leaf.startswith(("is", "has")):
        return rng.random() < 0.5
    if roll < 0.55:
        return rng.choice([0, 1, 2, 12.5, 48.0, 80, 230, 1000.0])
    return rng.choice(["OPEN", "CLOSED", "LOCKED", "UNLOCKED", "INTERMEDIATE", "OK", "LOW"])


def _fuzz_day(seed: int, **harness: Any) -> CoordinatorHarness:
    """A random day of a car's life: drives, charges at home and away, chaos.

    The timeline fuzzer above throws loose values at the charging logic; this
    one walks a car through what it really does -- park, unlock, drive with
    the odometer climbing and the fix moving, arrive at a zone, plug in on the
    wallbox meter or an AC socket that reports volts and amps -- with
    restarts, stream drops, late and stale batches, cleared values, the car's
    other descriptors, basic data, tyre fetches, a live price and random
    silences thrown in, so trips, sessions, the meter and the edge events all
    run under the invariants together.
    """

    rng = random.Random(seed)
    priced = rng.random() < 0.3
    h = CoordinatorHarness(
        home=HOME if rng.random() < 0.8 else None,
        wallbox_meter=DAY_METER if rng.random() < 0.6 else None,
        pricing={"price_entity": DAY_PRICE} if priced else None,
        trip_debug=rng.random() < 0.15,
        **harness,
    )
    h.hass.add_zone("Home", *HOME, radius_m=150)
    h.hass.add_zone("Work", *WORK, radius_m=150)
    car = CARS[rng.choice(sorted(CARS))]
    minute = 0.0
    soc = rng.uniform(15, 90)
    odometer = rng.uniform(1000, 90000)
    lat, lon = HOME
    meter_kwh = 1000.0
    charging = False
    try:
        if h.wallbox_meter is not None:
            h.meter(0, meter_kwh)
        if priced:
            h.hass.set_state(DAY_PRICE, rng.choice(["0.31", "unavailable", "0.18"]))
        if rng.random() < 0.5:
            h.send(0, basic=rng.choice(BASIC_DATA), step="basic data")
        for _ in range(rng.randint(8, 30)):
            minute = max(minute, h.minute) + rng.choice([0.5, 1, 3, 10, 30, 90])
            action = rng.random()
            if action < 0.3:
                # A drive: unlock, door, ignition, moving fixes, park.
                h.send(minute, door=True, step="door open")
                h.send(minute + 0.2, door=False, ignition=True, moving=True)
                target = rng.choice([HOME, WORK, (lat + 0.05, lon - 0.03)])
                legs = rng.randint(1, 6)
                for leg in range(1, legs + 1):
                    minute = h.minute + rng.choice([1, 2, 3])
                    step_lat = lat + (target[0] - lat) * leg / legs
                    step_lon = lon + (target[1] - lon) * leg / legs
                    odometer += rng.uniform(0.3, 4)
                    soc = max(0.0, soc - rng.uniform(0, 1.5))
                    h.fix(minute, step_lat, step_lon, odometer=round(odometer, 1))
                    if rng.random() < 0.5:
                        h.send(h.minute + 0.1, soc=round(soc))
                lat, lon = target
                end = h.minute + rng.choice([0.5, 1, 5])
                h.send(end, moving=False, ignition=False, step="park")
                if rng.random() < 0.7:
                    h.send(end + 0.3, door=True)
                    h.send(end + 0.6, door=False)
                minute = h.minute
            elif action < 0.52:
                # A charge, at whatever place the car stands.
                power = rng.choice([3700, 7400, 11000, 50000])
                start: dict[str, Any] = {"plug": "CONNECTED", "status": "CHARGINGACTIVE"}
                if rng.random() < 0.4:
                    # An AC socket that reports volts, amps and phases instead.
                    phases = rng.choice([1, 3, "3", "THREE_PHASES", "2 phases", 0])
                    start.update(ac_volt=230, ac_amp=rng.choice([10, 16, 32]), phases=phases)
                else:
                    start["power"] = power
                if rng.random() < 0.4:
                    start["target"] = rng.choice([60, 80, 100])
                h.send(minute, soc=round(soc), **start)
                charging = True
                for _ in range(rng.randint(1, 8)):
                    minute = h.minute + rng.choice([1, 5, 15, 30])
                    kwh = power / 1000 * rng.choice([1, 5, 15, 30]) / 60
                    soc = min(100.0, soc + kwh * 1.3)
                    if h.wallbox_meter is not None and rng.random() < 0.8:
                        meter_kwh += kwh
                        h.meter(minute, round(meter_kwh, 3))
                    if priced and rng.random() < 0.2:
                        h.hass.set_state(DAY_PRICE, rng.choice(["0.29", "unknown", "x"]))
                    if rng.random() < 0.6:
                        h.send(h.minute + 0.1, soc=round(soc))
                end = rng.choice(["CHARGINGENDED", "NOCHARGING", "CHARGINGPAUSED", "FINISHED"])
                h.send(h.minute + 1, status=end, power=0)
                if rng.random() < 0.7:
                    h.send(h.minute + rng.choice([0.5, 3, 10]), plug="DISCONNECTED")
                    charging = False
                minute = h.minute
            elif action < 0.6:
                # The car's other descriptors, a handful at a time.
                picks = rng.sample(car, k=min(len(car), rng.randint(1, 8)))
                h.send(
                    minute,
                    extra={d: _corpus_value(rng, d) for d in picks},
                    step="corpus " + ",".join(d.rsplit(".", 1)[-1] for d in picks),
                )
            elif action < 0.65:
                # BMW clearing values: a ``null`` where a reading was.
                cleared = rng.sample(
                    ["status", "power", "target", "ac_volt", "ac_amp", "phases"], 3
                )
                h.send(minute, step="cleared " + ",".join(cleared), **dict.fromkeys(cleared))
            elif action < 0.72:
                h.advance_to(minute + rng.choice([5, 60, 600]))
            elif action < 0.8:
                h.restart(
                    downtime_s=rng.choice([10, 60, 600, 7200]),
                    order=rng.choice(["descriptors-first", "derived-first"]),
                )
            elif action < 0.86:
                h.connection(rng.choice(["connected", "disconnected", "unauthorized"]))
            elif action < 0.92:
                # A REST catch-up or a late batch: the car's word, hours old.
                h.send(
                    minute,
                    stamped=max(0.0, minute - rng.choice([5, 60, 300])),
                    rest=rng.random() < 0.5,
                    soc=round(soc),
                    odometer=round(odometer, 1),
                )
            elif action < 0.95:
                h.tyres(rng.choice([TYRES, {}, None, {"passengerCar": "broken"}]), minute=minute)
            elif h.wallbox_meter is not None:
                # The meter going away and coming back.
                h.meter(minute, None)
                h.meter(minute + 1, round(meter_kwh, 3))
        h.advance_to(h.minute + (240 if charging else 60), step="settle")
    except AssertionError as err:
        raise AssertionError(f"day seed {seed}: {err}") from None
    return h


@pytest.mark.parametrize("seed", range(150))
def test_random_days_keep_every_invariant(seed):
    h = _fuzz_day(seed)
    h.close()
