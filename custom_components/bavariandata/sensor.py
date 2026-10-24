"""Sensor platform for BMW CarData."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.const import UnitOfLength

from .const import DOMAIN, REQUEST_LIMIT
from .car_signals import DESC_ODOMETER, EFFICIENCY_LIVE_DESCRIPTORS
from .coordinator import CardataCoordinator
from .descriptor_metadata import DESCRIPTOR_META, SECTIONS
from .entity import CardataEntity
from .history.health import degradation_series, usable_capacity
from .history.summary import (
    driving_summary,
    sessions_in_month,
    summarise,
    trips_in_month,
)
from .placeholders import is_placeholder
from .restore_units import restore_native
from . import sensor_plan, sensor_values
from .units import entity_unit
from .sensor_classes import sensor_classes
from .structured_values import (
    CHECK_CONTROL,
    ITEMS_ATTRIBUTE,
    RESOLVED_ATTRIBUTE,
    MessageHistory,
    restored_items,
    structured_state,
)


# String metadata values -> Home Assistant sensor enums.
_DEVICE_CLASS_MAP = {
    "battery": SensorDeviceClass.BATTERY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "energy_storage": SensorDeviceClass.ENERGY_STORAGE,
    "distance": SensorDeviceClass.DISTANCE,
    "speed": SensorDeviceClass.SPEED,
    "pressure": SensorDeviceClass.PRESSURE,
    "duration": SensorDeviceClass.DURATION,
    "volume": SensorDeviceClass.VOLUME,
    "volume_storage": SensorDeviceClass.VOLUME_STORAGE,
}
_STATE_CLASS_MAP = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

ACCOUNT_DEVICE_NAME = "BMW CarData"


def _account_device_info(entry_id: str) -> DeviceInfo:
    """The account's own device, holding its diagnostics.

    "BMW CarData", the service it stands for, in every language: named after
    the cars it read like one of them. The entry above it carries the cars'
    names, which is what tells two accounts apart.
    """

    return {
        "identifiers": {(DOMAIN, entry_id)},
        "manufacturer": "BMW",
        "name": ACCOUNT_DEVICE_NAME,
    }


class CardataRestoreSensor(CardataEntity, RestoreSensor):
    """A sensor that gets its own value back after a restart -- in native units.

    ``RestoreSensor`` (rather than plain ``RestoreEntity``) because Home
    Assistant's saved *state* is the value as displayed, and a display unit is
    not ours to choose: the unit system picks one for imperial installs, and any
    user can pick one per entity. Restoring that number as if it were native
    re-applies the conversion on every restart -- see ``restore_units``, which
    also carries the one-shot fallback for upgrading from a build that never
    saved native data.
    """

    async def async_restored_native(self) -> Tuple[Any, Optional[str], Any]:
        """Return ``(value, unit, last_state)`` from the previous run.

        ``value`` is ``None`` when there is nothing trustworthy to restore --
        no saved state, or one whose unit says it has been converted. Callers
        get ``last_state`` regardless so they can read the attributes they
        stored alongside it (timestamps, ``last_reset``), but must only use
        those when ``value`` is not ``None``.
        """

        last_state = await self.async_get_last_state()
        native_unit = self.native_unit_of_measurement

        # Preferred: what this class saves for itself -- the native value and
        # the unit it was actually measured in, with nothing to infer.
        sensor_data = await self.async_get_last_sensor_data()
        if sensor_data is not None and sensor_data.native_value is not None:
            stored_value = sensor_data.native_value
            stored_unit = sensor_data.native_unit_of_measurement
        elif last_state is not None:
            # Upgrading from a build that saved no native data: all that exists
            # is the state as displayed, and its unit is the only evidence of
            # whether it was converted on the way out.
            stored_value = last_state.state
            stored_unit = last_state.attributes.get("unit_of_measurement")
        else:
            return None, None, None

        # Both sources go through the same rule. Saved native data is normally a
        # match, but the catalogue's unit for a descriptor can change under it --
        # and a value silently relabelled into a new unit is the same bug again.
        value, unit = restore_native(stored_value, stored_unit, native_unit)
        if value is None and str(stored_value) not in ("unknown", "unavailable"):
            _LOGGER.debug(
                "%s: not restoring %r -- stored as %s, native unit is %s; "
                "waiting for a fresh reading instead",
                self.descriptor,
                stored_value,
                stored_unit,
                native_unit,
            )
        return value, unit, last_state


class CardataSensor(CardataRestoreSensor):
    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        self._attr_should_poll = False
        self._unsubscribe = None
        # Check Control keeps the messages that have since cleared, so the card
        # can list them apart from the current ones (see structured_values).
        self._history = MessageHistory() if descriptor == CHECK_CONTROL else None
        # ``True`` when the catalogue pins the unit/device class, so runtime unit
        # strings from BMW (e.g. "percent") must not override it.
        self._fixed_unit = False
        self._is_enum = False

        meta = DESCRIPTOR_META.get(descriptor)
        if meta:
            # Which classes and unit apply is decided in sensor_classes, where
            # the test suite can check it for every descriptor (issue #25).
            classes = sensor_classes(meta)
            if classes.is_enum:
                # Enum sensor: translated states come from the translation key.
                self._attr_device_class = SensorDeviceClass.ENUM
                self._attr_options = list(classes.options)
                self._is_enum = True
            else:
                self._attr_state_class = _STATE_CLASS_MAP.get(classes.state_class)
                if classes.unit:
                    self._attr_native_unit_of_measurement = classes.unit
                    self._fixed_unit = True
            device_class = _DEVICE_CLASS_MAP.get(classes.device_class)
            if device_class is not None:
                self._attr_device_class = device_class
                if classes.whole_numbers:
                    self._attr_suggested_display_precision = 0
        elif self._descriptor == "vehicle.vehicle.travelledDistance":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if getattr(self, "_attr_native_value", None) is None:
            restored, unit, last_state = await self.async_restored_native()
            if self._history is not None and last_state is not None:
                self._history.restore(last_state.attributes)
            if last_state is not None:
                # A list-valued descriptor saved its count as the state; the
                # list itself is in the attributes (see structured_values).
                if (items := restored_items(last_state.attributes)) is not None:
                    restored = items
            if is_placeholder(self._descriptor, restored):
                # A zero saved before BMW's "no reading" was recognised.
                restored = None
            if restored is not None:
                if self._is_enum:
                    restored, self._attr_options = sensor_values.enum_state(
                        restored, self._attr_options
                    )
                self._attr_native_value, _items = structured_state(restored)
                if unit is not None and not self._fixed_unit:
                    self._attr_native_unit_of_measurement = unit
                    # If unit is a length/distance type, enable conversion. The
                    # catalogue metadata already sets this for known descriptors;
                    # this covers restored, not-yet-classified sensors.
                    # NB: read via getattr — HA's SensorEntity declares
                    # _attr_device_class as a bare annotation with no default,
                    # so a direct attribute access raises AttributeError for any
                    # sensor whose __init__ never set a device class (e.g. GPS
                    # altitude), which would abort adding the entity.
                    if getattr(self, "_attr_device_class", None) is None and unit in {
                        u.value for u in UnitOfLength
                    }:
                        self._attr_device_class = (
                            SensorDeviceClass.DISTANCE
                        )  # Enables km/mi, m/ft, etc., conversion
                timestamp = None
                if last_state is not None:
                    timestamp = last_state.attributes.get("timestamp")
                    if not timestamp and last_state.last_changed:
                        timestamp = last_state.last_changed.isoformat()
                self._coordinator.restore_descriptor_state(
                    self.vin,
                    self.descriptor,
                    restored,
                    unit,
                    timestamp,
                )
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
        if not state:
            return
        value = state.value
        if self._is_enum:
            value, self._attr_options = sensor_values.enum_state(value, self._attr_options)
        # A list (service items, Check Control messages) is shown as its count:
        # stringified it overflows Home Assistant's 255-character state limit
        # and is recorded as unknown. The list itself goes out as an attribute.
        self._attr_native_value, items = structured_state(value)
        if self._history is not None:
            self._history.update(items, state.timestamp, dt_util.utcnow().isoformat())
        if not self._fixed_unit:
            self._attr_native_unit_of_measurement = entity_unit(
                getattr(self, "_attr_device_class", None), state.unit
            )

        self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if state is not None:
            _native, items = structured_state(state.value)
            if items is not None:
                attrs[ITEMS_ATTRIBUTE] = items
        if self._history is not None:
            attrs[RESOLVED_ATTRIBUTE] = self._history.resolved
        return attrs


class CardataDiagnosticsSensor(SensorEntity, RestoreEntity):
    _attr_should_poll = False
    # Without it Home Assistant ignores the translation key, and every
    # account sensor was named only after its device.
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CardataCoordinator,
        stream_manager,
        entry_id: str,
        sensor_type: str,
        quota_manager,
    ) -> None:
        self._coordinator = coordinator
        self._stream = stream_manager
        self._entry_id = entry_id
        self._sensor_type = sensor_type
        self._quota = quota_manager
        self._unsub = None
        # One of sensor_plan.DIAGNOSTIC_TYPES, each named from translations
        # (tools/derived_entities.json).
        self._attr_translation_key = sensor_type
        if sensor_type in {"last_message", "last_telematic_api"}:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_unique_id = f"{entry_id}_diagnostics_{sensor_type}"

    @property
    def device_info(self) -> DeviceInfo:
        return _account_device_info(self._entry_id)

    @property
    def extra_state_attributes(self) -> dict:
        if self._sensor_type == "connection_status":
            return sensor_values.connection_attributes(
                self._stream.debug_info,
                self._coordinator.last_disconnect_reason,
                self._quota,
                self._coordinator.display_units,
                self._coordinator.login_refused,
            )
        if self._sensor_type == "last_telematic_api":
            return sensor_values.quota_attributes(self._quota)
        return {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_native_value is None:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                if self._sensor_type in {"last_message", "last_telematic_api"}:
                    self._attr_native_value = dt_util.parse_datetime(last_state.state)
                else:
                    self._attr_native_value = last_state.state
        self._unsub = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_diagnostics,
            self._handle_update,
        )
        self._handle_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _handle_update(self) -> None:
        if self._sensor_type == "last_message":
            value = self._coordinator.last_message_at
            if value is not None:
                self._attr_native_value = value
        elif self._sensor_type == "last_telematic_api":
            value = self._coordinator.last_telematic_api_at
            if value is not None:
                self._attr_native_value = value
        elif self._sensor_type == "connection_status":
            value = self._coordinator.connection_status
            if value is not None:
                self._attr_native_value = value
        self.schedule_update_ha_state()

    @property
    def native_value(self):
        return self._attr_native_value


class CardataQuotaSensor(SensorEntity):
    """Surface the rolling 24 h REST quota as a first-class diagnostic sensor.

    The value is the number of requests still available; ``used``, ``limit`` and
    the next-reset time ride along as attributes so an automation can warn before
    the integration runs out of calls.
    """

    _attr_should_poll = False
    # Without it Home Assistant ignores the translation key, and every
    # account sensor was named only after its device.
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "requests"
    _attr_translation_key = "api_quota_remaining"

    def __init__(self, coordinator: CardataCoordinator, entry_id: str, quota_manager) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._quota = quota_manager
        self._unsub = None
        self._attr_unique_id = f"{entry_id}_diagnostics_api_quota_remaining"

    @property
    def device_info(self) -> DeviceInfo:
        return _account_device_info(self._entry_id)

    @property
    def native_value(self):
        return self._quota.remaining if self._quota else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self._quota:
            return {}
        attrs = sensor_values.quota_attributes(self._quota, prefix="")
        attrs["limit"] = REQUEST_LIMIT
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_diagnostics,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _handle_update(self) -> None:
        self.schedule_update_ha_state()


class CardataSocEstimateSensor(CardataRestoreSensor):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    # Named from translations (tools/derived_entities.json) rather than a
    # hardcoded _attr_name, so German installs don't fall back to English.
    _attr_translation_key = "soc_estimate"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "soc_estimate")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored, _unit, last_state = await self.async_restored_native()
        if restored is not None:
            try:
                self._attr_native_value = float(restored)
            except TypeError, ValueError:
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp") if last_state else None
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None and last_state is not None:
                    reference = last_state.last_changed
                if reference is not None:
                    reference = dt_util.as_utc(reference)
                # Always offered: the coordinator keeps whichever of this and
                # the restored SoC reading is newer (see ``adopt_estimate``).
                meter_kwh = last_state.attributes.get("meter_reading_kwh") if last_state else None
                self._coordinator.restore_soc_cache(
                    self.vin,
                    estimate=self._attr_native_value,
                    timestamp=reference,
                    meter_kwh=meter_kwh if isinstance(meter_kwh, (int, float)) else None,
                )
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_soc_estimate,
            self._handle_update,
        )
        existing = self._coordinator.get_soc_estimate(self.vin)
        if existing is not None:
            self._attr_native_value = existing
            self.schedule_update_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(self._coordinator.soc_estimate_attributes(self.vin))
        return attrs

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        value = self._coordinator.get_soc_estimate(vin)
        self._attr_native_value = value
        self.schedule_update_ha_state()


class CardataSocRateSensor(CardataRestoreSensor):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%/h"
    _attr_translation_key = "soc_rate"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "soc_rate")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored, _unit, last_state = await self.async_restored_native()
        if restored is not None:
            try:
                self._attr_native_value = float(restored)
            except TypeError, ValueError:
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp") if last_state else None
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None and last_state is not None:
                    reference = last_state.last_changed
                if reference is not None:
                    reference = dt_util.as_utc(reference)
                if self._coordinator.get_soc_rate(self.vin) is None:
                    self._coordinator.restore_soc_cache(
                        self.vin,
                        rate=self._attr_native_value,
                        timestamp=reference,
                    )
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_soc_estimate,
            self._handle_update,
        )
        existing = self._coordinator.get_soc_rate(self.vin)
        if existing is not None:
            self._attr_native_value = existing
            self.schedule_update_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        value = self._coordinator.get_soc_rate(vin)
        self._attr_native_value = value
        self.schedule_update_ha_state()


class CardataChargeEtaSensor(CardataEntity, SensorEntity):
    """Minutes until a home charge reaches its target, from the wallbox meter.

    Recomputed from the SoC estimate every tick, so nothing is restored: a stale
    figure is exactly what this replaces (see ``_charge_eta_minutes``).
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "charge_eta"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charge_eta")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_soc_estimate,
            self._handle_update,
        )
        self._attr_native_value = self._coordinator.get_charge_eta(self.vin)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        self._attr_native_value = self._coordinator.get_charge_eta(vin)
        self.schedule_update_ha_state()


