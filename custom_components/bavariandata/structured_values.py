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

Home Assistant-free on purpose, so the rule is unit-tested; ``sensor.py`` applies
it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple, Union

ITEMS_ATTRIBUTE = "items"

Structure = Union[list, dict]


def structured_state(value: Any) -> Tuple[Any, Optional[Structure]]:
    """Return ``(state, items)`` for a descriptor value.

    A list is shown as its length and exposed whole. A mapping has no count
    worth showing, so its state is ``None`` (unknown) but it is still exposed
    rather than dropped. Anything else is a plain value and passes through with
    no items.
    """

    if isinstance(value, list):
        return len(value), value
    if isinstance(value, dict):
        return None, value
    return value, None


def restored_items(attributes: Optional[Mapping[str, Any]]) -> Optional[Structure]:
    """The structure saved in a previous run's ``items`` attribute, if any."""

    if not attributes:
        return None
    items = attributes.get(ITEMS_ATTRIBUTE)
    return items if isinstance(items, (list, dict)) else None
