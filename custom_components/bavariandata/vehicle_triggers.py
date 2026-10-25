"""The rules behind the device triggers: when a car situation starts, ends, fires.

Home Assistant-free, so every rule is unit-testable (``tests/test_vehicle_triggers.py``)
and the coordinator can run it under the fake-HA harness. ``device_trigger.py`` is
the thin HA wrapper around it; the coordinator feeds it and fires the bus events.

Raw car data makes poor automation triggers: BMW's stream is noisy, silent for
hours, and repeats old values, and after a restart every entity comes back from
storage looking current. So the triggers here are built on three rules:

* **Live values only.** A situation starts only from values the stream delivered
  since Home Assistant started, never from a restored state -- a car locked while
  HA was down must not raise "left unlocked" on boot. A missed firing is
  recoverable; a false alarm at 3 a.m. teaches people to delete the automation.
* **Situations, not samples.** "Left unlocked" is a state with a start and an
  end, not an event: the car unlocks for a few seconds at every arrival and
  relocks itself after about two minutes if no door opens (measured on an i5:
  median 39 s, 90 % under 2.4 min). The coordinator reports when the situation
  begins and ends; each automation chooses how long it has to last
  (:class:`SituationDelay`).
* **Say "not known", don't guess.** A car that never streamed a lock word has no
  lock state, and none of these triggers are offered for it
  (:func:`available_triggers`).

The alarm and preconditioning triggers are *edges*: a change from the value
before. Their "before" is kept by :class:`EdgeTracker`, seeded from the restored
state and from REST answers (which may come from BMW's cache, hours old) but moved
only by the stream, and a stream value only counts while its own timestamp is
fresh (:data:`EDGE_FRESH_S`), so a replay of yesterday's alarm is not one now.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional

from .evcc import PLUG_DESCRIPTORS
from .structured_values import CHECK_CONTROL as CHECK_CONTROL_DESCRIPTOR

__all__ = [
    "ALARM_DESCRIPTOR",
    "CHARGE_DESCRIPTORS",
    "CHECK_CONTROL_DESCRIPTOR",
    "DEFAULT_DURATION_S",
    "EDGE_FRESH_S",
    "GPS_DESCRIPTORS",
    "LOCK_DESCRIPTORS",
    "PRECONDITIONING_DESCRIPTOR",
    "PRECONDITIONING_ERROR_DESCRIPTOR",
    "SITUATIONS",
    "SITUATION_PARKED_UNLOCKED",
    "SITUATION_PARKED_WINDOW_OPEN",
    "SITUATION_PLUGGED_NOT_CHARGING",
    "TARGET_TOLERANCE",
    "TRIGGER_ALARM",
    "TRIGGER_ARRIVED",
    "TRIGGER_CHARGING_COMPLETE",
    "TRIGGER_CHARGING_INTERRUPTED",
    "TRIGGER_CHARGING_STARTED",
    "TRIGGER_CHECK_CONTROL",
    "TRIGGER_LEFT",
    "TRIGGER_PRECONDITIONING_FINISHED",
    "TRIGGER_PRECONDITIONING_STARTED",
    "TRIGGER_TRIP_ENDED",
    "TRIGGER_TYPES",
    "TRIP_DESCRIPTORS",
    "WINDOW_DESCRIPTORS",
    "EdgeTracker",
    "SituationDelay",
    "SituationTracker",
    "alarm_on",
    "available_triggers",
    "charging_interrupted",
    "event_matches",
    "fresh",
    "lock_state",
    "message_changes",
    "open_windows",
    "parked_unlocked",
    "parked_window_open",
    "plugged_not_charging",
    "preconditioning_active",
    "window_open",
]

# The situations, which are also trigger types.
SITUATION_PARKED_UNLOCKED = "parked_unlocked"
SITUATION_PLUGGED_NOT_CHARGING = "plugged_not_charging"
SITUATION_PARKED_WINDOW_OPEN = "parked_window_open"
SITUATIONS = (
    SITUATION_PARKED_UNLOCKED,
    SITUATION_PLUGGED_NOT_CHARGING,
    SITUATION_PARKED_WINDOW_OPEN,
)

# Point triggers: each is one bus event, fired once.
TRIGGER_ARRIVED = "zone_arrived"
TRIGGER_LEFT = "zone_left"
TRIGGER_TRIP_ENDED = "trip_ended"
TRIGGER_CHARGING_STARTED = "charging_started"
TRIGGER_CHARGING_COMPLETE = "charging_complete"
TRIGGER_CHARGING_INTERRUPTED = "charging_interrupted"
TRIGGER_ALARM = "alarm"
TRIGGER_CHECK_CONTROL = "check_control"
TRIGGER_PRECONDITIONING_STARTED = "preconditioning_started"
TRIGGER_PRECONDITIONING_FINISHED = "preconditioning_finished"

TRIGGER_TYPES = (
    TRIGGER_ARRIVED,
    TRIGGER_LEFT,
    TRIGGER_TRIP_ENDED,
    SITUATION_PARKED_UNLOCKED,
    SITUATION_PARKED_WINDOW_OPEN,
    SITUATION_PLUGGED_NOT_CHARGING,
    TRIGGER_CHARGING_STARTED,
    TRIGGER_CHARGING_COMPLETE,
    TRIGGER_CHARGING_INTERRUPTED,
    TRIGGER_PRECONDITIONING_STARTED,
    TRIGGER_PRECONDITIONING_FINISHED,
    TRIGGER_ALARM,
    TRIGGER_CHECK_CONTROL,
)

# How long a situation must last when an automation doesn't say. Unlocked: well
# past the car's own ~2 min relock. Plugged in: past BMW's "initialization" and a
# charge controller's start-up delay. A window: past airing the car out on arrival.
DEFAULT_DURATION_S = {
    SITUATION_PARKED_UNLOCKED: 10 * 60,
    SITUATION_PLUGGED_NOT_CHARGING: 15 * 60,
    SITUATION_PARKED_WINDOW_OPEN: 10 * 60,
}

# A stream value older than this (by its own timestamp) moves an edge's "before"
# but fires nothing: it is the car repeating the past, not saying what happens.
EDGE_FRESH_S = 10 * 60

# A charge within this many percent of its target counts as having reached it --
# the same tolerance as the ``charging_complete`` event.
TARGET_TOLERANCE = 1.0

# The lock state, best source first. ``cabin.door.status`` is the one that
# streams (the i5 sends LOCKED/SECURED/UNLOCKED/SELECTIVE_LOCKED within seconds);
# ``cabin.door.lock.status`` is REST-only and a fallback. On some cars (a MINI)
# ``cabin.door.status`` carries open/closed words instead -- those are not lock
# words and read as "unknown", which is the point of :func:`lock_state`.
LOCK_DESCRIPTORS = (
    "vehicle.cabin.door.status",
    "vehicle.cabin.door.lock.status",
)
GPS_DESCRIPTORS = (
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
)
CHARGE_DESCRIPTORS = ("vehicle.drivetrain.electricEngine.charging.status",)
# Any of these makes trips: a position, the motion flag, or (on a car that only
# reports once parked) the odometer.
TRIP_DESCRIPTORS = (
    *GPS_DESCRIPTORS,
    "vehicle.isMoving",
    "vehicle.vehicle.mileage",
    "vehicle.vehicle.travelledDistance",
)

# Each opening and the name it goes by in an event's ``windows``. BMW says
# OPEN / INTERMEDIATE / CLOSED for the windows and the sunroof (its tilt too);
# the rear window is a plain flag.
WINDOW_DESCRIPTORS = {
    "vehicle.cabin.window.row1.driver.status": "front_driver",
    "vehicle.cabin.window.row1.passenger.status": "front_passenger",
    "vehicle.cabin.window.row2.driver.status": "rear_driver",
    "vehicle.cabin.window.row2.passenger.status": "rear_passenger",
    "vehicle.cabin.sunroof.status": "sunroof",
    "vehicle.cabin.sunroof.tiltStatus": "sunroof_tilt",
    "vehicle.body.trunk.window.isOpen": "rear_window",
}

ALARM_DESCRIPTOR = "vehicle.vehicle.antiTheftAlarmSystem.alarm.isOn"
PRECONDITIONING_DESCRIPTOR = "vehicle.vehicle.preConditioning.activity"
PRECONDITIONING_ERROR_DESCRIPTOR = "vehicle.vehicle.preConditioning.error"

_LOCKED_WORDS = frozenset({"LOCKED", "SECURED"})
_UNLOCKED_WORDS = frozenset({"UNLOCKED", "SELECTIVELOCKED"})
# A partly open window lets the rain in as well as a fully open one.
_OPEN_WORDS = frozenset({"OPEN", "INTERMEDIATE"})
_CLOSED_WORDS = frozenset({"CLOSED"})
# The i5 sends HEATING and INACTIVE; the rest are BMW's other climate words. A
# word in neither set says nothing and leaves the state where it was.
_CONDITIONING_WORDS = frozenset({"HEATING", "COOLING", "VENTILATION", "ACTIVE"})
_IDLE_WORDS = frozenset({"INACTIVE", "STANDBY"})


def lock_state(values: Mapping[str, Any]) -> Optional[bool]:
    """``True`` locked, ``False`` not (fully) locked, ``None`` when the car doesn't say.

    Case- and punctuation-insensitive (``SELECTIVE_LOCKED``, ``selective-Locked``
    and a restored ``selectivelocked`` are one word), and tolerant of BMW's
    ``ASN_`` prefix. *Selectively* locked -- only some doors -- counts as unlocked:
    the question this answers is "can someone open the car".
    """

    for descriptor in LOCK_DESCRIPTORS:
        word = _lock_word(values.get(descriptor))
        if word in _LOCKED_WORDS:
            return True
        if word in _UNLOCKED_WORDS:
            return False
    return None


def _lock_word(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        # The structured form: {"allDoorsLocked": "ASN_isTrue", ...}.
        flag = _lock_word(value.get("allDoorsLocked"))
        if flag == "ISTRUE":
            return "LOCKED"
        if flag == "ISFALSE":
            return "UNLOCKED"
        return _lock_word(value.get("newDoorStatus"))
    return _word(value)


def _word(value: Any) -> Optional[str]:
    """A BMW word, compared loosely: case, punctuation and ``ASN_`` don't matter."""

    if not isinstance(value, str):
        return None
    word = value.strip().upper()
    if word.startswith("ASN_"):
        word = word[4:]
    return re.sub(r"[^A-Z]", "", word) or None