class CardataChargedEnergySensor(CardataRestoreSensor):
    """Lifetime energy delivered to the battery, for the HA Energy dashboard.

    ``TOTAL_INCREASING`` + ``ENERGY`` is exactly what the Energy dashboard needs
    to track a device's consumption; the value is integrated from the streamed
    charging power so it works even though BMW never sends a kWh counter.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"
    _attr_translation_key = "charged_energy_total"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charged_energy_total")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._coordinator.get_lifetime_energy_kwh(self.vin) is None:
            value, _unit, last_state = await self.async_restored_native()
            if value is not None:
                try:
                    restored = float(value)
                except TypeError, ValueError:
                    restored = None
                if restored is not None:
                    self._attr_native_value = restored
                    self._coordinator.restore_lifetime_energy(self.vin, restored)
        existing = self._coordinator.get_lifetime_energy_kwh(self.vin)
        if existing is not None:
            self._attr_native_value = existing
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_energy,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        value = self._coordinator.get_lifetime_energy_kwh(vin)
        if value is not None:
            self._attr_native_value = value
            self.schedule_update_ha_state()


class CardataSessionEnergySensor(CardataRestoreSensor):
    """Energy delivered during the current charging session (resets each plug-in)."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_translation_key = "charged_energy_session"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charged_energy_session")
        self._unsubscribe = None

    @property
    def last_reset(self):
        return self._coordinator.get_session_start(self.vin)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._coordinator.get_session_energy_kwh(self.vin) is None:
            value, _unit, last_state = await self.async_restored_native()
            if value is not None:
                try:
                    restored = float(value)
                except TypeError, ValueError:
                    restored = None
                if restored is not None:
                    self._attr_native_value = restored
                    start_iso = last_state.attributes.get("last_reset") if last_state else None
                    start = dt_util.parse_datetime(start_iso) if start_iso else None
                    self._coordinator.restore_session_energy(self.vin, restored, start)
        existing = self._coordinator.get_session_energy_kwh(self.vin)
        if existing is not None:
            self._attr_native_value = existing
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_energy,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        value = self._coordinator.get_session_energy_kwh(vin)
        if value is not None:
            self._attr_native_value = value
            self.schedule_update_ha_state()


