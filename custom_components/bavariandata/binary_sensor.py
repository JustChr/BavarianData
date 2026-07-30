"""Binary sensor platform for BMW CarData."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_registry as er

from .coordinator import CardataCoordinator
from .entity import CardataEntity


def _binary_device_class(
    descriptor: str,
) -> tuple[BinarySensorDeviceClass | None, bool]:
    """Return ``(device_class, invert)`` for a boolean descriptor.

    A binary sensor with no device class always renders as plain on/off. BMW
    reports the raw boolean as ``True`` == the open / plugged / moving / locked
    state, so most classes need no inversion. The exception is HA's ``LOCK``
    class, whose polarity is inverted (``on`` == *unlocked*): lock booleans are
    inverted here so the UI reads Locked / Unlocked correctly.
    """

    d = descriptor.lower()
    if d.endswith(".islocked"):
        return BinarySensorDeviceClass.LOCK, True
    if d.endswith(".isplugged"):
        return BinarySensorDeviceClass.PLUG, False
    if d.endswith(".ismoving"):
        return BinarySensorDeviceClass.MOVING, False
    if d.endswith(("engine.isactive", ".isignitionon")):
        return BinarySensorDeviceClass.RUNNING, False
    if d.endswith(".ismobilephoneconnected"):
        return BinarySensorDeviceClass.CONNECTIVITY, False
    if d.endswith(".isopen"):
        if ".window." in d:
            return BinarySensorDeviceClass.WINDOW, False
        return BinarySensorDeviceClass.OPENING, False
    return None, False


class CardataBinarySensor(CardataEntity, BinarySensorEntity):
    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        self._attr_should_poll = False
        self._unsubscribe = None
        self._attr_device_class, self._invert = _binary_device_class(descriptor)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if getattr(self, "_attr_is_on", None) is None:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                self._attr_is_on = last_state.state.lower() == "on"
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_update,
            self._handle_update,
        )
        self._handle_update(self.vin, self.descriptor)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str, descriptor: str) -> None:
        if vin != self.vin or descriptor != self.descriptor:
            return
        state = self._coordinator.get_state(vin, descriptor)
        if not state or not isinstance(state.value, bool):
            return
        # LOCK is polarity-inverted (on == unlocked); see _binary_device_class.
        self._attr_is_on = (not state.value) if self._invert else state.value

        self.schedule_update_ha_state()


# How often the in-progress figures (distance, duration, SoC) are rewritten while
# a drive is under way. The stream delivers a fix every few seconds, and writing
# each one would put hundreds of rows per drive in the recorder for numbers nobody
# reads that precisely. The open/closed edges are never throttled, and a frontend
# can tick the elapsed time itself from ``started``.
TRIP_PROGRESS_WRITE_INTERVAL_S = 60


class CardataTripInProgressBinarySensor(CardataEntity, BinarySensorEntity):
    """Whether a drive is under way, with the trip so far as attributes.

    Deliberately **not** ``BinarySensorDeviceClass.MOVING``. What this knows is
    that a trip is *open*, which is not the same claim as the car being in motion
    right now: detection opens a trip on the first GPS fix that reads as movement
    and closes it a debounce after the last one -- longer while the position
    stream is silent, which is what parking underground looks like. Calling that
    "moving" would be wrong at both ends of every drive, and would invite
    automations to trust it as a motion signal. ``last_movement`` and ``held``
    are attributes so anyone who needs the finer question can ask it.

    Nothing is restored across a restart: the in-flight trip lives only in the
    coordinator's memory and is flushed on unload, so a restarted install
    genuinely has no drive under way, and restoring ``on`` would strand it there.
    """

    _attr_should_poll = False
    _attr_icon = "mdi:steering"
    _attr_translation_key = "trip_in_progress"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "trip_in_progress")
        self._attr_should_poll = False
        self._unsub_active = None
        self._unsub_update = None
        self._last_write = 0.0
        self._written_on = False

    @property
    def is_on(self) -> bool:
        return self._coordinator.has_open_trip(self.vin)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        progress: Optional[Dict[str, Any]] = self._coordinator.open_trip_progress(
            self.vin
        )
        if progress is None:
            # Parked: carry no figures at all rather than the last drive's, which
            # would go on reading like a live trip.
            return attrs
        start_place = progress.get("start_place") or {}
        attrs["started"] = progress.get("start")
        attrs["start_location"] = start_place.get("label")
        attrs["distance_km"] = progress.get("distance_km")
        attrs["duration_s"] = progress.get("duration_s")
        attrs["soc_start"] = progress.get("soc_start")
        attrs["soc_now"] = progress.get("soc_end")
        attrs["energy_kwh"] = progress.get("energy_kwh")
        attrs["last_movement"] = progress.get("last_movement")
        attrs["held"] = progress.get("held")
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_active = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_trip_active,
            self._handle_trip_edge,
        )
        self._unsub_update = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_update,
            self._handle_stream,
        )
        self._written_on = self.is_on
        self._last_write = time.monotonic()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        for attr in ("_unsub_active", "_unsub_update"):
            unsub = getattr(self, attr)
            if unsub:
                unsub()
                setattr(self, attr, None)

    def _handle_trip_edge(self, vin: str) -> None:
        """A trip opened or closed: write immediately, never throttled."""

        if vin == self.vin:
            self._write()

    def _handle_stream(self, vin: str, descriptor: str) -> None:
        """Refresh the in-progress figures, at most once per interval."""

        if vin != self.vin:
            return
        on = self.is_on
        if on == self._written_on:
            if not on:
                return  # parked and known to be parked: nothing to refresh
            if time.monotonic() - self._last_write < TRIP_PROGRESS_WRITE_INTERVAL_S:
                return
        self._write()

    def _write(self) -> None:
        self._written_on = self.is_on
        self._last_write = time.monotonic()
        self.schedule_update_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = entry.runtime_data
    coordinator: CardataCoordinator = runtime.coordinator

    entities: Dict[Tuple[str, str], CardataBinarySensor] = {}
    trip_entities: Dict[str, CardataTripInProgressBinarySensor] = {}

    def ensure_trip_entity(vin: str) -> None:
        if vin in trip_entities:
            return
        entity = CardataTripInProgressBinarySensor(coordinator, vin)
        trip_entities[vin] = entity
        async_add_entities([entity])

    def ensure_entity(vin: str, descriptor: str, *, assume_binary: bool = False) -> None:
        if (vin, descriptor) in entities:
            return
        state = coordinator.get_state(vin, descriptor)
        if state:
            if not isinstance(state.value, bool):
                return
        elif not assume_binary:
            return
        entity = CardataBinarySensor(coordinator, vin, descriptor)
        entities[(vin, descriptor)] = entity
        async_add_entities([entity])

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.domain != "binary_sensor":
            continue
        if entity_entry.disabled_by is not None:
            continue
        unique_id = entity_entry.unique_id
        if not unique_id or "_" not in unique_id:
            continue
        vin, descriptor = unique_id.split("_", 1)
        if descriptor == "trip_in_progress":
            # Derived entity, not a BMW descriptor: re-create it as itself rather
            # than letting the generic path mint a CardataBinarySensor on its id.
            ensure_trip_entity(vin)
            continue
        ensure_entity(vin, descriptor, assume_binary=True)

    for vin, descriptor in coordinator.iter_descriptors(binary=True):
        ensure_entity(vin, descriptor)

    for vin in list(coordinator.data.keys()):
        ensure_trip_entity(vin)

    async def async_handle_new(vin: str, descriptor: str) -> None:
        # A vehicle can turn up after setup (its first message arrives late), so
        # the trip flag is ensured here too rather than only at startup.
        ensure_trip_entity(vin)
        ensure_entity(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_binary, async_handle_new)
    )

    async def async_handle_trip_active(vin: str) -> None:
        # A vehicle whose first data arrived after setup gets its flag on the trip
        # that revealed it, instead of waiting for the next restart. Only on the
        # opening edge: the closing edge also fires while unloading (open trips are
        # flushed before the platforms come down), and adding an entity then would
        # race the teardown.
        if coordinator.has_open_trip(vin):
            ensure_trip_entity(vin)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, coordinator.signal_trip_active, async_handle_trip_active
        )
    )
