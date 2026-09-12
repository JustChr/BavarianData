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
from .coordinator import (
    DESC_ODOMETER,
    EFFICIENCY_LIVE_DESCRIPTORS,
    CardataCoordinator,
)
from .descriptor_metadata import DESCRIPTOR_META, SECTIONS
from .entity import CardataEntity
from .history.health import MIN_SAMPLES, degradation_series, usable_capacity
from .history.summary import (
    driving_summary,
    sessions_in_month,
    summarise,
    trips_in_month,
)
from .restore_units import restore_native


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

_LOGGER = logging.getLogger(__name__)


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
            restored, unit, last_state = await self.async_restored_native()
            if restored is not None:
                if self._is_enum and isinstance(restored, str):
                    # Match the lowercase-slug options (old installs stored
                    # ALL_CAPS enum states before this normalisation).
                    restored = restored.lower()
                    if restored not in self._attr_options:
                        self._attr_options = [*self._attr_options, restored]
                self._attr_native_value = restored
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
                timestamp = None
                if last_state is not None:
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


class CardataSocEstimateSensor(CardataRestoreSensor):
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
        restored, _unit, last_state = await self.async_restored_native()
        if restored is not None:
            try:
                self._attr_native_value = float(restored)
            except (TypeError, ValueError):
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp") if last_state else None
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None and last_state is not None:
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


class CardataTestingSocEstimateSensor(CardataRestoreSensor):
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
        restored, _unit, last_state = await self.async_restored_native()
        if restored is not None:
            try:
                self._attr_native_value = float(restored)
            except (TypeError, ValueError):
                self._attr_native_value = None
            else:
                restored_ts = last_state.attributes.get("timestamp") if last_state else None
                reference = dt_util.parse_datetime(restored_ts) if restored_ts else None
                if reference is None and last_state is not None:
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


class CardataSocRateSensor(CardataRestoreSensor):
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
        restored, _unit, last_state = await self.async_restored_native()
        if restored is not None:
            try:
                self._attr_native_value = float(restored)
            except (TypeError, ValueError):
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
    _attr_icon = "mdi:lightning-bolt"
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


class CardataSessionEnergySensor(CardataRestoreSensor):
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
            value, _unit, last_state = await self.async_restored_native()
            if value is not None:
                try:
                    restored = float(value)
                except (TypeError, ValueError):
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
            if session.location.get("address"):
                attrs["address"] = session.location.get("address")
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

    @property
    def extra_state_attributes(self) -> dict:
        """Adds this month's source mix -- as attributes, not as new entities.

        The history layer's entity budget is spent (one entity per question a
        user actually asks), and "how much of my charging was sun?" is asked of
        the same number this sensor already reports. ``solar_percent`` is lifted
        to the top level because it is the figure people want; the kWh behind it
        stay in ``energy_mix`` for anything that wants to check the arithmetic.

        Absent entirely when nothing could be attributed, which is deliberately
        distinct from a mix saying none of it was solar.
        """

        attrs = dict(super().extra_state_attributes)
        mix = self._summary.get("energy_mix")
        if mix:
            attrs["energy_mix"] = mix
            if mix.get("solar_percent") is not None:
                attrs["solar_percent"] = mix["solar_percent"]
        return attrs


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


