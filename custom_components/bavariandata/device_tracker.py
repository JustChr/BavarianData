"""Device tracker for BMW CarData vehicles."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CardataCoordinator
from .entity import CardataEntity

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

LOCATION_DESCRIPTORS = (
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
    "vehicle.trip.segment.end.vehicleLocation.gpsPosition.latitude",
    "vehicle.trip.segment.end.vehicleLocation.gpsPosition.longitude",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BMW CarData tracker from config entry."""
    runtime_data = getattr(config_entry, "runtime_data", None)
    if not runtime_data:
        return

    coordinator: CardataCoordinator = runtime_data.coordinator
    trackers: dict[str, CardataDeviceTracker] = {}

    def ensure_tracker(vin: str) -> None:
        if vin in trackers:
            return
        tracker = CardataDeviceTracker(coordinator, vin)
        trackers[vin] = tracker
        async_add_entities([tracker])
        _LOGGER.debug("Created device tracker for VIN: %s", vin)

    # Re-create trackers the registry already knows about, rather than waiting
    # for BMW to stream a location again -- which can take hours. Without this a
    # tracker restored after a restart stays an unavailable ghost until the next
    # GPS push; the entity has to exist for RestoreEntity to seed its position.
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        if entity_entry.disabled_by is not None:
            continue
        if entity_entry.domain == "device_tracker" and entity_entry.unique_id.endswith("_tracker"):
            ensure_tracker(entity_entry.unique_id[: -len("_tracker")])

    for vin in coordinator.data.keys():
        ensure_tracker(vin)

    def handle_location(vin: str, _descriptor: str) -> None:
        ensure_tracker(vin)

    # Any car's first fix creates its tracker.
    config_entry.async_on_unload(
        coordinator.router.subscribe(None, LOCATION_DESCRIPTORS, handle_location)
    )


class CardataDeviceTracker(CardataEntity, TrackerEntity):
    """BMW CarData device tracker."""

    _attr_force_update = False
    _attr_translation_key = "car"
    _attr_name = None

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, vin, "device_tracker")
        self._attr_unique_id = f"{vin}_tracker"

    async def async_added_to_hass(self) -> None:
        """Handle entity added to Home Assistant."""
        await super().async_added_to_hass()
        await self._async_restore_location()
        self.async_on_remove(
            self._coordinator.router.subscribe(self.vin, LOCATION_DESCRIPTORS, self._handle_update)
        )

    async def _async_restore_location(self) -> None:
        """Show where the car was last left instead of "unknown" after a restart.

        BMW only streams a location occasionally, so without this the map point
        would blank on every restart until the next GPS push (potentially hours).
        Seed the coordinator so the ``latitude``/``longitude`` properties resolve
        the restored position unchanged, and only until live data supersedes it.
        """
        lat_desc, lon_desc = LOCATION_DESCRIPTORS[0], LOCATION_DESCRIPTORS[1]
        if self._coordinator.get_state(self._vin, lat_desc) is not None:
            return  # A live location already arrived; nothing to restore.
        last_state = await self.async_get_last_state()
        if not last_state or last_state.state in ("unknown", "unavailable"):
            return
        lat = last_state.attributes.get("latitude")
        lon = last_state.attributes.get("longitude")
        if lat is None or lon is None:
            return
        timestamp = last_state.last_changed.isoformat() if last_state.last_changed else None
        self._coordinator.restore_descriptor_state(self._vin, lat_desc, lat, None, timestamp)
        self._coordinator.restore_descriptor_state(self._vin, lon_desc, lon, None, timestamp)

    def _handle_update(self, _vin: str, _descriptor: str) -> None:
        """A new fix for this car (the router hands it only those)."""
        self.schedule_update_ha_state()

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device."""
        return SourceType.GPS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return self._basic_data_attributes()

    def _fetch_coordinate(self, descriptor: str) -> float | None:
        state = self._coordinator.get_state(self._vin, descriptor)
        if state and state.value is not None:
            try:
                return float(state.value)
            except ValueError, TypeError:
                _LOGGER.debug(
                    "Unable to parse coordinate for %s from descriptor %s: %s",
                    self._vin,
                    descriptor,
                    state.value,
                )
        return None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._fetch_coordinate(
            "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
        )

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._fetch_coordinate(
            "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
        )
