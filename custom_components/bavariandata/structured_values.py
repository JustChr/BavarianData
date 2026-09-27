"""Showing a list-valued descriptor as a sensor.

A few BMW descriptors carry a structure rather than a scalar:
``vehicle.status.conditionBasedServices`` is a list of service items (brake
fluid, vehicle check, statutory inspection, each with a due date) and
``vehicle.status.checkControlMessages`` a list of the warnings the car raised.
Handed to Home Assistant as a state, the list is stringified, runs past the
255-character state limit, and Home Assistant logs an error and records
``unknown`` instead -- so the service dates and the warnings never reached Home
Assistant at all, on every install, on every message.

The state becomes the number of entries, which always fits and can drive an
automation ("a Check Control message appeared"); the structure itself rides
along as the ``items`` attribute, and is what gets restored after a restart --
the count alone could not give the list back, and these descriptors arrive
rarely.

The structure usually arrives as **JSON text**, not a decoded list: that is what
the live i5 delivered, and it is also what Home Assistant restores after a restart
from a run that stored the rejected string as the native value. Text that parses
to a list or mapping is treated as that structure; any other string is a plain
value.

**A cleared Check Control list arrives as null, not as ``[]``.** The car uploads
its messages at drive start, and once the last one is gone BMW's REST reply
carries the key with value, unit and timestamp all null -- measured on the i5 on
2026-09-27, a day after its washer fluid was topped up. Skipped like any other
null, it left the old warning showing for good; :data:`EMPTY_WHEN_NULL` names the
descriptors for which null means "none".

Messages that leave the list are kept for a while as the ``resolved`` attribute
(:class:`MessageHistory`), so the card can show what the car complained about
without showing it as current.

Home Assistant-free on purpose, so the rules are unit-tested; ``sensor.py`` and
the coordinator apply them.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Tuple, Union

ITEMS_ATTRIBUTE = "items"
RESOLVED_ATTRIBUTE = "resolved"

CHECK_CONTROL = "vehicle.status.checkControlMessages"

# Descriptors whose null value is an empty list. Only Check Control: it is
# REST-only, so a null can only be BMW's answer, never a stream gap.
EMPTY_WHEN_NULL: frozenset[str] = frozenset({CHECK_CONTROL})

# How many resolved messages the attribute keeps, newest first.
RESOLVED_LIMIT = 10

Structure = Union[list, dict]


def structured_state(value: Any) -> Tuple[Any, Optional[Structure]]:
    """Return ``(state, items)`` for a descriptor value.

    A list is shown as its length and exposed whole. A mapping has no count
    worth showing, so its state is ``None`` (unknown) but it is still exposed
    rather than dropped. A string holding a JSON list or object counts as that
    structure. Anything else is a plain value and passes through with no items.
    """

    value = _decoded(value)
    if isinstance(value, list):
        return len(value), value
    if isinstance(value, dict):
        return None, value
    return value, None


def _decoded(value: Any) -> Any:
    """``value`` decoded when it is JSON text for a list or mapping, else as-is."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        parsed = json.loads(text)
    except ValueError:
        return value
    return parsed if isinstance(parsed, (list, dict)) else value


def restored_items(attributes: Optional[Mapping[str, Any]]) -> Optional[Structure]:
    """The structure saved in a previous run's ``items`` attribute, if any."""

    if not attributes:
        return None
    items = attributes.get(ITEMS_ATTRIBUTE)
    return items if isinstance(items, (list, dict)) else None


def _message_key(item: Mapping[str, Any]) -> tuple:
    """What makes two list entries the same message: its type and id, or text."""

    ident = item.get("id")
    return (item.get("messageType"), ident if ident is not None else item.get("text"))


def _messages(items: Any) -> list[dict]:
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


class MessageHistory:
    """The messages a list sensor showed, and the ones that have since gone.

    ``update`` is handed every list the sensor shows. A message in the previous
    list but not in the new one is resolved: it moves to :attr:`resolved` with
    ``last_reported`` (the timestamp of the last list that held it) and
    ``resolved_at`` (when the list without it reached us -- BMW gives a cleared
    list no timestamp of its own). A message that comes back leaves the history,
    so it is never shown as current and past at once.
    """

    def __init__(self) -> None:
        self._shown: Optional[list[dict]] = None
        self._shown_at: Optional[str] = None
        self.resolved: list[dict] = []

    def restore(self, attributes: Optional[Mapping[str, Any]]) -> None:
        """Pick up where the previous run stopped: its list and its history."""

        if not attributes:
            return
        if isinstance(attributes.get(ITEMS_ATTRIBUTE), list):
            self._shown = _messages(attributes[ITEMS_ATTRIBUTE])
            self._shown_at = attributes.get("timestamp")
        self.resolved = _messages(attributes.get(RESOLVED_ATTRIBUTE))[:RESOLVED_LIMIT]

    def update(self, items: Any, reported_at: Optional[str], now: str) -> None:
        """Record the list now shown; ``items`` that are not a list are ignored."""

        if not isinstance(items, list):
            return
        current = _messages(items)
        if self._shown is not None:
            active = {_message_key(item) for item in current}
            gone = [item for item in self._shown if _message_key(item) not in active]
            kept = [item for item in self.resolved if _message_key(item) not in active]
            fresh = [{**item, "last_reported": self._shown_at, "resolved_at": now} for item in gone]
            self.resolved = (fresh + kept)[:RESOLVED_LIMIT]
        self._shown = current
        self._shown_at = reported_at
