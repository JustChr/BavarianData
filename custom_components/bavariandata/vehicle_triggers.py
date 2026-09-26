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
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional

from .evcc import PLUG_DESCRIPTORS

__all__ = [
    "CHARGE_DESCRIPTORS",
    "DEFAULT_DURATION_S",
    "GPS_DESCRIPTORS",
    "LOCK_DESCRIPTORS",
    "SITUATIONS",
    "SITUATION_PARKED_UNLOCKED",
    "SITUATION_PLUGGED_NOT_CHARGING",
    "TARGET_TOLERANCE",
    "TRIGGER_ARRIVED",
    "TRIGGER_CHARGING_COMPLETE",
    "TRIGGER_CHARGING_INTERRUPTED",
    "TRIGGER_CHARGING_STARTED",
    "TRIGGER_LEFT",
    "TRIGGER_TYPES",
    "SituationDelay",
    "SituationTracker",
    "available_triggers",
    "charging_interrupted",
    "event_matches",
    "lock_state",
    "parked_unlocked",
    "plugged_not_charging",
]

# The two situations, which are also trigger types.
SITUATION_PARKED_UNLOCKED = "parked_unlocked"
SITUATION_PLUGGED_NOT_CHARGING = "plugged_not_charging"
SITUATIONS = (SITUATION_PARKED_UNLOCKED, SITUATION_PLUGGED_NOT_CHARGING)

# Point triggers: each is one bus event, fired once.
TRIGGER_ARRIVED = "zone_arrived"
TRIGGER_LEFT = "zone_left"
TRIGGER_CHARGING_STARTED = "charging_started"
TRIGGER_CHARGING_COMPLETE = "charging_complete"
TRIGGER_CHARGING_INTERRUPTED = "charging_interrupted"

TRIGGER_TYPES = (
    TRIGGER_ARRIVED,
    TRIGGER_LEFT,
    SITUATION_PARKED_UNLOCKED,
    SITUATION_PLUGGED_NOT_CHARGING,
    TRIGGER_CHARGING_STARTED,
    TRIGGER_CHARGING_COMPLETE,
    TRIGGER_CHARGING_INTERRUPTED,
)

# How long a situation must last when an automation doesn't say. Unlocked: well
# past the car's own ~2 min relock. Plugged in: past BMW's "initialization" and a
# charge controller's start-up delay.
DEFAULT_DURATION_S = {
    SITUATION_PARKED_UNLOCKED: 10 * 60,
    SITUATION_PLUGGED_NOT_CHARGING: 15 * 60,
}

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

_LOCKED_WORDS = frozenset({"LOCKED", "SECURED"})
_UNLOCKED_WORDS = frozenset({"UNLOCKED", "SELECTIVELOCKED"})


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
    if lock_known:
        offered.append(SITUATION_PARKED_UNLOCKED)
    if any(d in seen for d in CHARGE_DESCRIPTORS):
        offered += [TRIGGER_CHARGING_STARTED, TRIGGER_CHARGING_COMPLETE]
        if any(d in seen for d in PLUG_DESCRIPTORS):
            offered += [SITUATION_PLUGGED_NOT_CHARGING, TRIGGER_CHARGING_INTERRUPTED]
    return offered


def event_matches(
    data: Mapping[str, Any],
    *,
    vin: str,
    situation: Optional[str] = None,
    zone: Optional[str] = None,
) -> bool:
    """Whether a bus event's data belongs to this trigger.

    ``zone`` is a zone entity id; ``None`` means any zone.
    """

    if data.get("vin") != vin:
        return False
    if situation is not None and data.get("situation") != situation:
        return False
    if zone is not None and data.get("zone_entity_id") != zone:
        return False
    return True


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
