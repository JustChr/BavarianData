"""Every capture in ``tests/replays/`` is a regression test.

Trip-capture mode records BMW's raw batches; replayed through the real
coordinator they reproduce what the integration did with a real day -- the
trips it recorded, the charges, the estimate along the way -- and the snapshot
pins it. Turning a bug report into a regression test is: capture, anonymize
(``tools/anonymize_capture.py``), drop the file here, run with
``--snapshot-update``, read the result, fix, and watch the diff.

The anonymizer has tests of its own below, because a replay that leaks a VIN or
a home address into a public repository is worse than no replay at all.
"""

from __future__ import annotations

import json
import math
import pathlib
from datetime import datetime
from itertools import pairwise

import pytest

from .harness import CoordinatorHarness

REPLAYS = sorted((pathlib.Path(__file__).parent / "replays").glob("*.ndjson"))


def _load_tool():
    import importlib.util

    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "anonymize_capture.py"
    spec = importlib.util.spec_from_file_location("anonymize_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anonymize_capture = _load_tool()


def _records(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _first_position(records: list[dict]):
    found: dict[str, float] = {}
    for record in records:
        for key, entry in record["data"].items():
            axis = key.rsplit(".", 1)[-1]
            if axis in ("latitude", "longitude") and axis not in found:
                found[axis] = entry["value"]
        if len(found) == 2:
            return found["latitude"], found["longitude"]
    return None


def _summary(h: CoordinatorHarness) -> str:
    lines = ["trips:"]
    for trip in sorted(h.trips(), key=lambda t: t.start):
        end = "open" if trip.end is None else f"{trip.end:%H:%M:%S}"
        lines.append(
            f"  {trip.start:%H:%M:%S} -> {end}  {trip.distance_km} km  "
            f"{trip.start_place.get('label')} -> {trip.end_place.get('label')}"
        )
    lines.append("charging sessions:")
    for session in sorted(h.sessions(), key=lambda s: s.start):
        lines.append(
            f"  {session.start:%H:%M:%S} -> {session.end:%H:%M:%S}  "
            f"{session.soc_start} -> {session.soc_end} %  {session.energy_kwh} kWh  "
            f"{session.end_reason}"
        )
    lines.append(f"events: {[name.removeprefix('bavariandata_') for name, _ in h.events('')]}")
    return "\n".join(lines)


@pytest.mark.parametrize("path", REPLAYS, ids=[p.stem for p in REPLAYS])
def test_a_captured_day_replays_to_what_it_did_before(path, snapshot):
    records = _records(path)
    start = datetime.fromisoformat(records[0]["at"])
    # A real install restores the last known position before the first live
    # fix (the device tracker does); a capture does not contain it, so take
    # the first complete fix. Without this every replay would start inside
    # the fresh-install pairing bug pinned in test_scenarios_trips.py.
    home = _first_position(records)
    with CoordinatorHarness(capacity_kwh=None, start=start, vin=records[0]["vin"], home=home) as h:
        h.hass.add_zone("Home", *anonymize_capture.ORIGIN, radius_m=150)
        h.replay(records)
        h.advance_to(h.minute + 60)  # let every debounce and hold run out

        assert _summary(h) + "\n--\n" + h.render() == snapshot


def test_the_replay_folder_is_not_empty():
    """An empty glob would turn the replay suite into a silent no-op."""

    assert REPLAYS


# --- the anonymizer -----------------------------------------------------------

REAL_VIN = "WBY71HH030CW55801"
LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"


def _capture() -> list[str]:
    def rec(at, data):
        return json.dumps({"at": at, "vin": REAL_VIN, "open": False, "data": data})

    return [
        rec(
            "2026-09-26T06:10:00+00:00",
            {LAT: {"value": 47.26, "timestamp": "2026-09-26T06:10:00Z"}},
        ),
        rec(
            "2026-09-26T06:10:01+00:00",
            {LON: {"value": 11.39, "timestamp": "2026-09-26T06:10:00Z"}},
        ),
        rec(
            "2026-09-26T06:12:00+00:00",
            {LAT: {"value": 47.27, "timestamp": "2026-09-26T06:12:00Z"}},
        ),
        rec(
            "2026-09-26T06:12:01+00:00",
            {LON: {"value": 11.40, "timestamp": "2026-09-26T06:12:00Z"}},
        ),
        rec(
            "2026-09-26T06:15:00+00:00",
            {
                "vehicle.cabin.infotainment.navigation.destination.address": {"value": "Home St 1"},
                "vehicle.drivetrain.batteryManagement.header": {"value": 54},
            },
        ),
    ]


def test_the_anonymizer_leaves_no_vin_position_date_or_address():
    out = "\n".join(anonymize_capture.anonymize(_capture()))

    assert REAL_VIN not in out
    assert "47.26" not in out and "11.39" not in out
    assert "2026-09-26" not in out
    assert "Home St" not in out and "destination" not in out
    assert "vehicle.drivetrain.batteryManagement.header" in out  # data survives


def test_the_anonymizer_keeps_distances_and_timing():
    before = [json.loads(line) for line in _capture()]
    after = [json.loads(line) for line in anonymize_capture.anonymize(_capture())]

    def step(records):
        lat = [r["data"][LAT]["value"] for r in records if LAT in r["data"]]
        lon = [r["data"][LON]["value"] for r in records if LON in r["data"]]
        return lat[1] - lat[0], lon[1] - lon[0]

    assert all(
        math.isclose(a, b, abs_tol=1e-9) for a, b in zip(step(before), step(after), strict=True)
    )

    def gaps(records):
        times = [datetime.fromisoformat(r["at"]) for r in records]
        return [b - a for a, b in pairwise(times)]

    assert gaps(before) == gaps(after)
    assert after[0]["data"][LAT]["value"] == anonymize_capture.ORIGIN[0]