class CardataChargingSummarySensor(CardataEntity, SensorEntity):
    """Base for the sensors derived from recorded charging sessions.

    Values are recomputed on read rather than cached, and the state is rewritten
    both when a session lands and just after midnight -- otherwise a "this
    month" total would still show last month's figure on the 1st, which is
    exactly the sort of quietly-wrong number this feature must avoid.
    """

    _attr_should_poll = False

    def __init__(self, coordinator: CardataCoordinator, vin: str, key: str) -> None:
        super().__init__(coordinator, vin, key)
        self._unsubscribe = None
        self._unsub_midnight = None

    @property
    def _summary(self) -> Dict[str, Any]:
        history = self._coordinator.history
        if history is None:
            return {}
        now = dt_util.now()
        return summarise(
            sessions_in_month(
                history.sessions(self.vin),
                year=now.year,
                month=now.month,
                localize=dt_util.as_local,
            ),
            hybrid=self._coordinator.is_plug_in_hybrid(self.vin),
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_history,
            self._handle_update,
        )
        self._unsub_midnight = async_track_time_change(
            self.hass, self._handle_rollover, hour=0, minute=0, second=10
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    def _handle_update(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()

    def _handle_rollover(self, _now) -> None:
        self.schedule_update_ha_state()


class CardataChargingCostMonthSensor(CardataChargingSummarySensor):
    """What charging has cost so far this calendar month."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_translation_key = "charging_cost_month"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_cost_month")
        self._attr_native_unit_of_measurement = coordinator.pricing.currency

    @property
    def native_value(self):
        return self._summary.get("cost")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.cost_month_attributes(self._summary))
        return attrs


class CardataChargingCostSessionSensor(CardataEntity, SensorEntity):
    """What the most recently finished charging session cost."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_translation_key = "charging_cost_session"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_cost_session")
        self._attr_native_unit_of_measurement = coordinator.pricing.currency
        self._unsubscribe = None

    @property
    def _latest(self):
        history = self._coordinator.history
        if history is None:
            return None
        found = history.sessions(self.vin, limit=1)
        return found[0] if found else None

    @property
    def native_value(self):
        return sensor_values.session_cost(self._latest)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.session_cost_attributes(self._latest))
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_history,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()


