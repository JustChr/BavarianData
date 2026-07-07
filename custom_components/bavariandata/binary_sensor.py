"""Binary sensor platform for BMW CarData."""

from __future__ import annotations

from typing import Dict, Tuple

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
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
    if d.endswith("engine.isactive") or d.endswith(".isignitionon"):
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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: CardataCoordinator = runtime.coordinator

    entities: Dict[Tuple[str, str], CardataBinarySensor] = {}

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
        ensure_entity(vin, descriptor, assume_binary=True)

    for vin, descriptor in coordinator.iter_descriptors(binary=True):
        ensure_entity(vin, descriptor)

    async def async_handle_new(vin: str, descriptor: str) -> None:
        ensure_entity(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_binary, async_handle_new)
    )
