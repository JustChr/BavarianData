"""What a catalogue sensor becomes in Home Assistant: its classes and unit.

``descriptor_metadata`` says, per descriptor, which device class, state class and
unit its entity should carry. This module turns that into the decision the
sensor platform applies, and it is kept free of Home Assistant imports so the
test suite can check it against every descriptor in the catalogue -- the
platform itself cannot be imported here.

It exists because the decision used to live inline in ``CardataSensor``, where
the state class and the unit were only read inside the ``device class is set``
branch. Every metadata field looked independent and was not: taking the battery
device class off the fuel tank level (v0.9.10) silently took its state class
with it, and Home Assistant stopped its long-term statistics (issue #25). The
rules below are therefore stated one field at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class SensorClasses:
    """The metadata strings a sensor entity is given, before HA enum mapping."""

    device_class: Optional[str] = None
    state_class: Optional[str] = None
    # A unit here is pinned: BMW's runtime unit string must not override it.
    unit: Optional[str] = None
    # Non-empty only for an enum sensor.
    options: tuple[str, ...] = ()

    @property
    def is_enum(self) -> bool:
        return bool(self.options)


def sensor_classes(meta: Optional[Mapping[str, Any]]) -> SensorClasses:
    """Return the classes and pinned unit for a descriptor's metadata.

    - A device class wins over enum options (no descriptor has both today).
    - An enum sensor gets no state class and no unit: Home Assistant rejects
      either on ``SensorDeviceClass.ENUM``.
    - Otherwise the state class applies **whether or not** there is a device
      class -- a plain percentage is a measurement too.
    - The unit is pinned whenever the entity has a device class or a state
      class, since both tie Home Assistant to that unit (conversions, and the
      statistics' unit). A classless sensor keeps taking its unit off the
      stream, as it always has.
    """

    if not meta:
        return SensorClasses()
    device_class = meta.get("device_class") or None
    options = tuple(meta.get("options") or ())
    if device_class is None and options:
        return SensorClasses(options=options)
    state_class = meta.get("state_class") or None
    unit = meta.get("unit") or None
    if device_class is None and state_class is None:
        unit = None
    return SensorClasses(device_class=device_class, state_class=state_class, unit=unit)
