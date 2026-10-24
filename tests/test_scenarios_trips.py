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


def _arrive_with_the_door_open(h: CoordinatorHarness, minute: float, origin) -> tuple:
    """The last moving fix, and the driver door opening right behind it.

    How the i5 arrives: the final position and the door land within the same
    second, so when the door opens the car has not yet been seen standing still
    -- and once parked, the stream goes quiet. The door closes ten seconds later.
    """

    here = (origin[0] + STEP, origin[1] + STEP)
    h.fix(minute, *here)
    h.send(minute + 2 / 60, door=True)
    h.send(minute + 12 / 60, door=False)
    return here


def test_a_quick_stop_ends_the_trip_at_the_arrival_not_the_departure(h, snapshot):
    """Parked three minutes on a silent stream, then back in and off again.

    Seen on the i5 (2026-10-20): the door opened with the last moving fix, too
    soon to count as an arrival, so nothing closed the trip until the driver
    opened the door again to leave -- and that close was dated "now", counting
    the three parked minutes as driving.
    """

    h.send(4, door=False)  # the driver got in
    here = _drive(h, 5, 10)
    here = _arrive_with_the_door_open(h, 27, here)
    h.send(30, door=True)  # back in, three silent minutes later
    h.send(30 + 5 / 60, door=False)
    there = _drive(h, 31, 5, origin=here)
    h.fix(43, *there)
    h.advance_to(70)

    first, second = sorted(h.trips(), key=lambda t: t.start)
    assert first.end <= h.at(27.1)  # the arrival, not the departure at minute 30
    assert second.start >= h.at(30)
    assert _record(h) == snapshot


def test_a_door_opened_on_arrival_is_stop_evidence(h, snapshot):
    """The stream goes quiet once parked; the door already said the car stopped."""

    h.send(4, door=False)
    here = _drive(h, 5, 10)
    _arrive_with_the_door_open(h, 27, here)
    h.advance_to(35)  # one close debounce later, no stationary fix at all

    (trip,) = h.trips()  # closed, not held for TRIP_SILENT_HOLD_MAX_S
    assert trip.end <= h.at(27.1)
    assert _record(h) == snapshot


def test_a_door_opened_mid_drive_ends_nothing(h):
    """A door read with the car rolling on afterwards is no arrival."""

    h.send(4, door=False)
    here = _drive(h, 5, 10)
    here = _arrive_with_the_door_open(h, 27, here)
    there = _drive(h, 28, 5, origin=here)  # moving again within a minute
    h.advance_to(40)
    assert h.trips() == []  # silent after minute 38: held, no stop evidence
    h.fix(45, *there)
    h.advance_to(70)

    (trip,) = h.trips()
    assert trip.end >= h.at(38)


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
        assert fresh.coordinator.gps_halves_pending(fresh.vin) == set()


# What makes the harness car a plug-in hybrid: fuel data beside its battery.
FUEL = {"vehicle.drivetrain.fuelSystem.level": 95}


def test_a_hybrid_drive_without_a_soc_reading_records_no_soc_end(h):
    """Issue #25's X3 30e never streams its state of charge.

    The SoC arrives only with a REST catch-up, so the reading from before the
    drive was still the only one at its end, and every trip was stored as
    "38 → 38 %": a battery that apparently never moved. With nothing read after
    the trip started, there is no end to report.
    """

    h.send(1, soc=38.0, extra=FUEL)
    here = _drive(h, 5, 5)
    h.fix(17, *here)  # parked
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.soc_start == 38.0
    assert trip.soc_end is None
    assert trip.soc_drop is None


def test_a_hybrid_drive_with_a_soc_reading_keeps_its_soc_end(h):
    """The counterpart: a hybrid that does send its SoC keeps the whole delta."""

    h.send(1, soc=72.0, extra=FUEL)
    here = _drive(h, 5, 5)
    h.send(16, soc=68.0)
    h.fix(17, *here)  # parked
    h.advance_to(40)

    (trip,) = h.trips()
    assert (trip.soc_start, trip.soc_end) == (72.0, 68.0)


