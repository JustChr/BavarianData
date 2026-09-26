"""Trip detection, run through the real coordinator.

The detector works from what the i5 really streams (see the ``trip-detection``
notes): GPS fixes as separate latitude/longitude messages, a close debounced
over :data:`TRIP_CLOSE_DEBOUNCE_S`, and a *held* close when the stream goes
quiet mid-drive -- a tunnel must not split one drive into two. Each scenario
asserts its point and snapshots the recorded trips together with the timeline.

Positions step about 1.3 km per fix; Home is a 150 m zone at the start point. A
drive ends with a *stationary* fix, as a real parked car keeps reporting where
it stands: that is the stop evidence a close needs. Without it the close is held
(up to ``TRIP_SILENT_HOLD_MAX_S``), which has its own scenario below.

The fixture restores a parked position at Home, as the device tracker does on
a real install. Until 0.9.13-beta.3 that restored position was ignored: the
first fix of a drive only seeded the tracker, so the trip opened one fix late,
away from Home -- see ``test_a_drive_out_of_home_starts_at_home``.
"""

from __future__ import annotations

import pytest

from .harness import CoordinatorHarness

HOME = (48.1000, 16.3000)
STEP = 0.01  # degrees per fix, ~1.3 km


@pytest.fixture
def h():
    harness = CoordinatorHarness(home=HOME)
    harness.hass.add_zone("Home", *HOME, radius_m=150)
    yield harness
    harness.close()


def _drive(
    h: CoordinatorHarness, start: float, fixes: int, *, every: float = 2, origin=HOME, step=STEP
):
    for i in range(1, fixes + 1):
        h.fix(start + i * every, origin[0] + i * step, origin[1] + i * step)
    return (origin[0] + fixes * step, origin[1] + fixes * step)


def _trips_text(h: CoordinatorHarness) -> str:
    lines = []
    for trip in h.trips():
        end = "open" if trip.end is None else trip.end.strftime("%H:%M:%S")
        lines.append(
            f"{trip.start:%H:%M:%S} -> {end}  {trip.distance_km} km  "
            f"{trip.start_place.get('label')} -> {trip.end_place.get('label')}"
        )
    return "\n".join(lines) or "(no trips)"


def _record(h: CoordinatorHarness) -> str:
    return f"{_trips_text(h)}\n--\n{h.render()}"


def test_a_drive_is_one_trip_ending_at_the_last_movement(h, snapshot):
    here = _drive(h, 5, 10)
    h.fix(27, *here)  # parked, still reporting its position
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.end < h.at(26)  # the last moving fix, not when the timer fired
    assert _record(h) == snapshot


def test_a_drive_out_of_home_starts_at_home(h):
    """The first fix after a restart is already movement from the parked spot."""

    _drive(h, 5, 5)
    h.fix(17, HOME[0] + 5 * STEP, HOME[1] + 5 * STEP)
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.start_place["label"] == "Home"
    assert trip.start <= h.at(7.1)  # the first moving fix, not the second


def test_a_close_without_stop_evidence_is_held_not_guessed(h, snapshot):
    """Fixes simply stop: the car may be in a tunnel, so the trip waits."""

    _drive(h, 5, 5)
    h.advance_to(40)  # well past the close debounce
    assert h.trips() == []  # held: silence is not evidence of a stop
    h.advance_to(5 * 60)  # past TRIP_SILENT_HOLD_MAX_S

    (trip,) = h.trips()
    assert trip.end < h.at(16)  # still backdated to the last movement
    assert _record(h) == snapshot


def test_a_tunnel_does_not_split_the_drive(h, snapshot):
    """Ten silent minutes mid-drive, then the fixes resume far away."""

    here = _drive(h, 5, 5)
    # Silence from minute 15 to 25: longer than the close debounce.
    there = _drive(h, 25, 5, origin=(here[0] + 5 * STEP, here[1] + 5 * STEP))
    h.fix(37, *there)
    h.advance_to(60)

    assert len(h.trips()) == 1
    assert _record(h) == snapshot


def test_a_garage_that_swallows_the_fix_closes_at_the_arrival(h, snapshot):
    """Silent after parking, then a fix at the same spot: the car stood."""

    here = _drive(h, 5, 5)
    h.fix(40, *here)  # the fix comes back where it left off
    h.advance_to(60)

    (trip,) = h.trips()
    assert trip.end <= h.at(16)  # backdated to the arrival, not to minute 40
    assert _record(h) == snapshot


def test_a_short_hop_is_not_a_trip(h):
    h.fix(5, HOME[0] + 0.001, HOME[1] + 0.001)  # ~130 m
    h.advance_to(30)

    assert h.trips() == []


def test_two_drives_with_a_stop_between_are_two_trips(h, snapshot):
    here = _drive(h, 5, 5)
    h.fix(17, *here)  # parked
    h.advance_to(60)
    there = _drive(h, 60, 5, origin=here)
    h.fix(72, *there)
    h.advance_to(100)

    assert len(h.trips()) == 2
    first, second = sorted(h.trips(), key=lambda t: t.start)
    assert first.end <= second.start
    assert _record(h) == snapshot


def test_a_restart_mid_drive_does_not_lose_the_trip(h, snapshot):
    here = _drive(h, 5, 5)
    h.restart(downtime_s=60)
    there = _drive(h, h.minute, 5, origin=here)
    h.fix(h.minute + 2, *there)
    h.advance_to(h.minute + 30)

    trips = h.trips()
    assert trips, "the drive was lost across the restart"
    assert _record(h) == snapshot


def test_a_fresh_install_pairs_its_gps_halves_correctly():
    """Found by this harness: without a restored position, pairing drifted.

    BMW sends a fix as latitude then longitude. With no position known yet (a
    fresh install, or a device tracker that never saved one), the first
    latitude was dropped before its arrival was registered -- the longitude
    check returned early -- so every later fix paired the new latitude with
    the previous longitude: the L-shaped track ``_gps_fix_ready`` exists to
    prevent.
    """

    with CoordinatorHarness() as fresh:
        for i in range(4):
            fresh.fix(5 + i * 2, HOME[0] + i * STEP, HOME[1] + i * STEP)
        assert fresh.coordinator._gps_pending_parts.get(fresh.vin, set()) == set()
