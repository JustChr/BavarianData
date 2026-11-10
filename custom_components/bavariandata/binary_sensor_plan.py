"""Which binary sensors each car gets, and what they show, apart from Home Assistant.

``binary_sensor.py`` builds the entities and listens; the rules are here, where
the test suite runs them (``tests/test_binary_sensor_plan.py``): which boolean
descriptor becomes a sensor, which device class it gets and whether its polarity
flips, what the trip-in-progress sensor carries, and how often it is rewritten.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional, Protocol

from .coverage import is_ev_only_descriptor
from .evcc import PLUG_DESCRIPTORS, plug_source, plug_state

TRIP_IN_PROGRESS = "trip_in_progress"

# Whether a cable is in the car, as Home Assistant's standard ``plug`` binary
# sensor. Derived rather than streamed: no car we know sends the ``isPlugged``
# boolean that would have made one on its own, while every electrified car sends
# ``chargingPort.status``, an enum whose name is translated in every language.
# Other integrations and automations look for a plug-class binary sensor to learn
# "cable in" (SpotNav#2), so each car gets one, read by the same rule as the evcc
# bridge, Assist and the charging tracker (``evcc.plug_state``).
CHARGING_CABLE = "charging_cable"

# How often the in-progress figures (distance, duration, SoC) are rewritten while
# a drive is under way. The stream delivers a fix every few seconds, and writing
# each one would put hundreds of rows per drive in the recorder for numbers nobody
# reads that precisely. The open/closed edges are never throttled, and a frontend
# can tick the elapsed time itself from ``started``.
TRIP_PROGRESS_WRITE_INTERVAL_S = 60


def device_class(descriptor: str) -> tuple[Optional[str], bool]:
    """Return ``(device_class, invert)`` for a boolean descriptor.

    The device class is Home Assistant's ``BinarySensorDeviceClass`` value. A
    binary sensor with no device class always renders as plain on/off. BMW
    reports the raw boolean as ``True`` == the open / plugged / moving / locked
    state, so most classes need no inversion. The exception is HA's ``lock``
    class, whose polarity is inverted (``on`` == *unlocked*): lock booleans are
    inverted here so the UI reads Locked / Unlocked correctly.
    """

    d = descriptor.lower()
    if d.endswith(".islocked"):
        return "lock", True
    if d.endswith(".isplugged"):
        return "plug", False
    if d.endswith(".ismoving"):
        return "moving", False
    if d.endswith(("engine.isactive", ".isignitionon")):
        return "running", False
    if d.endswith(".ismobilephoneconnected"):
        return "connectivity", False
    if d.endswith(".isopen"):
        if ".window." in d:
            return "window", False
        return "opening", False
    return None, False


def trip_attributes(progress: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The drive so far, as the trip-in-progress sensor's attributes.

    Parked (``None``): no figures at all rather than the last drive's, which
    would go on reading like a live trip.
    """

    if progress is None:
        return {}
    start_place = progress.get("start_place") or {}
    return {
        "started": progress.get("start"),
        "start_location": start_place.get("label"),
        "distance_km": progress.get("distance_km"),
        "duration_s": progress.get("duration_s"),
        "soc_start": progress.get("soc_start"),
        "soc_now": progress.get("soc_end"),
        "energy_kwh": progress.get("energy_kwh"),
        "last_movement": progress.get("last_movement"),
        "held": progress.get("held"),
    }


def trip_write_due(on: bool, written_on: bool, since_write_s: float) -> bool:
    """Whether a stream message should rewrite the trip-in-progress sensor.

    Always when the open/closed state differs from what was last written; never
    while parked and known to be parked; during a drive, once per interval.
    """

    if on != written_on:
        return True
    return on and since_write_s >= TRIP_PROGRESS_WRITE_INTERVAL_S


def plugged(host: "BinarySensorHost", vin: str) -> Optional[bool]:
    """Whether a cable is in the car now, or ``None`` if no source answers.

    ``INVALID`` and the like are no answer: they fall through to the next source,
    and with none left the sensor reads unknown, never unplugged.
    """

    return plug_state(_plug_values(host, vin))


