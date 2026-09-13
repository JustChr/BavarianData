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

Home Assistant-free on purpose, so the rule is unit-tested; ``sensor.py`` applies
it.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Tuple, Union

ITEMS_ATTRIBUTE = "items"

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