def parked_unlocked(locked: Optional[bool], trip_open: bool) -> Optional[bool]:
    """Whether the car stands unlocked. ``None`` when the lock state is unknown."""

    if locked is None:
        return None
    return not locked and not trip_open


def _opening_open(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    word = _word(value)
    if word in _OPEN_WORDS:
        return True
    if word in _CLOSED_WORDS:
        return False
    return None


def open_windows(values: Mapping[str, Any]) -> list[str]:
    """The openings standing open (or partly), by their names in ``WINDOW_DESCRIPTORS``."""

    return [
        name
        for descriptor, name in WINDOW_DESCRIPTORS.items()
        if _opening_open(values.get(descriptor)) is True
    ]


def window_open(values: Mapping[str, Any]) -> Optional[bool]:
    """Whether any window or the sunroof is open. ``None`` when the car says of none.

    One opening the car reports as closed is enough to answer ``False``: a car
    without a sunroof never says anything about one.
    """

    states = [_opening_open(values.get(descriptor)) for descriptor in WINDOW_DESCRIPTORS]
    if any(state is True for state in states):
        return True
    if any(state is False for state in states):
        return False
    return None


def parked_window_open(opened: Optional[bool], trip_open: bool) -> Optional[bool]:
    """Whether the car stands with a window open. ``None`` when no window is known."""

    if opened is None:
        return None
    return opened and not trip_open


def alarm_on(value: Any) -> Optional[bool]:
    """Whether the anti-theft alarm sounds. ``None`` for anything but a flag."""

    if isinstance(value, bool):
        return value
    word = _word(value)
    if word in ("TRUE", "ON"):
        return True
    if word in ("FALSE", "OFF"):
        return False
    return None


def preconditioning_active(value: Any) -> Optional[bool]:
    """Whether the car is heating or cooling itself. ``None`` for an unknown word."""

    word = _word(value)
    if word in _CONDITIONING_WORDS:
        return True
    if word in _IDLE_WORDS:
        return False
    return None


def fresh(stamp: Optional[datetime], now: datetime) -> bool:
    """Whether a value stamped ``stamp`` speaks of now. No stamp counts as now."""

    return stamp is None or (now - stamp).total_seconds() <= EDGE_FRESH_S


def _message_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    # The same identity as the sensor's history (structured_values.MessageHistory).
    ident = item.get("id")
    return (item.get("messageType"), ident if ident is not None else item.get("text"))


def message_changes(
    before: Any, after: Any
) -> Optional[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """``(new, gone)`` Check Control messages between two lists.

    ``None`` when either side is not a list: with nothing to compare against,
    every message would look new -- the first list only sets the starting point.
    """

    if not isinstance(before, list) or not isinstance(after, list):
        return None
    old = [item for item in before if isinstance(item, Mapping)]
    now = [item for item in after if isinstance(item, Mapping)]
    old_keys = {_message_key(item) for item in old}
    now_keys = {_message_key(item) for item in now}
    new = [dict(item) for item in now if _message_key(item) not in old_keys]
    gone = [dict(item) for item in old if _message_key(item) not in now_keys]
    return new, gone


def plugged_not_charging(
    plugged: Optional[bool],
    charging: Optional[bool],
    soc: Optional[float],
    target: Optional[float],
) -> Optional[bool]:
    """Whether a cable is in, no charge is running, and the car still wants one.

    ``None`` when the plug or the charging state is unknown. A car at its target
    that has stopped is done, not stuck, so it never counts.
    """

    if plugged is None or charging is None:
        return None
    if not plugged or charging:
        return False
    return not _at_target(soc, target)


def charging_interrupted(
    soc: Optional[float], target: Optional[float], plugged: Optional[bool]
) -> bool:
    """Whether a charge that just ended stopped short, with the cable still in.

    Deliberately strict: an unplug before the target is the driver's choice,
    and without a known SoC *and* target there is no "short" to speak of.
    """

    if plugged is not True or soc is None or target is None:
        return False
    return not _at_target(soc, target)


def _at_target(soc: Optional[float], target: Optional[float]) -> bool:
    return soc is not None and target is not None and soc >= target - TARGET_TOLERANCE


def available_triggers(
    seen: Iterable[str],
    *,
    lock_known: bool,
    trips_enabled: bool,
) -> list[str]:
    """The trigger types worth offering for a car, from what it has streamed.

    A trigger whose source the car never sent would sit in the editor and never
    fire. ``seen`` is every descriptor the car has delivered (live or restored --
    this is about what the car *can* send, not what it said lately).
    """

    seen = set(seen)
    offered: list[str] = []
    if trips_enabled and any(d in seen for d in GPS_DESCRIPTORS):
        offered += [TRIGGER_ARRIVED, TRIGGER_LEFT]
    if trips_enabled and any(d in seen for d in TRIP_DESCRIPTORS):
        offered.append(TRIGGER_TRIP_ENDED)
    if lock_known:
        offered.append(SITUATION_PARKED_UNLOCKED)
    if any(d in seen for d in WINDOW_DESCRIPTORS):
        offered.append(SITUATION_PARKED_WINDOW_OPEN)
    if any(d in seen for d in CHARGE_DESCRIPTORS):
        offered += [TRIGGER_CHARGING_STARTED, TRIGGER_CHARGING_COMPLETE]
        if any(d in seen for d in PLUG_DESCRIPTORS):
            offered += [SITUATION_PLUGGED_NOT_CHARGING, TRIGGER_CHARGING_INTERRUPTED]
    if PRECONDITIONING_DESCRIPTOR in seen:
        offered += [TRIGGER_PRECONDITIONING_STARTED, TRIGGER_PRECONDITIONING_FINISHED]
    if ALARM_DESCRIPTOR in seen:
        offered.append(TRIGGER_ALARM)
    if CHECK_CONTROL_DESCRIPTOR in seen:
        offered.append(TRIGGER_CHECK_CONTROL)
    return offered


def event_matches(
    data: Mapping[str, Any],
    *,
    vin: str,
    situation: Optional[str] = None,
    zone: Optional[str] = None,
    active: Optional[bool] = None,
) -> bool:
    """Whether a bus event's data belongs to this trigger.

    ``zone`` is a zone entity id; ``None`` means any zone. ``active`` picks one
    side of an event that fires on a start and an end alike.
    """

    if data.get("vin") != vin:
        return False
    if situation is not None and data.get("situation") != situation:
        return False
    if zone is not None and data.get("zone_entity_id") != zone:
        return False
    if active is not None and data.get("active") is not active:
        return False
    return True


class EdgeTracker:
    """The value each edge was last at, per vehicle -- the "before" of a change.

    :meth:`seed` (a restored state, a REST answer) only fills a gap: either may be
    hours old, and must not overwrite what the stream said since. :meth:`move` is
    the stream's and returns the value it replaces, ``None`` if there was none.
    """

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], Any] = {}

    def seed(self, vin: str, key: str, value: Any) -> None:
        if value is not None:
            self._last.setdefault((vin, key), value)

    def move(self, vin: str, key: str, value: Any) -> Any:
        previous = self._last.get((vin, key))
        if value is not None:
            self._last[(vin, key)] = value
        return previous


