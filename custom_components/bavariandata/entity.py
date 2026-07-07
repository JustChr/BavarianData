"""Base entity classes for BMW CarData."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import CardataCoordinator
from .descriptor_metadata import DESCRIPTOR_META, SECTIONS
from .keys import translation_key

# vehicle.chassis.axle.row1.wheel.left.tire.pressure -> (row1, left, pressure)
_TIRE_RE = re.compile(
    r"vehicle\.chassis\.axle\.(row\d)\.wheel\.(left|right)\.tire\.(\w+)"
)


class CardataEntity(RestoreEntity):
    # The device carries the vehicle name; Home Assistant prepends it to the
    # translated entity name automatically.
    _attr_has_entity_name = True

    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        self._coordinator = coordinator
        self._vin = vin
        self._descriptor = descriptor
        self._attr_unique_id = f"{vin}_{descriptor}"

        meta = DESCRIPTOR_META.get(descriptor)
        if meta is not None:
            # Catalogue-backed descriptor: name/state come from translations.
            self._attr_translation_key = translation_key(descriptor)
            if meta.get("entity_category") == "diagnostic":
                self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = bool(
                meta.get("enabled_default", True)
            )
        elif getattr(self, "_attr_translation_key", None) is not None:
            # Subclass declared its own translation key (e.g. the device
            # tracker's "car"); leave its naming untouched.
            pass
        else:
            # Unknown descriptor (e.g. BMW added a new field, or an internal
            # helper sensor): fall back to a computed English name.
            self._attr_name = self._format_name()

        self._attr_available = True

    @property
    def device_info(self) -> DeviceInfo:
        metadata = self._coordinator.device_metadata.get(self._vin, {})
        name = metadata.get("name") or self._coordinator.names.get(self._vin, self._vin)
        manufacturer = metadata.get("manufacturer", "BMW")
        info: DeviceInfo = {
            "identifiers": {(DOMAIN, self._vin)},
            "manufacturer": manufacturer,
            "name": name,
        }
        if model := metadata.get("model"):
            info["model"] = model
        if sw_version := metadata.get("sw_version"):
            info["sw_version"] = sw_version
        if hw_version := metadata.get("hw_version"):
            info["hw_version"] = hw_version
        if serial := metadata.get("serial_number"):
            info["serial_number"] = serial
        return info

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {}
        # Static, state-independent metadata below — always exposed, even before
        # the first live value arrives. Otherwise a rarely-changing entity that
        # is only restored after a restart (e.g. a closed door) would carry no
        # ``cluster``/``descriptor`` until BMW next reports it, so the Lovelace
        # cards that group/auto-detect by those attributes would drop it.
        #
        # Expose the raw descriptor path so the Lovelace card can auto-detect
        # entities language-independently: friendly names / entity_ids are
        # localized (e.g. German "Reichweite"), but the descriptor is always the
        # English BMW path, so keyword matching stays reliable in any HA locale.
        attrs["descriptor"] = self._descriptor
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if state and state.timestamp:
            attrs["timestamp"] = state.timestamp
        # Expose the descriptor's cluster so the Lovelace card can group entities
        # by cluster (e.g. render one card per cluster) without duplicating the
        # catalogue's descriptor->section mapping in the frontend.
        meta = DESCRIPTOR_META.get(self._descriptor)
        if meta is not None:
            section = meta.get("section")
            if section:
                attrs["cluster"] = section
                attrs["cluster_name"] = SECTIONS.get(section, section)
            if category := meta.get("category"):
                attrs["category"] = category
            # Tire descriptors: expose axle/side/metric so the card can place each
            # wheel on a top-down diagram without parsing friendly names.
            if section == "tire":
                if tire_match := _TIRE_RE.match(self._descriptor):
                    attrs["tire_axle"] = tire_match.group(1)
                    attrs["tire_side"] = tire_match.group(2)
                    attrs["tire_metric"] = tire_match.group(3)
        metadata = self._coordinator.device_metadata.get(self._vin)
        if metadata:
            extra = metadata.get("extra_attributes")
            if extra:
                attrs.setdefault("vehicle_basic_data", dict(extra))
            raw = metadata.get("raw_data")
            if raw:
                attrs.setdefault("vehicle_basic_data_raw", dict(raw))
        return attrs

    @property
    def descriptor(self) -> str:
        return self._descriptor

    @property
    def vin(self) -> str:
        return self._vin

    def _format_name(self) -> str:
        # Reached only for descriptors absent from the catalogue (DESCRIPTOR_META),
        # so there is no curated title to use — derive a name from the path.
        parts = [
            p
            for p in self._descriptor.replace("_", " ").replace(".", " ").split()
            if p and p.lower() != "vehicle"
        ]
        title = " ".join(p.capitalize() for p in parts)
        return title or self._vin
