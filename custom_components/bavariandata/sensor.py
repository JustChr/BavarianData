"""Sensor platform for BMW CarData."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from homeassistant.components.sensor import (
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
from .coordinator import CardataCoordinator
from .descriptor_metadata import DESCRIPTOR_META
from .entity import CardataEntity
from .history.summary import sessions_in_month, summarise


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
    "volume_storage": SensorDeviceClass.VOLUME_STORAGE,
}
_STATE_CLASS_MAP = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}


class CardataSensor(CardataEntity, SensorEntity):
    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        self._attr_should_poll = False
        self._unsubscribe = None
        # ``True`` when the catalogue pins the unit/device class, so runtime unit
        # strings from BMW (e.g. "percent") must not override it.
        self._fixed_unit = False
        self._is_enum = False

        meta = DESCRIPTOR_META.get(descriptor)
        if meta:
            device_class = _DEVICE_CLASS_MAP.get(meta.get("device_class"))
            options = meta.get("options") or []
            if device_class is not None:
                self._attr_device_class = device_class
                self._attr_state_class = _STATE_CLASS_MAP.get(meta.get("state_class"))
                if meta.get("unit"):
                    self._attr_native_unit_of_measurement = meta["unit"]
                    self._fixed_unit = True
            elif options:
                # Enum sensor: translated states come from the translation key.
                self._attr_device_class = SensorDeviceClass.ENUM
                self._attr_options = list(options)
                self._is_enum = True
        elif self._descriptor == "vehicle.vehicle.travelledDistance":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
    
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if getattr(self, "_attr_native_value", None) is None:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                restored = last_state.state
                if self._is_enum and isinstance(restored, str):
                    # Match the lowercase-slug options (old installs stored
                    # ALL_CAPS enum states before this normalisation).
                    restored = restored.lower()
                    if restored not in self._attr_options:
                        self._attr_options = [*self._attr_options, restored]
                self._attr_native_value = restored
                unit = last_state.attributes.get("unit_of_measurement")
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
                    if (
                        getattr(self, "_attr_device_class", None) is None
                        and unit in {u.value for u in UnitOfLength}
                    ):
                        self._attr_device_class = SensorDeviceClass.DISTANCE # Enables km/mi, m/ft, etc., conversion
                timestamp = last_state.attributes.get("timestamp")
                if not timestamp and last_state.last_changed:
                    timestamp = last_state.last_changed.isoformat()
                self._coordinator.restore_descriptor_state(
                    self.vin,
                    self.descriptor,
                    self._attr_native_value,
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
        if self._is_enum and isinstance(value, str):
            # Options and translation state keys are lowercase slugs; BMW sends
            # ALL_CAPS tokens, so normalise before matching/displaying.
            value = value.lower()
            if value not in self._attr_options:
                # BMW occasionally reports a value the catalogue did not
                # document. Extend the option list so Home Assistant accepts it
                # instead of logging a validation error; it shows untranslated.
                self._attr_options = [*self._attr_options, value]
        self._attr_native_value = value
        if not self._fixed_unit:
            self._attr_native_unit_of_measurement = state.unit

        self.schedule_update_ha_state()


class CardataDiagnosticsSensor(SensorEntity, RestoreEntity):
    _attr_should_poll = False
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
        # Known types are named from translations (tools/derived_entities.json);
        # only the catch-all keeps a literal _attr_name, since an unforeseen
        # sensor_type has no translation to resolve.
        if sensor_type == "last_message":
            suffix = "last_message"
            self._attr_translation_key = suffix
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif sensor_type == "last_telematic_api":
            suffix = "last_telematic_api"
            self._attr_translation_key = suffix
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif sensor_type == "connection_status":
            suffix = "connection_status"
            self._attr_translation_key = suffix
        else:
            suffix = sensor_type
            self._attr_name = sensor_type
        self._attr_unique_id = f"{entry_id}_diagnostics_{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "manufacturer": "BMW",
            "name": "CarData Debug Device",
        }

    @property
    def extra_state_attributes(self) -> dict:
        if self._sensor_type == "connection_status":
            attrs = dict(self._stream.debug_info)
            if self._coordinator.last_disconnect_reason:
                attrs["last_disconnect_reason"] = self._coordinator.last_disconnect_reason
            if self._quota:
                attrs["api_quota_used"] = self._quota.used
                attrs["api_quota_remaining"] = self._quota.remaining
                if next_reset := self._quota.next_reset_iso:
                    attrs["api_quota_next_reset"] = next_reset
            return attrs
        if self._sensor_type == "last_telematic_api":
            attrs: dict[str, Any] = {}
            if self._quota:
                attrs["api_quota_used"] = self._quota.used
                attrs["api_quota_remaining"] = self._quota.remaining
                if next_reset := self._quota.next_reset_iso:
                    attrs["api_quota_next_reset"] = next_reset
            return attrs
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
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "requests"
    _attr_icon = "mdi:api"
    _attr_translation_key = "api_quota_remaining"

    def __init__(self, coordinator: CardataCoordinator, entry_id: str, quota_manager) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._quota = quota_manager
        self._unsub = None
        self._attr_unique_id = f"{entry_id}_diagnostics_api_quota_remaining"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "manufacturer": "BMW",
            "name": "CarData Debug Device",
        }

    @property
    def native_value(self):
        return self._quota.remaining if self._quota else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self._quota:
            return {}
        attrs: dict[str, Any] = {
            "used": self._quota.used,
            "remaining": self._quota.remaining,
            "limit": REQUEST_LIMIT,
        }
        if next_reset := self._quota.next_reset_iso:
            attrs["next_reset"] = next_reset
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


class CardataSocEstimateSensor(CardataEntity, SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-clock"
    # Named from translations (tools/derived_entities.json) rather than a
    # hardcoded _attr_name, so German installs don't fall back to English.
    _attr_translation_key = "soc_estimate"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "soc_estimate")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except (TypeError, ValueError):
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp")
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None:
                    reference = last_state.last_changed
                if reference is not None:
                    reference = dt_util.as_utc(reference)
                if self._coordinator.get_soc_estimate(self.vin) is None:
                    self._coordinator.restore_soc_cache(
                        self.vin,
                        estimate=self._attr_native_value,
                        timestamp=reference,
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

    def _handle_update(self, vin: str) -> None:
        if vin != self.vin:
            return
        value = self._coordinator.get_soc_estimate(vin)
        self._attr_native_value = value
        self.schedule_update_ha_state()


class CardataTestingSocEstimateSensor(CardataEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "soc_estimate_testing"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "soc_estimate_testing")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except (TypeError, ValueError):
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp")
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None:
                    reference = last_state.last_changed
                if reference is not None:
                    reference = dt_util.as_utc(reference)
                if self._coordinator.get_testing_soc_estimate(self.vin) is None:
                    self._coordinator.restore_testing_soc_cache(
                        self.vin,
                        estimate=self._attr_native_value,
                        timestamp=reference,
                    )
        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            self._coordinator.signal_soc_estimate,
            self._handle_update,
        )
        existing = self._coordinator.get_testing_soc_estimate(self.vin)
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
        value = self._coordinator.get_testing_soc_estimate(vin)
        self._attr_native_value = value
        self.schedule_update_ha_state()


class CardataSocRateSensor(CardataEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%/h"
    _attr_icon = "mdi:battery-clock"
    _attr_translation_key = "soc_rate"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "soc_rate")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except (TypeError, ValueError):
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp")
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None:
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


class CardataChargedEnergySensor(CardataEntity, SensorEntity):
    """Lifetime energy delivered to the battery, for the HA Energy dashboard.

    ``TOTAL_INCREASING`` + ``ENERGY`` is exactly what the Energy dashboard needs
    to track a device's consumption; the value is integrated from the streamed
    charging power so it works even though BMW never sends a kWh counter.
    """

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:lightning-bolt"
    _attr_translation_key = "charged_energy_total"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charged_energy_total")
        self._unsubscribe = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._coordinator.get_lifetime_energy_kwh(self.vin) is None:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                try:
                    restored = float(last_state.state)
                except (TypeError, ValueError):
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


class CardataSessionEnergySensor(CardataEntity, SensorEntity):
    """Energy delivered during the current charging session (resets each plug-in)."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:ev-station"
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
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in ("unknown", "unavailable"):
                try:
                    restored = float(last_state.state)
                except (TypeError, ValueError):
                    restored = None
                if restored is not None:
                    self._attr_native_value = restored
                    start_iso = last_state.attributes.get("last_reset")
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
            )
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
    _attr_icon = "mdi:cash-multiple"
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
        summary = self._summary
        attrs["sessions"] = summary.get("sessions", 0)
        attrs["energy_kwh"] = summary.get("energy_kwh")
        # Flags a total that is a floor, not a figure: at least one session was
        # charged while the price was unknown.
        attrs["partial"] = summary.get("partial", False)
        return attrs