class SituationTracker:
    """When each situation began, per vehicle. Pure bookkeeping, no timers."""

    def __init__(self) -> None:
        self._since: dict[tuple[str, str], datetime] = {}

    def update(
        self, vin: str, situation: str, active: Optional[bool], now: datetime
    ) -> Optional[bool]:
        """Record the current truth; return ``True``/``False`` on a start/end.

        ``None`` (unknown) ends a running situation: once we can no longer say
        the car is unlocked, we must not go on claiming it.
        """

        key = (vin, situation)
        running = key in self._since
        if active and not running:
            self._since[key] = now
            return True
        if not active and running:
            del self._since[key]
            return False
        return None

    def since(self, vin: str, situation: str) -> Optional[datetime]:
        return self._since.get((vin, situation))

    def active(self, vin: str) -> list[str]:
        return [situation for (v, situation) in self._since if v == vin]


class SituationDelay:
    """One automation's "for N minutes": fire once if a situation lasts that long.

    ``call_later(seconds, callback)`` schedules and returns a cancel function
    (HA's ``async_call_later`` in production, a fake clock in tests). A new start
    while one is pending restarts nothing -- the situation is still the same one.
    """

    def __init__(
        self,
        seconds: float,
        call_later: Callable[[float, Callable[[Any], None]], Callable[[], None]],
        fire: Callable[[Mapping[str, Any]], None],
    ) -> None:
        self._seconds = seconds
        self._call_later = call_later
        self._fire = fire
        self._cancel: Optional[Callable[[], None]] = None

    @property
    def pending(self) -> bool:
        return self._cancel is not None

    def update(self, active: bool, data: Mapping[str, Any]) -> None:
        if not active:
            self.cancel()
            return
        if self._cancel is not None:
            return
        payload = dict(data)

        def _due(_now: Any = None) -> None:
            self._cancel = None
            self._fire(payload)

        self._cancel = self._call_later(self._seconds, _due)

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