# How many trend points to publish in the sensor's attributes. Battery health
# changes at most once per charge, so this attribute is rewritten rarely -- but
# the series still has to stay small enough not to bloat the recorder row.
_HEALTH_TREND_POINTS = 60


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
    _attr_icon = "mdi:battery-heart-variant"
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
        health = self._health
        # Never a number we aren't sure of: while learning, the state names the
        # progress instead of a figure that would jump as samples trickle in.
        if not health.confident:
            return f"Learning ({min(health.samples, MIN_SAMPLES)}/{MIN_SAMPLES})"
        return health.usable_kwh

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        health = self._health
        attrs["samples"] = health.samples
        attrs["samples_needed"] = MIN_SAMPLES
        attrs["confident"] = health.confident
        attrs["usable_capacity_kwh"] = health.usable_kwh
        attrs["nominal_capacity_kwh"] = health.nominal_kwh
        attrs["vs_new_percent"] = health.vs_new_percent
        # A capacity figure that disagrees with BMW's own is being withheld; say
        # so rather than leaving the sensor stuck at "Learning" with no reason.
        attrs["suspicious"] = health.suspicious
        history = self._coordinator.history
        if history is not None:
            attrs["trend"] = degradation_series(
                history.sessions(self.vin), limit=_HEALTH_TREND_POINTS
            )
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
    _attr_icon = "mdi:map-marker-distance"
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
        ranges = self._profile.get("range") or {}
        return ranges.get("now_km")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        profile = self._profile
        ranges = profile.get("range") or {}
        consumption = profile.get("consumption") or {}
        grid = profile.get("grid_consumption") or {}
        # Why there is no figure, when there is none: "not enough charging
        # history yet" and "this car never streamed its capacity" want different
        # things from the user.
        attrs["status"] = profile.get("status")
        attrs["range_full_km"] = ranges.get("full_km")
        # The same figure as the state, in kilometres whatever the user's unit
        # system converts the state into -- the card renders kilometres from the
        # ledger throughout, and reading them off a converted state is exactly
        # how a display unit once leaked into a stored value (issue #7).
        attrs["range_now_km"] = ranges.get("now_km")
        attrs["soc_percent"] = ranges.get("soc_percent")
        attrs["consumption_kwh_per_100km"] = consumption.get("kwh_per_100km")
        # Always beside the figure: a consumption number without its side is
        # ambiguous by exactly the size of the charging loss.
        attrs["consumption_source"] = consumption.get("source")
        attrs["consumption_window_days"] = consumption.get("window_days")
        attrs["consumption_distance_km"] = consumption.get("distance_km")
        attrs["grid_consumption_kwh_per_100km"] = grid.get("kwh_per_100km")
        attrs["measured_loss_percent"] = profile.get("measured_loss_percent")
        attrs["capacity_kwh"] = profile.get("capacity_kwh")
        attrs["capacity_source"] = profile.get("capacity_source")
        attrs["bmw_range_km"] = ranges.get("bmw_km")
        attrs["bmw_range_full_km"] = ranges.get("bmw_full_km")
        attrs["vs_bmw_percent"] = ranges.get("vs_bmw_percent")
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
    _attr_icon = "mdi:road-variant"
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
        summary = self._summary
        split = summary.get("split") or {}
        attrs["trip_count"] = summary.get("trip_count", 0)
        attrs["business_km"] = split.get("business_km")
        attrs["private_km"] = split.get("private_km")
        attrs["commute_km"] = split.get("commute_km")
        attrs["unclassified_km"] = split.get("unclassified_km")
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


# Our wheel slugs -> the axle token BMW's streamed tire descriptors use, so a
# diagnosis entity and a pressure entity for the same wheel share a card slot.
_AXLE_ROWS = {"front": "row1", "rear": "row2"}


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
        attrs = dict(super().extra_state_attributes)
        # These have no catalogue descriptor, so the cluster the card groups by
        # has to be declared here. Must stay present even when unavailable.
        attrs["cluster"] = "tire"
        attrs["cluster_name"] = SECTIONS.get("tire", "Tire data")
        # When the diagnosis was last fetched. Worth exposing: it is refreshed
        # once a day at most and now survives restarts, so a reading can
        # legitimately be a day old. Mirrored onto ``timestamp`` so these carry
        # the same "as of" attribute as every streamed entity.
        if fetched_at := self._diagnosis.get("fetched_at"):
            attrs["fetched_at"] = fetched_at
            attrs.setdefault("timestamp", fetched_at)
        return attrs

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

    _attr_icon = "mdi:tire"

    def __init__(self, coordinator: CardataCoordinator, vin: str, position: str) -> None:
        # Set before super(): CardataEntity falls back to a computed English
        # _attr_name when it finds no translation key, and that name would then
        # win over the translated one for German installs.
        self._attr_translation_key = f"tyre_{position}"
        super().__init__(coordinator, vin, f"tyre_{position}")
        self._position = position

    @property
    def _wheel(self) -> Dict[str, Any]:
        return (self._diagnosis.get("wheels") or {}).get(self._position) or {}

    @property
    def native_value(self):
        wheel = self._wheel
        return wheel.get("wear_status_color") or wheel.get("wear_status")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        # Land on the same wheel slot as the streamed pressure/temperature
        # sensors: the card keys wheels by "<tire_axle>_<tire_side>", and those
        # come from BMW's descriptor path, which numbers axles rather than
        # naming them (vehicle.chassis.axle.row1.wheel.left.tire.pressure).
        axle, side = self._position.split("_", 1)
        attrs["tire_axle"] = _AXLE_ROWS[axle]
        attrs["tire_side"] = side
        attrs["tire_metric"] = "diagnosis"
        attrs.update(self._wheel)
        return attrs