def test_a_battery_car_hop_without_a_soc_reading_keeps_its_soc_end(h):
    """A battery car streams its SoC on change: silence means it didn't drop.

    About half the i5's trips are short hops like this, stored as "47 → 47 %".
    That is true, and the hybrid rule above must not blank them.
    """

    h.send(1, soc=47.0)
    here = _drive(h, 5, 5)
    h.fix(17, *here)  # parked
    h.advance_to(40)

    (trip,) = h.trips()
    assert (trip.soc_start, trip.soc_end) == (47.0, 47.0)


# --- a car that streams in snapshots (issue #183, an i4 M50) -----------------
#
# The i5 sends a fix every few seconds. The i4 sends its whole state about every
# three minutes while driving, one message per field, in a fixed order: the
# door, the SoC, the odometer -- and the position last. So every reading in a
# snapshot is already the new one when its fix lands.


def _snapshot(h: CoordinatorHarness, minute: float, here, *, odometer: float, soc: float, **values):
    h.send(minute, stamped=minute, odometer=odometer, soc=soc, **values)
    h.fix(minute + 1 / 60, *here)


def test_a_snapshot_car_counts_its_drive_from_where_it_was_parked(h):
    """The first moving fix arrives with the odometer three km on.

    Jerry's outbound trip: the odometer read 18138 parked and 18141 in the
    snapshot whose fix first showed the car moving. The trip started counting
    there and stored 12 km of a 15 km drive (7.5 mi against My BMW's 9.4), and
    its SoC one percent late.
    """

    _snapshot(h, 1, HOME, odometer=100, soc=70)  # parked at Home
    h.send(2, door=False)  # the driver got in
    here = HOME
    for i in range(1, 6):
        here = (HOME[0] + i * 0.02, HOME[1])  # ~2.2 km per snapshot
        _snapshot(h, 2 + i * 3, here, odometer=100 + i * 3, soc=70 - i)
    _snapshot(h, 20, here, odometer=115, soc=65)  # parked
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.distance_km == 15
    assert trip.soc_start == 70


def test_a_door_that_lands_before_the_final_fix_ends_nothing_early(h):
    """The arrival snapshot carries the door open *and* the last 2 km.

    Jerry's return journey: the door, sent before the position, was read as the
    driver getting out of a car that had last moved three minutes earlier, so
    the trip closed there -- and the fix one second later, 2 km on, opened a
    second trip. One drive was stored as 12 km and 2 km.
    """

    _snapshot(h, 1, HOME, odometer=100, soc=70)
    h.send(2, door=False)
    here = HOME
    for i in range(1, 6):
        here = (HOME[0] + i * 0.02, HOME[1])
        _snapshot(h, 2 + i * 3, here, odometer=100 + i * 3, soc=70 - i)
    there = (here[0] + 0.02, here[1])
    _snapshot(h, 20, there, odometer=117, soc=64, door=True)  # arrived
    _snapshot(h, 20 + 4 / 60, there, odometer=117, soc=64)  # standing
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.distance_km == 17
    assert trip.end <= h.at(20.1)


def test_jerrys_two_drives_replay_to_two_whole_trips():
    """The capture behind both tests above, end to end (issue #183).

    My BMW: 9.4 mi out, 8.4 mi back without a stop. The odometer says 15 km and
    14 km. Stored before the fix: 12 km, then 10 km + 2 km.
    """

    import json
    import pathlib
    from datetime import datetime

    path = pathlib.Path(__file__).parent / "replays" / "i4-two-drives.ndjson"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    start = datetime.fromisoformat(records[0]["at"])
    with CoordinatorHarness(capacity_kwh=None, start=start, vin=records[0]["vin"]) as i4:
        # Basic data says what the capture can't: a battery car, though it
        # streams a fuel level (see ``is_plug_in_hybrid``).
        i4.coordinator.device_metadata[i4.vin] = {"extra_attributes": {"drive_train": "BEV"}}
        i4.replay(records)
        i4.advance_to(i4.minute + 60)

        out, back = sorted(i4.trips(), key=lambda t: t.start)
    assert (out.distance_km, back.distance_km) == (15, 14)
    assert (out.soc_start, out.soc_end) == (67, 61)
    assert (back.soc_start, back.soc_end) == (61, 54)


# --- readings that land after the close ---------------------------------------


