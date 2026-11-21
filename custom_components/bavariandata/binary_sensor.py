"""Binary sensor platform for BMW CarData.

Which binary sensors a car gets and what they show is decided in
``binary_sensor_plan.py``; this module builds the entities and listens.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from . import binary_sensor_plan
from .coordinator import CardataCoordinator
from .entity import CardataEntity
from .evcc import PLUG_DESCRIPTORS

PARALLEL_UPDATES = 0


class CardataBinarySensor(CardataEntity, BinarySensorEntity):
    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        self._attr_should_poll = False
        device_class, self._invert = binary_sensor_plan.device_class(descriptor)
        self._attr_device_class = (
            BinarySensorDeviceClass(device_class) if device_class is not None else None
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if getattr(self, "_attr_is_on", None) is None:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                self._attr_is_on = last_state.state.lower() == "on"
        self.async_on_remove(
            self._coordinator.router.subscribe(self.vin, (self.descriptor,), self._handle_update)
        )
        self._handle_update(self.vin, self.descriptor)

    def _handle_update(self, vin: str, descriptor: str) -> None:
        state = self._coordinator.get_state(vin, descriptor)
        if not state or not isinstance(state.value, bool):
            return
        # LOCK is polarity-inverted (on == unlocked); see binary_sensor_plan.
        self._attr_is_on = (not state.value) if self._invert else state.value

        self.schedule_update_ha_state()


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
    _attr_translation_key = binary_sensor_plan.TRIP_IN_PROGRESS

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, binary_sensor_plan.TRIP_IN_PROGRESS)
        self._attr_should_poll = False
        self._last_write = 0.0
        self._written_on = False

    @property
    def is_on(self) -> bool:
        return self._coordinator.has_open_trip(self.vin)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(super().extra_state_attributes)
        attrs.update(
            binary_sensor_plan.trip_attributes(self._coordinator.open_trip_progress(self.vin))
        )
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_trip_active, self._handle_trip_edge
            )
        )
        self.async_on_remove(
            self._coordinator.router.subscribe(self.vin, None, self._handle_stream)
        )
        self._written_on = self.is_on
        self._last_write = time.monotonic()

    def _handle_trip_edge(self, vin: str) -> None:
        """A trip opened or closed: write immediately, never throttled."""

        if vin == self.vin:
            self._write()

    def _handle_stream(self, vin: str, descriptor: str) -> None:
        """Refresh the in-progress figures, at most once per interval."""

        if binary_sensor_plan.trip_write_due(
            self.is_on, self._written_on, time.monotonic() - self._last_write
        ):
            self._write()

    def _write(self) -> None:
        self._written_on = self.is_on
        self._last_write = time.monotonic()
        self.schedule_update_ha_state()


class CardataChargingCableBinarySensor(CardataEntity, BinarySensorEntity):
    """Whether a cable is in the car: Home Assistant's standard plug sensor.

    What it reads, and why it exists, is in ``binary_sensor_plan``. Restored
    across a restart only until the car's plug state is known again.
    """

    _attr_should_poll = False
    _attr_translation_key = binary_sensor_plan.CHARGING_CABLE
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, binary_sensor_plan.CHARGING_CABLE)
        self._memory = binary_sensor_plan.CableMemory()
        self._heard = False

    @property
    def is_on(self) -> bool | None:
        return binary_sensor_plan.cable_shown(
            binary_sensor_plan.plugged(self._coordinator, self.vin), self._memory.on, self._heard
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(super().extra_state_attributes)
        attrs.update(
            binary_sensor_plan.cable_attributes(
                self._coordinator, self.vin, self._memory, self._heard
            )
        )
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in ("on", "off"):
            self._memory = binary_sensor_plan.CableMemory(
                on=last_state.state == "on",
                source=last_state.attributes.get("source"),
                timestamp=last_state.attributes.get("timestamp"),
            )
        self.async_on_remove(
            self._coordinator.router.subscribe(self.vin, PLUG_DESCRIPTORS, self._handle_update)
        )

    def _handle_update(self, _vin: str, _descriptor: str) -> None:
        self._heard = True
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: CardataCoordinator = entry.runtime_data.coordinator

    plan = binary_sensor_plan.BinarySensorPlan(
        coordinator,
        lambda vin, descriptor: async_add_entities(
            [CardataBinarySensor(coordinator, vin, descriptor)]
        ),
        lambda vin: async_add_entities([CardataTripInProgressBinarySensor(coordinator, vin)]),
        lambda vin: async_add_entities([CardataChargingCableBinarySensor(coordinator, vin)]),
    )

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity_entry.domain == "binary_sensor" and entity_entry.disabled_by is None:
            plan.restore(entity_entry.unique_id)

    plan.known(coordinator.iter_descriptors(binary=True), list(coordinator.data))

    async def async_handle_new(vin: str, descriptor: str) -> None:
        plan.new_descriptor(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_binary, async_handle_new)
    )

    async def async_handle_new_sensor(vin: str, descriptor: str) -> None:
        plan.new_sensor(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_sensor, async_handle_new_sensor)
    )

    async def async_handle_trip_active(vin: str) -> None:
        plan.trip_edge(vin, coordinator.has_open_trip(vin))

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_trip_active, async_handle_trip_active)
    )
