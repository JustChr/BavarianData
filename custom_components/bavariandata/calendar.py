"""Service calendar for BMW CarData vehicles: one calendar per car.

The events come from ``service_calendar.py`` (HA-free, unit-tested); this file
only hands them to Home Assistant. Disabled by default, like the other entities
from ``docs/roadmap.md`` (*Scheduled next*).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, Optional

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import CardataCoordinator
from .distance import car_distance_unit
from .entity import CardataEntity
from .service_calendar import CBS, ServiceEvent, lease_end_event, next_event, service_events

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

UNIQUE_SUFFIX = "_service_calendar"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one service calendar per car."""
    runtime_data = getattr(config_entry, "runtime_data", None)
    if not runtime_data:
        return

    coordinator: CardataCoordinator = runtime_data.coordinator
    calendars: Dict[str, CardataServiceCalendar] = {}

    def ensure_calendar(vin: str) -> None:
        if vin in calendars:
            return
        calendars[vin] = CardataServiceCalendar(coordinator, vin)
        async_add_entities([calendars[vin]])

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        if entity_entry.domain == "calendar" and entity_entry.unique_id.endswith(UNIQUE_SUFFIX):
            ensure_calendar(entity_entry.unique_id[: -len(UNIQUE_SUFFIX)])
    for vin in coordinator.data.keys():
        ensure_calendar(vin)

    async def handle_update(vin: str, descriptor: str) -> None:
        if descriptor == CBS:
            ensure_calendar(vin)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_update, handle_update)
    )


def _event(event: ServiceEvent) -> CalendarEvent:
    return CalendarEvent(
        start=event.start,
        end=event.end,
        summary=event.summary,
        description=event.description,
        uid=event.uid,
    )


class CardataServiceCalendar(CardataEntity, CalendarEntity):
    """The car's service items (brake fluid, inspection, ...) as all-day events."""

    _attr_translation_key = "service"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "service_calendar")
        self._attr_unique_id = f"{vin}{UNIQUE_SUFFIX}"
        self._attr_entity_registry_enabled_default = False
        self._unsubscribe = None
        self._unsubscribe_mileage = None

    def _events(self) -> list[ServiceEvent]:
        language = self.hass.config.language
        # The car's own unit, not just HA's unit system: a UK install is metric (#126).
        miles = car_distance_unit(self.hass, self._coordinator, self._vin) == "mi"
        events: list[ServiceEvent] = []
        state = self._coordinator.get_state(self._vin, CBS)
        if state is not None and state.value is not None:
            events = service_events(
                state.value,
                vin=self._vin,
                today=dt_util.now().date(),
                km_per_day=self._coordinator.km_per_day(self._vin),
                language=language,
                miles=miles,
            )
        # A lease's hand-back day belongs in the same calendar (Configure ->
        # Mileage allowance).
        lease_end = lease_end_event(
            self._coordinator.mileage_report(self._vin).get("allowance"),
            vin=self._vin,
            language=language,
            miles=miles,
        )
        if lease_end is not None:
            events.append(lease_end)
        return sorted(events, key=lambda event: (event.start, event.summary))

    @property
    def event(self) -> Optional[CalendarEvent]:
        upcoming = next_event(self._events(), dt_util.now().date())
        return _event(upcoming) if upcoming is not None else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        first: date = dt_util.as_local(start_date).date()
        last: date = dt_util.as_local(end_date).date()
        return [
            _event(event) for event in self._events() if event.end > first and event.start <= last
        ]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass, self._coordinator.signal_update, self._handle_update
        )
        self._unsubscribe_mileage = async_dispatcher_connect(
            self.hass, self._coordinator.signal_mileage, self._handle_mileage
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_mileage:
            self._unsubscribe_mileage()
            self._unsubscribe_mileage = None

    def _handle_update(self, vin: str, descriptor: str) -> None:
        if vin == self._vin and descriptor == CBS:
            self.schedule_update_ha_state()

    def _handle_mileage(self, vin: str) -> None:
        if vin == self._vin:
            self.schedule_update_ha_state()