class CardataChargingEnergyMonthSensor(CardataChargingSummarySensor):
    """Energy delivered to the battery this calendar month."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_translation_key = "charging_energy_month"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_energy_month")

    @property
    def native_value(self):
        return self._summary.get("energy_kwh")

    @property
    def extra_state_attributes(self) -> dict:
        """Adds this month's source mix (``sensor_values.energy_month_attributes``)."""

        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.energy_month_attributes(self._summary))
        return attrs


class CardataChargingCostPerDistanceSensor(CardataChargingSummarySensor):
    """Charging cost per 100 km, from the odometer read at each session.

    Only created when the odometer is actually streaming, and stays ``None``
    until two sessions have bracketed some distance -- one reading cannot
    describe a gap.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "charging_cost_per_100km"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_cost_per_100km")
        self._attr_native_unit_of_measurement = f"{coordinator.pricing.currency}/100 km"

    @property
    def native_value(self):
        return self._summary.get("cost_per_100km")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs["distance_km"] = self._summary.get("distance_km")
        return attrs


class CardataBatteryHealthSensor(CardataEntity, SensorEntity):
    """Usable HV-battery capacity, learned from wide-SoC charges.

    The single state a user should read for "how healthy is my battery". It
    refuses to show a figure until it has enough good samples *and* those samples
    agree with BMW's own capacity number: until then the state is
    ``Learning (n/10)`` rather than a value that would jump around as early,
    noisy samples arrive. The degradation trend and the vs-new figure ride along
    as attributes so the card can draw them without a second data source.
    """

    _attr_should_poll = False
    _attr_translation_key = "battery_health"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "battery_health")
        self._unsubscribe = None

    @property
    def _health(self):
        history = self._coordinator.history
        sessions = [] if history is None else history.sessions(self.vin)
        return usable_capacity(
            sessions,
            nominal_kwh=self._coordinator.battery_nominal_kwh(self.vin),
            sanity_kwh=self._coordinator.battery_capacity_kwh(self.vin),
        )

    @property
    def native_value(self):
        return sensor_values.battery_health_state(self._health)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        history = self._coordinator.history
        trend = (
            None
            if history is None
            else degradation_series(
                history.sessions(self.vin), limit=sensor_values.HEALTH_TREND_POINTS
            )
        )
        attrs.update(sensor_values.battery_health_attributes(self._health, trend))
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_history,
            self._handle_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()


class CardataRealRangeSensor(CardataEntity, SensorEntity):
    """How far the car really goes from its current charge.

    The one number BMW's own estimate is always being second-guessed against,
    so it is worth the entity slot the rest of this layer spends on services and
    attributes: usable capacity divided by consumption the charging ledger
    actually measured, scaled to the state of charge right now. Everything that
    explains it -- which window the consumption came from, which side of the
    charger, whose capacity, and what the car itself is predicting -- rides as
    attributes; the seasonal trend is left to ``get_efficiency`` rather than
    rewritten into the recorder on every SoC tick.

    Stays ``unknown`` until the ledger can support a figure. There is no
    "Learning" state to fall back on here the way battery health has one: a
    distance sensor has to be a distance, and a range invented from a nameplate
    consumption is precisely the number that strands someone.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "km"
    # Whole kilometres: the inputs are a capacity good to ~1 kWh and a
    # consumption good to 0.1 kWh/100 km, so a decimal place here would be
    # precision the measurement doesn't have -- and the car's own display,
    # which this sits next to, shows whole kilometres too.
    _attr_suggested_display_precision = 0
    _attr_translation_key = "real_range"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "real_range")
        self._unsub_history = None
        self._unsub_soc = None
        self._unsub_live = None
        self._unsub_heartbeat = None
        self._cached: Optional[Dict[str, Any]] = None
        # What the cached profile was computed from, so the heartbeat can tell
        # "nothing moved" from "nobody told us" without walking the ledger.
        self._inputs: Optional[tuple] = None

    @property
    def _profile(self) -> Dict[str, Any]:
        """The efficiency profile, recomputed only when something moved it.

        Both the state and the attributes read this, and each state write asks
        for both; the balance behind it walks every stored session, so caching
        between signals keeps a charging car's SoC ticks cheap.
        """

        if self._cached is None:
            self._inputs = self._live_inputs()
            self._cached = self._coordinator.efficiency(self.vin, months=0)
        return self._cached

    def _live_inputs(self) -> tuple:
        """The two figures the profile reads off the stream rather than the ledger.

        Both are O(1) dictionary reads, which is the point: the heartbeat can
        check them on every tick, and only the rare tick where one has actually
        moved pays for recomputing the profile.
        """

        return (
            self._coordinator.bmw_range_km(self.vin),
            self._coordinator.battery_capacity_kwh(self.vin),
        )

    @property
    def native_value(self):
        return sensor_values.real_range_state(self._profile)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.real_range_attributes(self._profile))
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_history = async_dispatcher_connect(
            self.hass, self._coordinator.signal_history, self._handle_update
        )
        # The range from *here* moves with every SoC tick, not only when a
        # charge lands.
        self._unsub_soc = async_dispatcher_connect(
            self.hass, self._coordinator.signal_soc_estimate, self._handle_update
        )
        # ...and the two figures it is measured *against* -- BMW's own range and
        # the pack capacity -- move with neither: they are plain stream messages.
        # Without this the first write after a restart, taken before the stream
        # has delivered anything, is the last one a parked car ever gets, and the
        # card's comparison against BMW silently disappears until the next charge.
        self._unsub_live = async_dispatcher_connect(
            self.hass, self._coordinator.signal_update, self._handle_descriptor
        )
        # A restart delivers those same two figures with no signal at all: every
        # descriptor entity restores its own state and pushes it back into the
        # coordinator, silently, in whatever order the platform adds them. This
        # entity's one write can therefore happen before the figures it is
        # measured against exist -- and on a parked car, which streams nothing
        # for hours, that first write is the last one. The stream heartbeat is
        # the tick that always comes, so it is what notices.
        self._unsub_heartbeat = async_dispatcher_connect(
            self.hass, self._coordinator.signal_diagnostics, self._handle_heartbeat
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_history:
            self._unsub_history()
            self._unsub_history = None
        if self._unsub_soc:
            self._unsub_soc()
            self._unsub_soc = None
        if self._unsub_live:
            self._unsub_live()
            self._unsub_live = None
        if self._unsub_heartbeat:
            self._unsub_heartbeat()
            self._unsub_heartbeat = None

    def _handle_descriptor(self, vin: str, descriptor: str) -> None:
        # Every descriptor this car streams comes through here, and recomputing
        # the profile walks every stored session -- so filter to the two the
        # profile actually reads live before paying for it.
        if descriptor not in EFFICIENCY_LIVE_DESCRIPTORS:
            return
        self._handle_update(vin)

    def _handle_heartbeat(self) -> None:
        if self._cached is not None and self._live_inputs() == self._inputs:
            return
        self._handle_update(self.vin)

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        self._cached = None
        self.schedule_update_ha_state()


class CardataDrivingDistanceMonthSensor(CardataEntity, SensorEntity):
    """How far the car has been driven this calendar month, from trip records.

    The single "how much am I driving" figure; the per-class split rides as
    attributes and the full month-in-review (consumption, recuperation, top
    destinations, cost) is served by ``get_driving_summary`` rather than spawning
    an entity per number (roadmap rule 1). Recomputed on read and rewritten both
    when a trip lands and just after midnight, so "this month" is never stale.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "km"
    _attr_translation_key = "driving_distance_month"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "driving_distance_month")
        self._unsubscribe = None
        self._unsub_midnight = None

    @property
    def _summary(self) -> Dict[str, Any]:
        history = self._coordinator.history
        if history is None:
            return {}
        now = dt_util.now()
        return driving_summary(
            trips_in_month(
                history.trips(self.vin),
                year=now.year,
                month=now.month,
                localize=dt_util.as_local,
            )
        )

    @property
    def native_value(self):
        return self._summary.get("total_km")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.driving_month_attributes(self._summary))
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_trips,
            self._handle_update,
        )
        self._unsub_midnight = async_track_time_change(
            self.hass, self._handle_rollover, hour=0, minute=0, second=10
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    def _handle_update(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()

    def _handle_rollover(self, _now) -> None:
        self.schedule_update_ha_state()


class CardataLastVehicleReportSensor(CardataEntity, SensorEntity):
    """When the car last reported its odometer, by the car's own clock.

    "Last Message Received" is when Home Assistant heard from BMW; this is when
    the car spoke. The two part ways for a car parked without reception, whose
    report arrives hours late (discussion #56). Created disabled: on a car with
    a heartbeat it changes every few minutes, and only a logbook needs it.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "last_vehicle_report"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "last_vehicle_report")
        self._unsubscribe = None

    @property
    def native_value(self):
        return self._coordinator.last_vehicle_report(self.vin)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass, self._coordinator.signal_update, self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str, descriptor: str) -> None:
        if vin == self.vin and descriptor in DESC_ODOMETER:
            self.schedule_update_ha_state()


