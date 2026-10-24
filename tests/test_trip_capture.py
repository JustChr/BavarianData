"""Trip-capture mode: where the detector's lines go, and the raw-batch file.

Capture mode is what a user switches on to send a drive in (``tests/replays``),
so it has to say what it promises: every batch, compact in the log and whole in
the file, and a file that stops growing at its cap instead of filling the disk.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from .conftest import load_module

trip_capture = load_module("trip_capture")
NOW = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
VIN = "WBATEST0000000001"


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.bavariandata")
    return caplog


def test_capture_mode_logs_everything_on_its_own_logger(lines):
    log = trip_capture.TripLog()
    log.enabled = True
    log.cap("[trip] %s decision", VIN)
    log.cap("[trip.gps] %s substrate", VIN, substrate=True)

    records = [r for r in lines.records if r.name == trip_capture.TRIPLOG.name]
    assert [r.levelno for r in records] == [logging.INFO, logging.INFO]


def test_without_capture_only_decisions_reach_the_debug_log(lines, monkeypatch):
    monkeypatch.setattr(trip_capture, "debug_enabled", lambda: True)
    log = trip_capture.TripLog()
    log.cap("[trip] %s decision", VIN)
    log.cap("[trip.gps] %s substrate", VIN, substrate=True)

    messages = [r.getMessage() for r in lines.records]
    assert f"[trip] {VIN} decision" in messages
    assert not any("substrate" in m for m in messages)


def test_with_debug_off_nothing_is_logged(lines, monkeypatch):
    monkeypatch.setattr(trip_capture, "debug_enabled", lambda: False)
    trip_capture.TripLog().cap("[trip] %s decision", VIN)

    assert lines.records == []


def test_a_batch_is_logged_raw_by_segment_and_by_watched_field(lines):
    log = trip_capture.TripLog()
    log.enabled = True
    data = {
        "vehicle.isMoving": {"value": True, "timestamp": "2026-10-24T07:59:00Z"},
        "vehicle.trip.segment.end.travelledDistance": {
            "value": 12.5,
            "timestamp": "2026-10-24T07:58:00Z",
        },
        "vehicle.trip.segment.accumulated.chargingCount": 3,
        "vehicle.body.lights": {"value": {"low": True}},
    }
    log.log_batch(VIN, data, NOW, open_trip=True, fresh_s=900)

    messages = [r.getMessage() for r in lines.records]
    raw = next(m for m in messages if m.startswith("[trip.raw]"))
    assert "open=True n=4" in raw and "body.lights=<dict>" in raw
    seg = next(m for m in messages if m.startswith("[trip.seg]"))
    assert "freshest_age=120s fresh=True" in seg
    assert "accumulated.chargingCount=3" in seg
    watch = next(m for m in messages if m.startswith("[trip.watch]"))
    assert "isMoving=True@2026-10-24T07:59:00Z" in watch


def test_a_stale_segment_is_not_fresh(lines):
    log = trip_capture.TripLog()
    log.enabled = True
    data = {"vehicle.trip.segment.end.hvSoc": {"value": 80, "timestamp": "2026-10-23T08:00:00Z"}}
    log.log_batch(VIN, data, NOW, open_trip=False, fresh_s=900)

    seg = next(r.getMessage() for r in lines.records if "[trip.seg]" in r.getMessage())
    assert "fresh=False" in seg


def test_a_batch_that_breaks_the_log_never_breaks_the_stream(lines):
    log = trip_capture.TripLog()
    log.enabled = True
    log.log_batch(VIN, {"vehicle.x": {"value": 1}}, None, open_trip=False, fresh_s=900)  # type: ignore[arg-type]

    # ``now`` was None: nothing raised, the failure was logged.
    log.log_batch(
        VIN,
        {"vehicle.trip.segment.end.x": {"timestamp": "2026-10-24T07:00:00Z"}},
        None,
        open_trip=False,
        fresh_s=900,
    )  # type: ignore[arg-type]
    assert any(r.levelno == logging.ERROR for r in lines.records)


def test_a_record_is_one_json_line_or_none():
    line = trip_capture.TripLog.record_line(VIN, {"vehicle.x": {"value": 1}}, NOW, open_trip=True)
    assert json.loads(line) == {
        "at": NOW.isoformat(),
        "vin": VIN,
        "open": True,
        "data": {"vehicle.x": {"value": 1}},
    }
    circular: dict = {}
    circular["self"] = circular
    assert trip_capture.TripLog.record_line(VIN, circular, NOW, open_trip=False) is None


def test_the_file_grows_until_its_cap_then_warns_once(tmp_path, lines, monkeypatch):
    monkeypatch.setattr(trip_capture, "TRIP_CAPTURE_MAX_BYTES", 10)
    path = tmp_path / trip_capture.TRIP_CAPTURE_FILE
    log = trip_capture.TripLog()
    for _ in range(4):
        log.append_line(str(path), "0123456789")

    assert path.read_text(encoding="utf-8") == "0123456789\n"
    warnings = [r for r in lines.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "exceeded" in warnings[0].getMessage()


def test_a_failed_write_is_a_warning(tmp_path, lines):
    trip_capture.TripLog().append_line(str(tmp_path / "missing" / "capture.ndjson"), "x")

    assert any("write failed" in r.getMessage() for r in lines.records)


@pytest.mark.parametrize("value", [None, "", "not a time", "2026-10-24T08:00:00", 5])
def test_only_a_zoned_timestamp_parses(value):
    assert trip_capture._parse(value) is None