def _drive_with_odometer(h: CoordinatorHarness, start: float, fixes: int, *, odometer: float):
    """A drive at a fix a minute, the odometer one km on with every fix."""

    here = HOME
    for i in range(1, fixes + 1):
        here = (HOME[0] + i * 0.009, HOME[1])  # ~1 km
        h.send(start + i, odometer=odometer + i)
        h.fix(start + i + 2 / 60, *here)
    return here, odometer + fixes


def test_an_odometer_tick_after_the_close_still_counts(h):
    """The i5, 15 Oct: the last km arrived 52 s after the door ended the trip.

    The trip was already stored without it, and the next trip started from the
    new reading, so that kilometre belonged to no trip at all.
    """

    h.send(1, odometer=100, soc=70)
    h.fix(2, *HOME)  # parked
    h.send(3, door=False)
    here, odometer = _drive_with_odometer(h, 3, 10, odometer=100)
    h.send(14, door=True)  # arrived
    h.fix(14.5, *here)  # standing: the trip closes here
    h.send(15, odometer=odometer + 1, soc=63)  # the final km and SoC, late
    h.fix(15.5, *here)
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.distance_km == 11
    assert trip.soc_end == 63


def test_a_late_tick_with_no_fix_after_it_still_counts(h):
    """A car that goes to sleep after its last odometer tick sends no fix."""

    h.send(1, odometer=100)
    h.fix(2, *HOME)
    h.send(3, door=False)
    here, odometer = _drive_with_odometer(h, 3, 10, odometer=100)
    h.send(14, door=True)
    h.fix(14.5, *here)
    h.send(15, odometer=odometer + 1)  # and then silence
    h.advance_to(40)

    (trip,) = h.trips()
    assert trip.distance_km == 11


def test_a_quick_departure_is_not_a_late_tick(h):
    """A snapshot car leaving again: the odometer lands ahead of the moving fix.

    Those km are the new drive's, not a late reading of the one just closed.
    """

    _snapshot(h, 1, HOME, odometer=100, soc=70)
    h.send(2, door=False)
    here = HOME
    for i in range(1, 4):
        here = (HOME[0] + i * 0.02, HOME[1])
        _snapshot(h, 2 + i * 3, here, odometer=100 + i * 3, soc=70 - i)
    _snapshot(h, 12, here, odometer=109, soc=67, door=True)  # arrived
    _snapshot(h, 12 + 4 / 60, here, odometer=109, soc=67, door=False)
    h.advance_to(20)  # closed
    there = (here[0] + 0.02, here[1])
    _snapshot(h, 21, there, odometer=112, soc=66)  # off again
    _snapshot(h, 24, (there[0] + 0.02, there[1]), odometer=115, soc=65)
    _snapshot(h, 27, (there[0] + 0.02, there[1]), odometer=115, soc=65)  # parked
    h.advance_to(50)

    first, second = sorted(h.trips(), key=lambda t: t.start)
    assert first.distance_km == 9
    assert second.distance_km == 6


def test_a_door_opened_at_a_barrier_does_not_split_the_drive(h):
    """The driver reaches out at a car park barrier, then drives on.

    The door opens seconds after the car stopped and a fix shows it standing,
    but nothing yet says the drive is over.
    """

    h.send(4, door=False)
    here = _drive(h, 5, 10)
    h.send(25 + 3 / 60, door=True)  # three seconds after the last move
    h.fix(25 + 8 / 60, *here)  # standing at the barrier
    h.send(25 + 20 / 60, door=False)
    there = _drive(h, 26, 5, origin=here)
    h.fix(38, *there)  # parked
    h.advance_to(60)

    (trip,) = h.trips()
    assert trip.end >= h.at(36)


SEGMENT_DISTANCE = "vehicle.trip.segment.end.travelledDistance"


def test_a_stale_segment_distance_is_not_this_trips(h):
    """BMW's last trip distance, stamped before this trip began, is another trip's.

    A short hop that doesn't tick the odometer falls back to BMW's own segment
    distance -- which, if BMW hasn't sent this trip's yet, is the previous one.
    """

    h.send(1, odometer=100, extra={SEGMENT_DISTANCE: 25.0})  # last week's drive
    here = _drive(h, 5, 2, step=0.003)  # ~0.8 km
    h.fix(10, *here)  # parked
    h.advance_to(30)

    (trip,) = h.trips()
    assert trip.distance_km is not None and trip.distance_km < 2
