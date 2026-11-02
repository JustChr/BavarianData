"""A car's whole stream, shared on Home Assistant's MQTT broker.

BMW allows one stream per account, and BavarianData holds it -- so a
MagicMirror module, a Garmin widget or a bridge someone wrote against BMW's own
format would otherwise need a stream of their own and lose it to us (or take it
from us). Sharing republishes what BMW sends onto the broker Home Assistant is
already connected to, in two shapes:

- ``<prefix>/<vin>/stream``: each message as BMW sent it, never retained. A
  message holds only what just changed, so retaining it would serve a fragment
  as if it were the car.
- ``<prefix>/<vin>/stream/<field>``: one topic per field, BMW's own
  ``{"value", "unit", "timestamp"}``, retained by the bridge's setting so a
  dashboard that starts later finds the car waiting.

The position and the navigation destination are left out unless the user says
otherwise: coordinates leave the house only on purpose.

Home Assistant-free, like ``evcc.py``: what goes where is a pure function of a
message, unit-tested without an HA install. ``bridge.py`` publishes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from .evcc import BridgeConfig, normalize_prefix

# The topic level under ``<prefix>/<vin>/``. Stable: other apps quote it.
STREAM = "stream"

# Fields that say where the car is or is going. Prefixes rather than a list, so
# a field BMW adds under either (it has added ``fixStatus`` before) is kept out
# too instead of slipping through.
POSITION_PREFIXES = (
    "vehicle.cabin.infotainment.navigation.currentLocation.",
    "vehicle.cabin.infotainment.navigation.destinationSet.",
)

# A field name that would not make a usable topic level: the wildcards are
# refused by the broker, and a slash would nest the field under another.
_UNUSABLE = ("#", "+", "/", "\x00")


def is_position(name: str) -> bool:
    """Whether a field says where the car is or is going."""

    return name.startswith(POSITION_PREFIXES)


def stream_topic(prefix: Optional[str], vin: str) -> str:
    """Where a car's messages go, as BMW sent them."""

    return f"{normalize_prefix(prefix)}/{vin}/{STREAM}"


def sweep_filter(prefix: Optional[str]) -> str:
    """The subscription that finds every shared field still retained."""

    return f"{normalize_prefix(prefix)}/+/{STREAM}/#"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class SharedMessage:
    """One BMW message as it goes on the broker."""

    # ``(topic, payload)`` of the message itself, or ``None`` when nothing in it
    # may be shared.
    message: Optional[tuple[str, str]] = None
    # The per-field topics, topic -> payload.
    fields: dict[str, str] = field(default_factory=dict)


def shared_message(
    prefix: Optional[str], payload: Mapping[str, Any], *, hide_position: bool
) -> SharedMessage:
    """What one stream message becomes on the broker.

    The message keeps BMW's envelope as it came; only the fields left out (the
    position, when it is hidden) are missing from its ``data``. A message with
    nothing left publishes nothing, rather than an empty ``data``.
    """

    vin = payload.get("vin")
    data = payload.get("data")
    if not isinstance(vin, str) or not vin or not isinstance(data, Mapping):
        return SharedMessage()
    kept = {
        name: value
        for name, value in data.items()
        if isinstance(name, str)
        and name
        and isinstance(value, Mapping)
        and not (hide_position and is_position(name))
    }
    if not kept:
        return SharedMessage()
    base = stream_topic(prefix, vin)
    fields = {
        f"{base}/{name}": _dump(value)
        for name, value in kept.items()
        if not any(char in name for char in _UNUSABLE)
    }
    return SharedMessage(message=(base, _dump({**payload, "data": kept})), fields=fields)


def clears(
    topic: str,
    prefix: Optional[str],
    *,
    vins: Optional[Iterable[str]] = None,
    position_only: bool = False,
) -> bool:
    """Whether a retained topic the sweep found is one to delete.

    ``vins`` limits it to those cars, ``position_only`` to the fields
    :func:`is_position` names. Only ``<prefix>/<vin>/stream/<field>`` qualifies:
    the evcc topics beside it are the bridge's other half and are cleared by it.
    """

    base = f"{normalize_prefix(prefix)}/"
    if not topic.startswith(base):
        return False
    parts = topic[len(base) :].split("/", 2)
    if len(parts) != 3 or parts[1] != STREAM or not parts[0] or not parts[2]:
        return False
    vin, _, name = parts
    if vins is not None and vin not in set(vins):
        return False
    return not position_only or is_position(name)


@dataclass(frozen=True)
class Sweep:
    """One clean-up of the shared stream's retained topics."""

    prefix: str
    # Only these cars' topics, or every car's.
    vins: Optional[tuple[str, ...]] = None
    position_only: bool = False


def sweeps(before: BridgeConfig, after: BridgeConfig, vins: Iterable[str]) -> list[Sweep]:
    """What a change of settings leaves on the broker that has to go.

    Sharing switched off for every car, or moved to another prefix: everything
    under the old one. One car switched off: that car's. The position hidden
    again: every car's position fields, which would otherwise stay retained.
    """

    if not before.any_sharing:
        return []
    if before.prefix != after.prefix or not after.any_sharing:
        return [Sweep(before.prefix)]
    found: list[Sweep] = []
    known = dict.fromkeys([*vins, *before.stream_vehicles])
    stopped = tuple(vin for vin in known if before.shares(vin) and not after.shares(vin))
    if stopped:
        found.append(Sweep(after.prefix, vins=stopped))
    if after.hide_position and not before.hide_position:
        found.append(Sweep(after.prefix, position_only=True))
    return found
