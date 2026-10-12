"""Which side of the car the driver sits on.

BMW names the side doors, windows and seats by *seat*, not by side:
``vehicle.cabin.door.row1.driver.isOpen`` is the driver's door wherever the
steering wheel is. On a left-hand-drive car that is the left door; on a
right-hand-drive one (UK, Ireland, Japan, Australia, ...) it is the right one.
Anything that places those descriptors on a picture of the car has to know
which, and basic data's ``steering`` field says so.

BMW spells it with its long-standing shorthand: ``LL`` (*Linkslenker*) for
left-hand drive, ``RL`` (*Rechtslenker*) for right-hand drive, both seen on real
cars. Anything else is unknown, which callers treat as left-hand drive, the way
they always have; the card's ``steering:`` option covers a car that says
nothing.

Home Assistant-free so the matching is unit-tested.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

__all__ = ["steering_side"]

_SIDES: dict[str, Literal["left", "right"]] = {"LL": "left", "RL": "right"}


def steering_side(value: Any) -> Optional[Literal["left", "right"]]:
    """``"left"`` or ``"right"`` for basic data's ``steering``, else ``None``."""

    return _SIDES.get(value) if isinstance(value, str) else None