class CardataMileageAllowanceSensor(CardataEntity, SensorEntity):
    """How far the car is under (+) or over (-) its mileage allowance's plan to date.

    The one number of the mileage overview (``mileage.py``); the rest of the
    allowance -- used, remaining, km a day left, projected end -- rides as
    attributes, and the full overview is ``get_mileage_report``'s. Unknown
    while the car has no allowance (Configure -> Mileage allowance). Created
    disabled, like the roadmap's other additions.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = "km"
    _attr_suggested_display_precision = 0
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "mileage_allowance"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "mileage_allowance")
        self._unsubscribers: list = []

    @property
    def _allowance(self) -> Dict[str, Any]:
        return self._coordinator.mileage_report(self.vin).get("allowance") or {}

    @property
    def native_value(self):
        return self._allowance.get("balance_km")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.mileage_attributes(self._allowance))
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribers = [
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_update, self._handle_update
            ),
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_mileage, self._handle_allowance
            ),
            async_track_time_change(self.hass, self._handle_rollover, hour=0, minute=0, second=10),
        ]

    async def async_will_remove_from_hass(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []

    def _handle_update(self, vin: str, descriptor: str) -> None:
        if vin == self.vin and descriptor in DESC_ODOMETER:
            self.schedule_update_ha_state()

    def _handle_allowance(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()

    def _handle_rollover(self, _now) -> None:
        # The plan to date grows every day even while the car stands still.
        self.schedule_update_ha_state()


class CardataTyreEntity(CardataEntity, SensorEntity):
    """Base for the sensors fed by the smart-maintenance tyre diagnosis.

    That data is REST-only -- BMW cannot stream it -- so these listen on their
    own signal rather than the stream update, and their state is read straight
    off the coordinator's stored diagnosis instead of being cached.
    """

    _attr_should_poll = False

    def __init__(self, coordinator: CardataCoordinator, vin: str, key: str) -> None:
        super().__init__(coordinator, vin, key)
        self._unsubscribe = None

    @property
    def _diagnosis(self) -> Dict[str, Any]:
        return self._coordinator.tyre_diagnosis.get(self.vin) or {}

    @property
    def extra_state_attributes(self) -> dict:
        return sensor_values.tyre_attributes(
            super().extra_state_attributes, self._diagnosis, SECTIONS.get("tire", "Tire data")
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = async_dispatcher_connect(
            self.hass, self._coordinator.signal_tyre, self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, vin: str) -> None:
        if vin == self.vin:
            self.schedule_update_ha_state()


class CardataTyreWheelSensor(CardataTyreEntity):
    """Tyre condition for one wheel.

    The state is the wear traffic light (``green``/``yellow``/``red``/``grey``)
    because that is the one field with a defined set of values; everything else
    BMW sends for the wheel -- remaining mileage, tread, season, dimension,
    dates -- rides along as attributes, which is what the card renders. Splitting
    those into ~8 entities per wheel would put 32 on the device to express one
    tyre service report.
    """

    def __init__(self, coordinator: CardataCoordinator, vin: str, position: str) -> None:
        # Set before super(): CardataEntity falls back to a computed English
        # _attr_name when it finds no translation key, and that name would then
        # win over the translated one for German installs.
        self._attr_translation_key = f"tyre_{position}"
        super().__init__(coordinator, vin, f"tyre_{position}")
        self._position = position

    @property
    def _wheel(self) -> Dict[str, Any]:
        return sensor_values.wheel(self._diagnosis, self._position)

    @property
    def native_value(self):
        return sensor_values.wheel_state(self._wheel)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.wheel_attributes(self._position, self._wheel))
        return attrs


class CardataTyreStatusSensor(CardataTyreEntity):
    """BMW's overall verdict on the mounted set, plus any upstream errors."""

    _attr_translation_key = "tyre_status"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "tyre_status")

    @property
    def native_value(self):
        return self._diagnosis.get("aggregated_status")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs.update(sensor_values.tyre_status_attributes(self._diagnosis))
        return attrs


