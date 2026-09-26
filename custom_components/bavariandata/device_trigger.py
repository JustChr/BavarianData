"""Device triggers: "arrived at a zone", "left unlocked", "plugged in but not charging"...

The thin Home Assistant side. Which triggers a car gets, what counts as a
situation starting or ending, and the per-automation "for N minutes" all live in
``vehicle_triggers.py`` (HA-free, unit-tested); the coordinator fires the bus
events those rules produce (``test_scenarios_triggers.py``). This module only
maps a device to its VIN, listens, and hands the automation its trigger data --
the same ``trigger.data`` shape for every type.
"""

from __future__ import annotations

from typing import Any, Optional

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_FOR,
    CONF_PLATFORM,
    CONF_TYPE,
    CONF_ZONE,
)
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_CHARGING_COMPLETE,
    EVENT_CHARGING_INTERRUPTED,
    EVENT_CHARGING_STARTED,
    EVENT_SITUATION,
    EVENT_ZONE_ARRIVED,
    EVENT_ZONE_LEFT,
)
from .vehicle_triggers import (
    DEFAULT_DURATION_S,
    LOCK_DESCRIPTORS,
    SITUATIONS,
    TRIGGER_ARRIVED,
    TRIGGER_CHARGING_COMPLETE,
    TRIGGER_CHARGING_INTERRUPTED,
    TRIGGER_CHARGING_STARTED,
    TRIGGER_LEFT,
    TRIGGER_TYPES,
    SituationDelay,
    available_triggers,
    event_matches,
    lock_state,
)

# The bus event behind each point trigger. Situations all share EVENT_SITUATION.
_POINT_EVENTS = {
    TRIGGER_ARRIVED: EVENT_ZONE_ARRIVED,
    TRIGGER_LEFT: EVENT_ZONE_LEFT,
    TRIGGER_CHARGING_STARTED: EVENT_CHARGING_STARTED,
    TRIGGER_CHARGING_COMPLETE: EVENT_CHARGING_COMPLETE,
    TRIGGER_CHARGING_INTERRUPTED: EVENT_CHARGING_INTERRUPTED,
}
_ZONE_TRIGGERS = (TRIGGER_ARRIVED, TRIGGER_LEFT)

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_ZONE): cv.entity_domain("zone"),
        vol.Optional(CONF_FOR): cv.positive_time_period_dict,
    }
)


def _vin_for_device(hass: HomeAssistant, device_id: str) -> Optional[str]:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


def _coordinator_for_vin(hass: HomeAssistant, vin: str) -> Any:
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        if coordinator is not None and vin in coordinator.data:
            return coordinator
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """Only the triggers this car can actually fire."""

    vin = _vin_for_device(hass, device_id)
    coordinator = None if vin is None else _coordinator_for_vin(hass, vin)
    if coordinator is None:
        return []
    lock_values = {}
    for descriptor in LOCK_DESCRIPTORS:
        state = coordinator.get_state(vin, descriptor)
        if state is not None:
            lock_values[descriptor] = state.value
    types = available_triggers(
        coordinator.seen_descriptors(vin),
        lock_known=lock_state(lock_values) is not None,
        trips_enabled=coordinator.history is not None,
    )
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in types
    ]


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    trigger_type = config[CONF_TYPE]
    if trigger_type in _ZONE_TRIGGERS:
        return {
            "extra_fields": vol.Schema(
                {
                    vol.Optional(CONF_ZONE): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="zone")
                    )
                }
            )
        }
    if trigger_type in SITUATIONS:
        minutes = DEFAULT_DURATION_S[trigger_type] // 60
        return {
            "extra_fields": vol.Schema(
                {
                    vol.Optional(
                        CONF_FOR, default={"hours": 0, "minutes": minutes, "seconds": 0}
                    ): cv.positive_time_period_dict
                }
            )
        }
    return {}


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    trigger_type: str = config[CONF_TYPE]
    device_id: str = config[CONF_DEVICE_ID]
    vin = _vin_for_device(hass, device_id)
    zone: Optional[str] = config.get(CONF_ZONE)
    job = HassJob(action, f"{DOMAIN} device trigger {trigger_type}")
    trigger_data = trigger_info["trigger_data"]

    def _run(data: dict[str, Any], context: Any) -> None:
        hass.async_run_hass_job(
            job,
            {
                "trigger": {
                    **trigger_data,
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_TYPE: trigger_type,
                    "data": data,
                    "description": f"{DOMAIN} {trigger_type}",
                }
            },
            context,
        )

    if trigger_type not in SITUATIONS:

        @callback
        def _point(event: Event) -> None:
            if vin is not None and event_matches(event.data, vin=vin, zone=zone):
                _run(dict(event.data), event.context)

        return hass.bus.async_listen(_POINT_EVENTS[trigger_type], _point)

    duration = config.get(CONF_FOR)
    seconds = duration.total_seconds() if duration is not None else DEFAULT_DURATION_S[trigger_type]
    contexts: dict[str, Any] = {}

    def _call_later(delay: float, due) -> CALLBACK_TYPE:
        return async_call_later(hass, delay, callback(due))

    delay = SituationDelay(seconds, _call_later, lambda data: _run(data, contexts.get("start")))

    @callback
    def _situation(event: Event) -> None:
        if vin is None or not event_matches(event.data, vin=vin, situation=trigger_type):
            return
        if event.data.get("active"):
            contexts["start"] = event.context
        delay.update(bool(event.data.get("active")), dict(event.data))

    remove = hass.bus.async_listen(EVENT_SITUATION, _situation)

    @callback
    def _detach() -> None:
        remove()
        delay.cancel()

    return _detach