class CardataTyreStatusSensor(CardataTyreEntity):
    """BMW's overall verdict on the mounted set, plus any upstream errors."""

    _attr_icon = "mdi:car-tire-alert"
    _attr_translation_key = "tyre_status"

    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "tyre_status")

    @property
    def native_value(self):
        return self._diagnosis.get("aggregated_status")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        diagnosis = self._diagnosis
        attrs["label"] = diagnosis.get("aggregated_label")
        attrs["wheels_reported"] = sorted(diagnosis.get("wheels") or {})
        errors = diagnosis.get("errors") or []
        if errors:
            # Surfaced rather than swallowed: an upstream outage is why the
            # wheels went empty, and that is worth being able to see.
            attrs["errors"] = errors
        return attrs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = entry.runtime_data
    coordinator: CardataCoordinator = runtime.coordinator

    entities: Dict[Tuple[str, str], CardataSensor] = {}
    soc_estimate_entities: Dict[str, CardataSocEstimateSensor] = {}
    soc_estimate_testing_entities: Dict[str, CardataTestingSocEstimateSensor] = {}
    soc_rate_entities: Dict[str, CardataSocRateSensor] = {}
    charged_energy_entities: Dict[str, CardataChargedEnergySensor] = {}
    session_energy_entities: Dict[str, CardataSessionEnergySensor] = {}
    charging_summary_entities: Dict[str, list] = {}
    battery_health_entities: Dict[str, CardataBatteryHealthSensor] = {}
    driving_entities: Dict[str, CardataDrivingDistanceMonthSensor] = {}
    real_range_entities: Dict[str, CardataRealRangeSensor] = {}
    # vin -> {"status"|<wheel position>: entity}
    tyre_entities: Dict[str, Dict[str, CardataTyreEntity]] = {}

    # A car that reports any of these can produce trips worth summarising; a
    # device that streams none (never driven, no odometer) gets no trip sensor
    # rather than one stuck at 0 km. Both odometer spellings count: the i5
    # streams ``travelledDistance`` and never ``mileage``, and gating on the
    # latter alone left the real car with no monthly-distance sensor at all.
    _DRIVE_SIGNALS = (*DESC_ODOMETER, "vehicle.isMoving")

    def ensure_driving_entity(vin: str, *, force: bool = False) -> None:
        """Create the monthly-distance sensor once the car looks drivable.

        ``force`` re-creates one restored from the registry without re-checking
        for a live signal, matching how battery health avoids the generic path
        minting a bogus CardataSensor on its id.
        """

        if vin in driving_entities or coordinator.history is None:
            return
        drivable = any(
            coordinator.get_state(vin, descriptor) is not None
            for descriptor in _DRIVE_SIGNALS
        )
        if not (force or drivable):
            return
        entity = CardataDrivingDistanceMonthSensor(coordinator, vin)
        driving_entities[vin] = entity
        async_add_entities([entity], True)

    # Any of these streaming marks the car as having an HV battery worth tracking
    # health for; a pure-ICE car streams none of them.
    _EV_SIGNALS = (
        "vehicle.drivetrain.batteryManagement.batterySizeMax",
        "vehicle.drivetrain.batteryManagement.maxEnergy",
        "vehicle.drivetrain.batteryManagement.header",
    )

    def ensure_battery_health_entity(vin: str, *, force: bool = False) -> None:
        """Create the battery-health sensor once the car looks like an EV.

        It self-explains via "Learning (n/10)" until it has data, so it is made
        as soon as any HV-battery signal appears -- but not for a pure-ICE car
        that will never charge, where it would sit at "Learning (0/10)" forever.
        ``force`` re-creates one that already existed (restored from the
        registry) without re-checking for a live signal.
        """

        if vin in battery_health_entities or coordinator.history is None:
            return
        has_battery = any(
            coordinator.get_state(vin, descriptor) is not None
            for descriptor in _EV_SIGNALS
        )
        if not (force or has_battery):
            return
        entity = CardataBatteryHealthSensor(coordinator, vin)
        battery_health_entities[vin] = entity
        async_add_entities([entity], True)

    def _has_odometer(vin: str) -> bool:
        return any(
            coordinator.get_state(vin, descriptor) is not None
            for descriptor in DESC_ODOMETER
        )

    def ensure_real_range_entity(vin: str, *, force: bool = False) -> None:
        """Create the real-range sensor for an EV that reports its odometer.

        Both halves are required by the arithmetic, not by taste: the range is
        usable capacity over consumption, and the consumption comes from the
        distance between two charging sessions' odometer readings. A car with no
        odometer would hold an entity that can never produce a number.
        ``force`` re-creates one restored from the registry without re-checking
        for a live signal.
        """

        if vin in real_range_entities or coordinator.history is None:
            return
        eligible = _has_odometer(vin) and any(
            coordinator.get_state(vin, descriptor) is not None
            for descriptor in _EV_SIGNALS
        )
        if not (force or eligible):
            return
        entity = CardataRealRangeSensor(coordinator, vin)
        real_range_entities[vin] = entity
        async_add_entities([entity], True)

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
            # Vehicle status cluster is optional in the portal. Either spelling
            # of the odometer will do -- see ``_DRIVE_SIGNALS``.
            if _has_odometer(vin):
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

    def ensure_tyre_entities(
        vin: str, *, positions: Any = None, status: bool = False
    ) -> None:
        """Create the tyre-diagnosis sensors for whichever wheels BMW reported.

        Only wheels present in the payload get an entity: a car with no tyre
        service record on file would otherwise gain four sensors permanently
        reading "unknown". ``positions``/``status`` re-create ones restored from
        the registry, before the first fetch of the day has landed.
        """

        known = tyre_entities.setdefault(vin, {})
        diagnosis = coordinator.tyre_diagnosis.get(vin) or {}
        wanted = (
            set(positions)
            if positions is not None
            else set(diagnosis.get("wheels") or {})
        )
        new_entities: list = []
        if (wanted or status) and "status" not in known:
            known["status"] = CardataTyreStatusSensor(coordinator, vin)
            new_entities.append(known["status"])
        for position in sorted(wanted):
            if position in known:
                continue
            known[position] = CardataTyreWheelSensor(coordinator, vin, position)
            new_entities.append(known[position])
        if new_entities:
            async_add_entities(new_entities, True)

    def ensure_entity(vin: str, descriptor: str, *, assume_sensor: bool = False) -> None:
        ensure_soc_tracking_entities(vin)
        ensure_charging_summary_entities(vin)
        ensure_battery_health_entity(vin)
        ensure_driving_entity(vin)
        ensure_real_range_entity(vin)
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
        if descriptor == "battery_health":
            # Re-create the one it had before live data arrives, rather than
            # letting the generic path mint a bogus CardataSensor on its id.
            ensure_battery_health_entity(vin, force=True)
            continue
        if descriptor == "driving_distance_month":
            ensure_driving_entity(vin, force=True)
            continue
        if descriptor == "real_range":
            ensure_real_range_entity(vin, force=True)
            continue
        if descriptor.startswith("tyre_"):
            # Re-create what the car had before today's fetch lands, so the
            # entity keeps its id and history instead of the generic path
            # minting a CardataSensor on the same unique id.
            if descriptor == "tyre_status":
                ensure_tyre_entities(vin, positions=set(), status=True)
            else:
                ensure_tyre_entities(vin, positions={descriptor[len("tyre_"):]})
            continue
        if descriptor in {
            "charging_energy_month",
            "charging_cost_month",
            "charging_cost_session",
            "charging_cost_per_100km",
        }:
            # These are minted by ensure_charging_summary_entities, not the
            # generic path; routing them there avoids assume_sensor also
            # creating a duplicate CardataSensor on the same unique id.
            ensure_charging_summary_entities(vin)
            continue
        ensure_entity(vin, descriptor, assume_sensor=True)

    for vin, descriptor in coordinator.iter_descriptors(binary=False):
        ensure_entity(vin, descriptor)

    for vin in list(coordinator.data.keys()):
        ensure_soc_tracking_entities(vin)
        ensure_battery_health_entity(vin)
        ensure_driving_entity(vin)
        ensure_real_range_entity(vin)

    for vin in list(coordinator.tyre_diagnosis):
        # The diagnosis restored from the tyre store (tyre_store.py) covers the
        # registry loop's case, but also the one it can't: a vehicle whose tyre
        # entities were removed from the registry still gets them back now
        # rather than waiting for the next daily fetch.
        ensure_tyre_entities(vin)

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

    async def async_handle_new_tyre(vin: str) -> None:
        # A later fetch can report a wheel the first one omitted, so this runs on
        # every diagnosis, not just the first.
        ensure_tyre_entities(vin)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_new_tyre", async_handle_new_tyre
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
