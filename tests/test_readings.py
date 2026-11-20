"""The one rule for a reading's time and number (``readings.py``).

Half a dozen private parsers used to disagree on a stamp without a timezone:
``None`` in some, UTC in others, a naive datetime in the rest (which raises
the first time it meets an aware one). They all read through ``parse_time``
now, so its answers are pinned here once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from .conftest import load_module

readings = load_module("readings")
car_state = load_module("car_state")


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        # BMW's wire format, both precisions seen in the recorded cars.
        ("2026-11-20T18:04:11.123456Z", datetime(2026, 11, 20, 18, 4, 11, 123456, tzinfo=UTC)),
        ("2026-11-20T18:04:11.000Z", datetime(2026, 11, 20, 18, 4, 11, tzinfo=UTC)),
        # What our own stores write (``datetime.isoformat`` of an aware time).
        ("2026-11-20T18:04:11+00:00", datetime(2026, 11, 20, 18, 4, 11, tzinfo=UTC)),
        # An offset is kept, not converted.
        (
            "2026-11-20T19:04:11+01:00",
            datetime(2026, 11, 20, 19, 4, 11, tzinfo=timezone(timedelta(hours=1))),
        ),
        # No timezone: UTC, what BMW's stamps are and ours were written as.
        ("2026-10-24T08:00:00", datetime(2026, 10, 24, 8, 0, tzinfo=UTC)),
    ],
)
def test_a_timestamp_reads_as_an_aware_time(stamp, expected):
    parsed = readings.parse_time(stamp)

    assert parsed == expected
    assert parsed.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize("value", [None, "", "not a time", 5, 1.5, True, b"2026-10-24"])
def test_anything_else_is_no_time(value):
    assert readings.parse_time(value) is None


def test_a_reading_carries_its_parsed_time():
    state = car_state.DescriptorState(value=47, unit="%", timestamp="2026-11-20T18:04:11.000Z")

    assert state.at == datetime(2026, 11, 20, 18, 4, 11, tzinfo=UTC)
    assert car_state.DescriptorState(value=47, unit="%", timestamp=None).at is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3.0), ("4.5", 4.5), (True, 1.0), (None, None), ("x", None), ([1], None)],
)
def test_as_float_is_pythons_float_or_none(value, expected):
    assert readings.as_float(value) == expected