def _plug_values(host: "BinarySensorHost", vin: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for descriptor in PLUG_DESCRIPTORS:
        state = host.get_state(vin, descriptor)
        if state is not None:
            values[descriptor] = state.value
    return values


class CableMemory(NamedTuple):
    """The cable sensor's own last state from before a restart, with its attributes."""

    on: Optional[bool] = None
    source: Optional[str] = None
    timestamp: Optional[str] = None


def cable_attributes(
    host: "BinarySensorHost", vin: str, memory: CableMemory, heard: bool
) -> dict[str, Any]:
    """When the car measured what the cable sensor shows, and whether that is confirmed.

    ``source`` is the plug descriptor that decided the state and ``timestamp``
    BMW's time for it. A port state restored after a restart keeps the time the
    car measured it, so a consumer can tell an old "plugged in" from a new one
    (#317). While the sensor shows its own last state, both come from that state.

    ``from_before_restart`` is true while the shown state comes from before the
    restart (the sensor's own memory, or a port value restored with its entity)
    and no plug reading has replaced it. BMW does not resend an unchanged value,
    so it can stay true for hours. Never name it ``restored``: that is Home
    Assistant's own attribute for an entity its integration no longer provides
    (the frontend then offers to delete it, and the recorder drops it).
    """

    values = _plug_values(host, vin)
    source = plug_source(values)
    if source is not None:
        state = host.get_state(vin, source)
        return {
            "source": source,
            "timestamp": getattr(state, "timestamp", None) or None,
            "from_before_restart": bool(getattr(state, "restored", False)),
        }
    if cable_shown(None, memory.on, heard) is not None:
        return {"source": memory.source, "timestamp": memory.timestamp, "from_before_restart": True}
    return {"source": None, "timestamp": None, "from_before_restart": False}


def cable_shown(current: Optional[bool], restored: Optional[bool], heard: bool) -> Optional[bool]:
    """What the charging-cable sensor shows.

    The car's plug state, once the integration holds one. Until then -- right
    after a restart, before the port sensor's own restore or the stream has
    supplied a value -- the sensor's last state from before the restart. Once a
    plug reading has arrived, that reading decides, including "no answer".
    """

    if current is not None or heard:
        return current
    return restored


class BinarySensorHost(Protocol):
    def get_state(self, vin: str, descriptor: str) -> Any: ...

    def is_combustion_only(self, vin: str) -> bool: ...

    def seen_descriptors(self, vin: str) -> set[str]: ...


class BinarySensorPlan:
    """The binary sensors one account has made, and which ones each car still needs.

    ``add_descriptor`` / ``add_trip`` / ``add_cable`` make the entity and hand
    it to Home Assistant; the plan only decides and remembers.
    """

    def __init__(
        self,
        host: BinarySensorHost,
        add_descriptor: Callable[[str, str], None],
        add_trip: Callable[[str], None],
        add_cable: Callable[[str], None],
    ) -> None:
        self._host = host
        self._add_descriptor = add_descriptor
        self._add_trip = add_trip
        self._add_cable = add_cable
        self._descriptors: set[tuple[str, str]] = set()
        self._trips: set[str] = set()
        self._cables: set[str] = set()

    def trip(self, vin: str) -> None:
        if vin in self._trips:
            return
        self._trips.add(vin)
        self._add_trip(vin)

    def cable(self, vin: str, *, restored: bool = False) -> None:
        """The charging-cable sensor, for a car that has ever said whether a cable is in.

        ``seen_descriptors`` remembers arrivals across restarts, so a car that
        reported its port before this sensor existed gets it at the first start
        after updating. A petrol car gets none, even from the registry.
        """

        if vin in self._cables or self._host.is_combustion_only(vin):
            return
        if not restored and not (self._host.seen_descriptors(vin) & set(PLUG_DESCRIPTORS)):
            return
        self._cables.add(vin)
        self._add_cable(vin)

    def new_sensor(self, vin: str, descriptor: str) -> None:
        """A car's first enum or number reading: the port status is one."""

        if descriptor in PLUG_DESCRIPTORS:
            self.cable(vin)

    def descriptor(self, vin: str, descriptor: str, *, assume_binary: bool = False) -> None:
        if (vin, descriptor) in self._descriptors:
            return
        if is_ev_only_descriptor(descriptor) and self._host.is_combustion_only(vin):
            # A charging port or plug on a car with no traction battery.
            return
        state = self._host.get_state(vin, descriptor)
        if state:
            if not isinstance(state.value, bool):
                return
        elif not assume_binary:
            return
        self._descriptors.add((vin, descriptor))
        self._add_descriptor(vin, descriptor)

    def restore(self, unique_id: Optional[str]) -> None:
        """Re-create a binary sensor the registry holds as what it was."""

        if not unique_id or "_" not in unique_id:
            return
        vin, descriptor = unique_id.split("_", 1)
        if descriptor == TRIP_IN_PROGRESS:
            # Derived, not a BMW descriptor: re-created as itself rather than
            # letting the generic path mint a descriptor sensor on its id.
            self.trip(vin)
        elif descriptor == CHARGING_CABLE:
            self.cable(vin, restored=True)
        else:
            self.descriptor(vin, descriptor, assume_binary=True)

    def known(self, descriptors: Iterable[tuple[str, str]], vins: Iterable[str]) -> None:
        """Everything the coordinator already holds when the platform starts."""

        for vin, descriptor in descriptors:
            self.descriptor(vin, descriptor)
        for vin in vins:
            self.trip(vin)
            self.cable(vin)

    def new_descriptor(self, vin: str, descriptor: str) -> None:
        # A vehicle can turn up after setup (its first message arrives late), so
        # the trip flag is ensured here too rather than only at startup.
        self.trip(vin)
        self.descriptor(vin, descriptor)
        self.new_sensor(vin, descriptor)

    def trip_edge(self, vin: str, open_trip: bool) -> None:
        # A vehicle whose first data arrived after setup gets its flag on the trip
        # that revealed it, instead of waiting for the next restart. Only on the
        # opening edge: the closing edge also fires while unloading (open trips
        # are flushed before the platforms come down), and adding an entity then
        # would race the teardown.
        if open_trip:
            self.trip(vin)