class CardataChargingCostSessionSensor(CardataEntity, SensorEntity):
    """What the most recently finished charging session cost."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash"
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
        session = self._latest
        if session is None or not session.cost:
            return None
        return session.cost.get("amount")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        session = self._latest
        if session is None:
            return attrs
        attrs["energy_kwh"] = session.energy_kwh
        attrs["duration_s"] = session.duration_s
        attrs["soc_start"] = session.soc_start
        attrs["soc_end"] = session.soc_end
        attrs["peak_power_kw"] = session.peak_power_kw
        if session.location:
            attrs["zone"] = session.location.get("zone")
        attrs["location_assumed"] = session.location_assumed
        if session.cost:
            attrs["cost_source"] = session.cost.get("source")
            attrs["partial"] = bool(session.cost.get("partial"))
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
    _attr_icon = "mdi:ev-station"
    _attr_translation_key = "charging_energy_month"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_energy_month")

    @property
    def native_value(self):
        return self._summary.get("energy_kwh")


class CardataChargingCostPerDistanceSensor(CardataChargingSummarySensor):
    """Charging cost per 100 km, from the odometer read at each session.

    Only created when the odometer is actually streaming, and stays ``None``
    until two sessions have bracketed some distance -- one reading cannot
    describe a gap.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash-marker"
    _attr_translation_key = "charging_cost_per_100km"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "charging_cost_per_100km")
        self._attr_native_unit_of_measurement = (
            f"{coordinator.pricing.currency}/100 km"
        )

    @property
    def native_value(self):
        return self._summary.get("cost_per_100km")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        attrs["distance_km"] = self._summary.get("distance_km")
        return attrs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: CardataCoordinator = runtime.coordinator

    entities: Dict[Tuple[str, str], CardataSensor] = {}
    soc_estimate_entities: Dict[str, CardataSocEstimateSensor] = {}
    soc_estimate_testing_entities: Dict[str, CardataTestingSocEstimateSensor] = {}
    soc_rate_entities: Dict[str, CardataSocRateSensor] = {}
    charged_energy_entities: Dict[str, CardataChargedEnergySensor] = {}
    session_energy_entities: Dict[str, CardataSessionEnergySensor] = {}
    charging_summary_entities: Dict[str, list] = {}

    def ensure_charging_summary_entities(vin: str) -> None:
        """Create the ledger sensors, but only once they can say something true.

        The energy total works for anyone. The cost sensors need a configured
        tariff, so until one exists they are not created at all rather than
        sitting at "unknown" and inviting the question of what's broken.
        """

        if vin in charging_summary_entities or coordinator.history is None:
            return
        new_entities: list = [CardataChargingEnergyMonthSensor(coordinator, vin)]
        if coordinator.pricing.enabled:
            new_entities.append(CardataChargingCostMonthSensor(coordinator, vin))
            new_entities.append(CardataChargingCostSessionSensor(coordinator, vin))
            # Distance-based cost is meaningless without an odometer, and the
            # Vehicle status cluster is optional in the portal.
            if coordinator.get_state(vin, "vehicle.vehicle.mileage") is not None:
                new_entities.append(
                    CardataChargingCostPerDistanceSensor(coordinator, vin)
                )
        charging_summary_entities[vin] = new_entities
        async_add_entities(new_entities, True)

    def ensure_soc_tracking_entities(vin: str) -> None:
        new_entities = []
        if vin not in soc_estimate_entities:
            estimate = CardataSocEstimateSensor(coordinator, vin)
            soc_estimate_entities[vin] = estimate
            new_entities.append(estimate)
        if vin not in soc_estimate_testing_entities:
            testing_estimate = CardataTestingSocEstimateSensor(coordinator, vin)
            soc_estimate_testing_entities[vin] = testing_estimate
            new_entities.append(testing_estimate)
        if vin not in soc_rate_entities:
            rate = CardataSocRateSensor(coordinator, vin)
            soc_rate_entities[vin] = rate
            new_entities.append(rate)
        if vin not in charged_energy_entities:
            charged = CardataChargedEnergySensor(coordinator, vin)
            charged_energy_entities[vin] = charged
            new_entities.append(charged)
        if vin not in session_energy_entities:
            session = CardataSessionEnergySensor(coordinator, vin)
            session_energy_entities[vin] = session
            new_entities.append(session)
        if new_entities:
            async_add_entities(new_entities, True)

    def ensure_entity(vin: str, descriptor: str, *, assume_sensor: bool = False) -> None:
        ensure_soc_tracking_entities(vin)
        ensure_charging_summary_entities(vin)
        if (vin, descriptor) in entities:
            return
        
        # Filter out location descriptors - these are used by device_tracker only
        location_descriptors = [
            "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
            "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
            "vehicle.cabin.infotainment.navigation.currentLocation.heading",
        ]
        if descriptor in location_descriptors:
            return
        
        state = coordinator.get_state(vin, descriptor)
        if state:
            if isinstance(state.value, bool):
                return
        elif not assume_sensor:
            return
        entity = CardataSensor(coordinator, vin, descriptor)
        entities[(vin, descriptor)] = entity
        async_add_entities([entity])

    entity_registry = er.async_get(hass)
    legacy_unique_ids = {
        f"{entry.entry_id}_connection_status": f"{entry.entry_id}_diagnostics_connection_status",
        f"{entry.entry_id}_last_message": f"{entry.entry_id}_diagnostics_last_message",
    }
    for old_unique_id, new_unique_id in legacy_unique_ids.items():
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
        if entity_id:
            entity_registry.async_update_entity(
                entity_id, new_unique_id=new_unique_id
            )

    legacy_soc_rate_unique = f"{entry.entry_id}_diagnostics_soc_rate"
    legacy_soc_rate_entity = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, legacy_soc_rate_unique
    )
    if legacy_soc_rate_entity:
        entity_registry.async_remove(legacy_soc_rate_entity)

    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.domain != "sensor":
            continue
        if entity_entry.disabled_by is not None:
            continue
        unique_id = entity_entry.unique_id
        if not unique_id or "_" not in unique_id:
            continue
        if unique_id.startswith(f"{entry.entry_id}_diagnostics_"):
            continue
        vin, descriptor = unique_id.split("_", 1)
        if descriptor in {
            "soc_estimate",
            "soc_rate",
            "soc_estimate_testing",
            "charged_energy_total",
            "charged_energy_session",
        }:
            ensure_soc_tracking_entities(vin)
            continue
        ensure_entity(vin, descriptor, assume_sensor=True)

    for vin, descriptor in coordinator.iter_descriptors(binary=False):
        ensure_entity(vin, descriptor)

    for vin in list(coordinator.data.keys()):
        ensure_soc_tracking_entities(vin)

    async def async_handle_new(vin: str, descriptor: str) -> None:
        ensure_entity(vin, descriptor)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_sensor, async_handle_new)
    )

    async def async_handle_soc_estimate(vin: str) -> None:
        ensure_soc_tracking_entities(vin)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, coordinator.signal_soc_estimate, async_handle_soc_estimate
        )
    )

    diagnostic_entities: list[CardataDiagnosticsSensor] = []
    stream_manager = runtime.stream
    for sensor_type in ("connection_status", "last_message", "last_telematic_api"):
        if sensor_type == "last_message":
            unique_id = f"{entry.entry_id}_diagnostics_last_message"
        elif sensor_type == "last_telematic_api":
            unique_id = f"{entry.entry_id}_diagnostics_last_telematic_api"
        else:
            unique_id = f"{entry.entry_id}_diagnostics_connection_status"
        entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry and entity_entry.disabled_by is not None:
                continue
            existing_state = hass.states.get(entity_id)
            if existing_state and not existing_state.attributes.get("restored", False):
                continue
        diagnostic_entities.append(
            CardataDiagnosticsSensor(
                coordinator,
                stream_manager,
                entry.entry_id,
                sensor_type,
                runtime.quota_manager,
            )
        )

    if runtime.quota_manager is not None:
        quota_unique_id = f"{entry.entry_id}_diagnostics_api_quota_remaining"
        quota_entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, quota_unique_id
        )
        add_quota = True
        if quota_entity_id:
            quota_entry = entity_registry.async_get(quota_entity_id)
            if quota_entry and quota_entry.disabled_by is not None:
                add_quota = False
        if add_quota:
            diagnostic_entities.append(
                CardataQuotaSensor(
                    coordinator, entry.entry_id, runtime.quota_manager
                )
            )

    if diagnostic_entities:
        async_add_entities(diagnostic_entities, True)