_DERIVED: Dict[str, Any] = {
    "soc_estimate": CardataSocEstimateSensor,
    "soc_rate": CardataSocRateSensor,
    "charge_eta": CardataChargeEtaSensor,
    "charged_energy_total": CardataChargedEnergySensor,
    "charged_energy_session": CardataSessionEnergySensor,
    sensor_plan.CHARGING_ENERGY_MONTH: CardataChargingEnergyMonthSensor,
    "charging_cost_month": CardataChargingCostMonthSensor,
    "charging_cost_session": CardataChargingCostSessionSensor,
    sensor_plan.CHARGING_COST_PER_DISTANCE: CardataChargingCostPerDistanceSensor,
    sensor_plan.BATTERY_HEALTH: CardataBatteryHealthSensor,
    sensor_plan.DRIVING_DISTANCE: CardataDrivingDistanceMonthSensor,
    sensor_plan.REAL_RANGE: CardataRealRangeSensor,
    sensor_plan.VEHICLE_REPORT: CardataLastVehicleReportSensor,
    sensor_plan.MILEAGE_ALLOWANCE: CardataMileageAllowanceSensor,
    sensor_plan.TYRE_STATUS: CardataTyreStatusSensor,
}


def _build(coordinator: CardataCoordinator, want: sensor_plan.Want) -> SensorEntity:
    """The entity a ``sensor_plan.Want`` names."""

    if want.kind == sensor_plan.DESCRIPTOR:
        return CardataSensor(coordinator, want.vin, want.key)
    if want.kind == sensor_plan.LOCATION:
        entity = CardataSensor(coordinator, want.vin, want.key)
        # Not the metadata's ``enabled_default``: that flag also decides what
        # the stream is asked for, and the position must stay in it.
        entity._attr_entity_registry_enabled_default = False
        return entity
    if want.kind == sensor_plan.TYRE_WHEEL:
        return CardataTyreWheelSensor(coordinator, want.vin, want.key)
    return _DERIVED[want.kind](coordinator, want.vin)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    runtime = entry.runtime_data
    coordinator: CardataCoordinator = runtime.coordinator
    entity_registry = er.async_get(hass)

    def add(wants: list[sensor_plan.Want], update: bool) -> list[SensorEntity]:
        entities = [_build(coordinator, want) for want in wants]
        async_add_entities(entities, update)
        return entities

    plan = sensor_plan.SensorPlan(coordinator, entry.entry_id, add)

    for old_unique_id, new_unique_id in sensor_plan.legacy_unique_ids(entry.entry_id).items():
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
        if entity_id:
            entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)
    for unique_id in sensor_plan.retired_unique_ids(entry.entry_id):
        if entity_id := entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id):
            entity_registry.async_remove(entity_id)

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity_entry.domain != "sensor" or entity_entry.disabled_by is not None:
            continue
        if not plan.restore(entity_entry.unique_id):
            entity_registry.async_remove(entity_entry.entity_id)

    plan.known(
        coordinator.iter_descriptors(binary=False),
        list(coordinator.data),
        list(coordinator.tyre_diagnosis),
    )

    async def async_handle_new(vin: str, descriptor: str) -> None:
        plan.descriptor(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_sensor, async_handle_new)
    )

    async def async_handle_soc_estimate(vin: str) -> None:
        plan.soc_tracking(vin)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_soc_estimate, async_handle_soc_estimate)
    )

    async def async_handle_new_tyre(vin: str) -> None:
        # Removing the registry entry removes the live entity with it.
        for entity in plan.tyre_diagnosis(vin):
            if entity.entity_id and entity_registry.async_get(entity.entity_id):
                entity_registry.async_remove(entity.entity_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_new_tyre", async_handle_new_tyre)
    )

    def wanted(unique_id: str) -> bool:
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if not entity_id:
            return True
        registered = entity_registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        return sensor_plan.add_diagnostic(
            disabled=registered is not None and registered.disabled_by is not None,
            live=state is not None and not state.attributes.get("restored", False),
        )

    diagnostic_entities: list[SensorEntity] = [
        CardataDiagnosticsSensor(
            coordinator, runtime.stream, entry.entry_id, sensor_type, runtime.quota_manager
        )
        for sensor_type in sensor_plan.DIAGNOSTIC_TYPES
        if wanted(sensor_plan.diagnostic_unique_id(entry.entry_id, sensor_type))
    ]
    if runtime.quota_manager is not None:
        quota_entity_id = entity_registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            sensor_plan.diagnostic_unique_id(entry.entry_id, sensor_plan.QUOTA_SUFFIX),
        )
        quota_entry = entity_registry.async_get(quota_entity_id) if quota_entity_id else None
        if quota_entry is None or quota_entry.disabled_by is None:
            diagnostic_entities.append(
                CardataQuotaSensor(coordinator, entry.entry_id, runtime.quota_manager)
            )

    if diagnostic_entities:
        async_add_entities(diagnostic_entities, True)
