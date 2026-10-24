"""The device-trigger events: situations, edges and leaving a zone.

Three kinds of event a user automates on, fired from what the stream says:

* **situations** that start and end -- parked unlocked, plugged in but not
  charging, parked with a window open (``EVENT_SITUATION``), judged on values
  the stream delivered since startup only, so a restart starts nothing until
  the car speaks again;
* **edges** of a reading -- the alarm, preconditioning, a Check Control
  message coming or going -- fired only on a fresh streamed value, since a
  REST answer may be BMW's cache and fills a gap at most;
* **leaving the zone** a trip started in, once per trip, on the first fix
  outside it.

The rules themselves are ``vehicle_triggers`` (HA-free, fully tested); this
reads the car through its host -- the coordinator -- and fires. A trigger
must never break the stream, so every entry point swallows and logs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol

from .car_profile import REST
from .car_state import Vehicle
from .const import (
    EVENT_ALARM,
    EVENT_CHECK_CONTROL,
    EVENT_PRECONDITIONING,
    EVENT_SITUATION,
    EVENT_ZONE_LEFT,
)
from .debug import debug_enabled, mask_vin
from .evcc import PLUG_DESCRIPTORS, plug_state
from .structured_values import structured_state
from .vehicle_triggers import (
    ALARM_DESCRIPTOR,
    CHECK_CONTROL_DESCRIPTOR,
    LOCK_DESCRIPTORS,
    PRECONDITIONING_DESCRIPTOR,
    PRECONDITIONING_ERROR_DESCRIPTOR,
    SITUATION_PARKED_UNLOCKED,
    SITUATION_PARKED_WINDOW_OPEN,
    SITUATION_PLUGGED_NOT_CHARGING,
    WINDOW_DESCRIPTORS,
    EdgeTracker,
    SituationTracker,
    alarm_on,
    fresh,
    lock_state,
    message_changes,
    open_windows,
    parked_unlocked,
    parked_window_open,
    plugged_not_charging,
    preconditioning_active,
    window_open,
)

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")

# The charging status: a situation only counts it once the stream sent it.
DESC_CHARGE_STATUS = "vehicle.drivetrain.electricEngine.charging.status"


class EventHost(Protocol):
    """What the events read and do, from the coordinator."""

    entry_id: str

    def _car(self, vin: str) -> Vehicle: ...
    def _peek(self, vin: str) -> Vehicle: ...
    def _value(self, vin: str, descriptor: str) -> Any: ...
    def _live_values(self, vin: str, descriptors: Iterable[str]) -> Dict[str, Any]: ...
    def _current_soc(self, vin: str) -> Optional[float]: ...
    def _current_zone(self, vin: str) -> Optional[tuple[str, str]]: ...
    def _zone_at(self, latitude: float, longitude: float) -> Optional[tuple[str, str]]: ...
    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None: ...


def _parse(value: Any) -> Optional[datetime]:
    """A BMW timestamp, or None when it isn't one with a timezone."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class VehicleEvents:
    """Fires the situation, edge and zone-left events of one account's cars."""

    def __init__(self, host: EventHost) -> None:
        self._host = host
        # Which situations stand, since when (``vehicle_triggers``).
        self._situations = SituationTracker()
        # The alarm, preconditioning and Check Control as last known: what an edge
        # event compares against (``vehicle_triggers.EdgeTracker``).
        self._edges = EdgeTracker()

    def evaluate_situations(self, vin: str, now: datetime) -> None:
        """Start or end the trigger situations, firing ``EVENT_SITUATION`` on a change.

        Judged on live values only (see ``Vehicle.live_descriptors``), so a restart can
        start nothing until the car speaks again.
        """

        try:
            tracking = self._host._peek(vin).charge.tracking
            charging: Optional[bool] = None
            if (
                tracking is not None
                and DESC_CHARGE_STATUS in self._host._peek(vin).live_descriptors
            ):
                charging = tracking.charging_active
            soc = self._host._current_soc(vin)
            target = None if tracking is None else tracking.target_soc_percent
            windows = self._host._live_values(vin, WINDOW_DESCRIPTORS)
            states = {
                SITUATION_PARKED_UNLOCKED: parked_unlocked(
                    lock_state(self._host._live_values(vin, LOCK_DESCRIPTORS)),
                    self._host._peek(vin).trip.builder is not None,
                ),
                SITUATION_PLUGGED_NOT_CHARGING: plugged_not_charging(
                    plug_state(self._host._live_values(vin, PLUG_DESCRIPTORS)),
                    charging,
                    soc,
                    target,
                ),
                SITUATION_PARKED_WINDOW_OPEN: parked_window_open(
                    window_open(windows),
                    self._host._peek(vin).trip.builder is not None,
                ),
            }
            for situation, active in states.items():
                began = self._situations.since(vin, situation)
                change = self._situations.update(vin, situation, active, now)
                if change is None:
                    continue
                zone = self._host._current_zone(vin)
                payload: Dict[str, Any] = {
                    "vin": vin,
                    "entry_id": self._host.entry_id,
                    "situation": situation,
                    "active": change,
                    "since": (now if change else began or now).isoformat(),
                    "zone": None if zone is None else zone[0],
                    "zone_entity_id": None if zone is None else zone[1],
                }
                if situation == SITUATION_PLUGGED_NOT_CHARGING:
                    payload["soc"] = None if soc is None else round(soc, 1)
                    payload["target_soc"] = target
                elif situation == SITUATION_PARKED_WINDOW_OPEN:
                    payload["windows"] = open_windows(windows) if change else []
                if debug_enabled():
                    _LOGGER.debug(
                        "[trigger] %s %s %s", vin, situation, "BEGIN" if change else "END"
                    )
                self._host._fire(EVENT_SITUATION, payload)
        except Exception:  # noqa: BLE001 - a trigger must never break the stream
            _LOGGER.exception("Trigger evaluation failed for %s", mask_vin(vin))

    def note_edges(
        self,
        vin: str,
        data: Mapping[str, Any],
        seen: Iterable[str],
        now: datetime,
        source: str,
    ) -> None:
        """Fire the alarm, preconditioning and Check Control events on a change.

        The alarm and preconditioning move only on the stream, and only fire on
        a value stamped fresh: a REST answer may be BMW's cache, and fills a gap
        at most (``EdgeTracker``). Check Control is REST-only, so its lists are
        compared whatever brought them; the first list only sets the start.
        """

        try:
            seen = set(seen)
            for descriptor in (ALARM_DESCRIPTOR, PRECONDITIONING_DESCRIPTOR):
                if descriptor not in seen:
                    continue
                reading = self._edge_reading(descriptor, self._host._value(vin, descriptor))
                if reading is None:
                    continue
                if source == REST:
                    self._edges.seed(vin, descriptor, reading)
                    continue
                before = self._edges.move(vin, descriptor, reading)
                stamp = (data.get(descriptor) or {}).get("timestamp")
                if before is reading or not fresh(_parse(stamp), now):
                    continue
                if not reading and before is None:
                    # An end with no start we knew of is nothing to announce.
                    continue
                if descriptor == ALARM_DESCRIPTOR:
                    self._fire_edge(EVENT_ALARM, vin, reading, {})
                else:
                    error = self._host._value(vin, PRECONDITIONING_ERROR_DESCRIPTOR)
                    activity = self._host._value(vin, PRECONDITIONING_DESCRIPTOR)
                    self._fire_edge(
                        EVENT_PRECONDITIONING,
                        vin,
                        reading,
                        {
                            "activity": activity.lower() if isinstance(activity, str) else None,
                            "error": error.lower() if isinstance(error, str) else None,
                            "soc": self._host._current_soc(vin),
                        },
                    )
            if CHECK_CONTROL_DESCRIPTOR in seen:
                _count, items = structured_state(self._host._value(vin, CHECK_CONTROL_DESCRIPTOR))
                if isinstance(items, list):
                    before = self._edges.move(vin, CHECK_CONTROL_DESCRIPTOR, items)
                    new, gone = message_changes(before, items) or ([], [])
                    for active, message in [(True, m) for m in new] + [(False, m) for m in gone]:
                        self._fire_edge(
                            EVENT_CHECK_CONTROL,
                            vin,
                            active,
                            {
                                "id": message.get("id"),
                                "title": message.get("title"),
                                "text": message.get("text"),
                                "message_type": message.get("messageType"),
                                "message": message,
                            },
                        )
        except Exception:  # noqa: BLE001 - a trigger must never break the stream
            _LOGGER.exception("Edge trigger evaluation failed for %s", mask_vin(vin))

    @staticmethod
    def _edge_reading(descriptor: str, value: Any) -> Optional[bool]:
        if descriptor == ALARM_DESCRIPTOR:
            return alarm_on(value)
        return preconditioning_active(value)

    def seed_edge(self, vin: str, descriptor: str, value: Any) -> None:
        """Give an edge its "before" from a restored value (``restore_descriptor_state``)."""

        if descriptor in (ALARM_DESCRIPTOR, PRECONDITIONING_DESCRIPTOR):
            self._edges.seed(vin, descriptor, self._edge_reading(descriptor, value))
        elif descriptor == CHECK_CONTROL_DESCRIPTOR:
            _count, items = structured_state(value)
            if isinstance(items, list):
                self._edges.seed(vin, descriptor, items)

    def _fire_edge(self, event_type: str, vin: str, active: bool, extra: Dict[str, Any]) -> None:
        zone = self._host._current_zone(vin)
        if debug_enabled():
            _LOGGER.debug("[trigger] %s %s %s", vin, event_type, "BEGIN" if active else "END")
        self._host._fire(
            event_type,
            {
                "vin": vin,
                "entry_id": self._host.entry_id,
                "active": active,
                **extra,
                "zone": None if zone is None else zone[0],
                "zone_entity_id": None if zone is None else zone[1],
            },
        )

    def fire_zone_left(self, vin: str, zone: tuple[str, str]) -> None:
        self._host._car(vin).trip.zone_left_fired = True
        builder = self._host._peek(vin).trip.builder
        self._host._fire(
            EVENT_ZONE_LEFT,
            {
                "vin": vin,
                "entry_id": self._host.entry_id,
                "zone": zone[0],
                "zone_entity_id": zone[1],
                "trip_start": None if builder is None else builder.start.isoformat(),
                "soc": self._host._current_soc(vin),
            },
        )

    def check_zone_left(self, vin: str, latitude: float, longitude: float) -> None:
        """Fire "left" once per trip, on the first fix outside the start zone."""

        start = self._host._peek(vin).trip.start_zone
        if start is None or self._host._peek(vin).trip.zone_left_fired:
            return
        here = self._host._zone_at(latitude, longitude)
        if here is not None and here[1] == start[1]:
            return
        self.fire_zone_left(vin, start)
