"""The Configure screens with settings a car holds for itself.

Charging costs, the evcc bridge, Trips and the mileage allowance describe a
car, so an account with more than one picks the car first and the screen
stores that car's values (``vehicle_options.py``); a single-car account sees
no picker. :class:`PerCarScreens` is mixed into ``CardataOptionsFlowHandler``
(``options_flow.py``) and relies on its ``_config_entry``, ``_get_runtime``,
``_picked_vin``, ``_trips_pending`` and ``_bridge_options``. The arithmetic
and checks are HA-free in ``flow_answers.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Mapping, Optional

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_BRIDGE_PREFIX,
    DEFAULT_BRIDGE_RETAIN,
    DEFAULT_HISTORY_RETAIN_MONTHS,
    DEFAULT_STATISTICS_IMPORT,
    DEFAULT_TRIP_COMMUTE_GAP_MIN,
    DEFAULT_TRIP_DEFAULT_CLASS,
    OPTION_BRIDGE_ENABLED,
    OPTION_BRIDGE_PREFIX,
    OPTION_BRIDGE_RETAIN,
    OPTION_CHARGE_LOCATIONS,
    OPTION_CHARGING_LOSS_PERCENT,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_HISTORY_RETAIN_MONTHS,
    OPTION_PRICE_CURRENCY,
    OPTION_PRICE_ENTITY,
    OPTION_PRICE_FIXED,
    OPTION_PRICE_MODE,
    OPTION_STATISTICS_IMPORT,
    OPTION_TRIP_COMMUTE_GAP,
    OPTION_TRIP_DEBUG,
    OPTION_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_GEOCODE,
    OPTION_TRIP_PHONE_IN_CAR,
    OPTION_TRIP_PHONE_IN_CAR_2,
    OPTION_TRIP_PHONE_TRACKER,
    OPTION_TRIP_PHONE_TRACKER_2,
    OPTION_TRIP_TRACK,
    OPTION_TRIP_WORK_ZONE,
    TRIP_DETECTION_CHOICES,
    TRIP_DETECTION_LIVE,
)
from .distance import car_distance_unit
from .evcc import BridgeConfig, bridge_payloads, evcc_yaml
from .flow_answers import (
    SHARED_SECTION,
    car_labels,
    evcc_vehicles,
    mileage_prefill,
    mileage_to_store,
    mileage_typed,
    phone_route_follows,
    price_error,
    split_shared,
    to_display,
)
from .history.classify import DEFAULT_CLASS_CHOICES
from .history.pricing import (
    DEFAULT_CURRENCY,
    MODE_ENTITY,
    MODE_FIXED,
    MODE_NONE,
    PricingConfig,
    vehicle_pricing,
)
from .mileage import (
    KEY_END as MILEAGE_END,
    KEY_KIND as MILEAGE_KIND,
    KEY_PER_YEAR as MILEAGE_PER_YEAR,
    KEY_START as MILEAGE_START,
    KEY_START_KM as MILEAGE_START_KM,
    KIND_LEASE,
    KIND_NONE,
    KIND_YEARLY,
    KINDS as MILEAGE_KINDS,
    allowance_settings,
)
from .trip_settings import account_trip_settings, vehicle_trip_settings
from .vehicle_options import (
    BRIDGE_OPTIONS,
    CHARGING_OPTIONS,
    TRIP_OPTIONS,
    mileage_allowances,
    mileage_values,
    store_mileage,
    store_values,
    values_for,
)

# The vehicle picker's form field (not an option).
_VEHICLE_FIELD = "vehicle"
# The phone screen's pairs (tracker, in-car sensor), in priority order.
PHONE_PAIRS = (
    (OPTION_TRIP_PHONE_TRACKER, OPTION_TRIP_PHONE_IN_CAR),
    (OPTION_TRIP_PHONE_TRACKER_2, OPTION_TRIP_PHONE_IN_CAR_2),
)
PHONE_KEYS = tuple(key for pair in PHONE_PAIRS for key in pair)
# ``_vehicle_context``'s "no car picked yet" (``None`` means "no car known").
_PICK = object()


if TYPE_CHECKING:
    from .runtime import CardataRuntimeData

    _FlowBase = OptionsFlow
else:
    _FlowBase = object


class PerCarScreens(_FlowBase):
    """The per-car screens of the options flow, and the picker in front of them."""

    if TYPE_CHECKING:
        # What ``CardataOptionsFlowHandler`` provides.
        _config_entry: ConfigEntry
        _picked_vin: Optional[str]
        _trips_pending: tuple[Dict[str, Any], Dict[str, Any]]
        _bridge_options: Optional[Dict[str, Any]]

        def _get_runtime(self) -> Optional[CardataRuntimeData]: ...

    # --- which car ----------------------------------------------------------

    def _vehicles(self) -> list[tuple[str, str]]:
        """``(vin, label)`` for each car on the account, in name order."""

        runtime = self._get_runtime()
        if runtime is None:
            return []
        coordinator = runtime.coordinator
        return car_labels(
            (
                vin,
                (coordinator.device_metadata.get(vin) or {}).get("name")
                or coordinator.names.get(vin)
                or vin,
            )
            for vin in coordinator.data
        )

    def _vehicle_context(self) -> tuple[list[tuple[str, str]], bool, Any]:
        """``(vehicles, per_vehicle, vin)`` for a screen with per-car settings.

        With more than one car the screen edits the car picked first, and
        ``vin`` is ``_PICK`` until one has been; with one (or none yet) it edits
        the account-wide settings on behalf of that car.
        """

        vehicles = self._vehicles()
        if len(vehicles) > 1:
            vin = self._picked_vin if self._picked_vin in dict(vehicles) else _PICK
            return vehicles, True, vin
        return vehicles, False, vehicles[0][0] if vehicles else None

    def _vehicle_label(self, vehicles: list[tuple[str, str]], vin: Optional[str]) -> str:
        return dict(vehicles).get(vin or "") or self._config_entry.title

    async def _vehicle_picker(
        self,
        *,
        step_id: str,
        user_input: Optional[Dict[str, Any]],
        then: Callable[[], Awaitable[ConfigFlowResult]],
    ) -> ConfigFlowResult:
        """Which car's settings a screen edits (only shown with two or more cars)."""

        vehicles = self._vehicles()
        if user_input is not None and user_input.get(_VEHICLE_FIELD) in dict(vehicles):
            self._picked_vin = user_input[_VEHICLE_FIELD]
            return await then()
        schema = vol.Schema(
            {
                vol.Required(_VEHICLE_FIELD, default=vehicles[0][0]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=vin, label=label)
                            for vin, label in vehicles
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        # Not the last step: HA labels the button "Next" instead of "Submit",
        # because nothing is saved until the car's own form is submitted.
        return self.async_show_form(step_id=step_id, data_schema=schema, last_step=False)

    async def async_step_trips_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        return await self._vehicle_picker(
            step_id="trips_vehicle", user_input=user_input, then=self.async_step_action_trips
        )

    async def async_step_charging_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        return await self._vehicle_picker(
            step_id="charging_vehicle",
            user_input=user_input,
            then=self.async_step_action_charging_costs,
        )

    async def async_step_bridge_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        return await self._vehicle_picker(
            step_id="bridge_vehicle", user_input=user_input, then=self.async_step_action_evcc_bridge
        )

    async def async_step_mileage_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        return await self._vehicle_picker(
            step_id="mileage_vehicle", user_input=user_input, then=self.async_step_action_mileage
        )

    # --- charging costs -----------------------------------------------------

    async def async_step_action_charging_costs(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Configure how charging energy is turned into money.

        Deliberately not a tariff editor: users already have a price entity from
        Tibber/Nordpool/aWATTar, or a flat rate they know. Until a mode other
        than "none" is picked, no cost entity is created at all.

        The wallbox meter and the charging losses describe a car -- two cars
        can charge at two wallboxes, through two different chargers -- so an
        account with more than one car picks the car first, and the tariff,
        history and statistics settings every car shares sit in the expanded
        "All vehicles" section below that car's own. A single-car account sees
        the form as it always was.
        """

        vehicles, per_vehicle, vin = self._vehicle_context()
        if vin is _PICK:
            return await self.async_step_charging_vehicle()

        options = dict(self._config_entry.options)
        car = values_for(options, vin, CHARGING_OPTIONS)
        price_fields = {
            vol.Required(
                OPTION_PRICE_MODE,
                default=options.get(OPTION_PRICE_MODE, MODE_NONE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_NONE, MODE_FIXED, MODE_ENTITY],
                    translation_key="price_mode",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                OPTION_PRICE_FIXED,
                description={"suggested_value": options.get(OPTION_PRICE_FIXED)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, step="any", mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                OPTION_PRICE_ENTITY,
                description={"suggested_value": options.get(OPTION_PRICE_ENTITY)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "input_number"])
            ),
            vol.Required(
                OPTION_PRICE_CURRENCY,
                default=options.get(OPTION_PRICE_CURRENCY, DEFAULT_CURRENCY),
            ): str,
        }
        car_fields = {
            vol.Optional(
                OPTION_GRID_ENERGY_ENTITY,
                description={"suggested_value": car.get(OPTION_GRID_ENERGY_ENTITY)},
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                OPTION_CHARGING_LOSS_PERCENT,
                default=car.get(OPTION_CHARGING_LOSS_PERCENT) or 0,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=30, step=0.5, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
        history_fields = {
            vol.Required(
                OPTION_HISTORY_RETAIN_MONTHS,
                default=options.get(OPTION_HISTORY_RETAIN_MONTHS, DEFAULT_HISTORY_RETAIN_MONTHS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=120, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                OPTION_STATISTICS_IMPORT,
                default=options.get(OPTION_STATISTICS_IMPORT, DEFAULT_STATISTICS_IMPORT),
            ): selector.BooleanSelector(),
            vol.Required(
                OPTION_CHARGE_LOCATIONS,
                default=bool(options.get(OPTION_CHARGE_LOCATIONS, False)),
            ): selector.BooleanSelector(),
        }
        if per_vehicle:
            # Expanded, unlike the other screens' shared sections: the price is
            # the main thing this screen is for.
            schema = vol.Schema(
                {
                    **car_fields,
                    vol.Required(SHARED_SECTION): section(
                        vol.Schema({**price_fields, **history_fields}), {"collapsed": False}
                    ),
                }
            )
        else:
            schema = vol.Schema({**price_fields, **car_fields, **history_fields})

        def _form(errors: Optional[Dict[str, str]] = None) -> ConfigFlowResult:
            return self.async_show_form(
                step_id="action_charging_costs",
                data_schema=schema,
                errors=errors,
                description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
            )

        if user_input is None:
            return _form()

        submitted, shared = split_shared(user_input, CHARGING_OPTIONS)
        if error := price_error(shared):
            return _form({"base": error})

        options.update(shared)
        options = store_values(
            options, submitted, CHARGING_OPTIONS, vin=vin, per_vehicle=per_vehicle
        )
        # Apply immediately rather than reloading the entry: BMW allows only one
        # concurrent stream per account, so a reload risks racing the reconnect.
        runtime = self._get_runtime()
        if runtime is not None:
            runtime.coordinator.pricing = PricingConfig.from_options(options)
            runtime.coordinator.vehicle_pricing = vehicle_pricing(options)
            if runtime.history is not None:
                months = int(shared.get(OPTION_HISTORY_RETAIN_MONTHS, 0) or 0)
                runtime.history.retain_months = months if months > 0 else None
                remember = bool(shared.get(OPTION_CHARGE_LOCATIONS))
                runtime.history.remember_locations = remember
                if not remember:
                    # Off means gone, not merely "no new ones".
                    runtime.history.forget_locations()
            if runtime.statistics is not None:
                enabled = bool(shared.get(OPTION_STATISTICS_IMPORT, True))
                was_enabled = runtime.statistics.enabled
                runtime.statistics.enabled = enabled
                if enabled:
                    # A changed tariff or retention window changes the series, so
                    # rebuild rather than wait for the next recorded session.
                    await runtime.statistics.async_publish(force=True)
                elif was_enabled:
                    # Turning it off has to take the published series with it --
                    # otherwise a stale mirror lingers on the Energy dashboard.
                    await runtime.statistics.async_remove()
        return self.async_create_entry(title="", data=options)

    # --- evcc bridge --------------------------------------------------------

    async def async_step_action_evcc_bridge(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Publish the car's live state for a charge controller to read.

        The one setting in this integration that *pushes* data to something
        which then acts on it, which is why it is off by default and why
        switching it off clears what was published rather than leaving evcc
        charging against a state of charge frozen at that moment.

        Whether a car is published is per car on an account with more than one
        (a company car charged elsewhere stays off the broker, VIN included);
        the topic prefix and retained flag are shared, in the collapsed "All
        vehicles" section.
        """

        vehicles, per_vehicle, vin = self._vehicle_context()
        if vin is _PICK:
            return await self.async_step_bridge_vehicle()

        options = dict(self._config_entry.options)
        before = BridgeConfig.from_options(options)
        car = values_for(options, vin, BRIDGE_OPTIONS)
        car_fields = {
            vol.Required(
                OPTION_BRIDGE_ENABLED, default=bool(car.get(OPTION_BRIDGE_ENABLED))
            ): selector.BooleanSelector(),
        }
        shared_fields = {
            vol.Optional(
                OPTION_BRIDGE_PREFIX,
                description={
                    "suggested_value": options.get(OPTION_BRIDGE_PREFIX) or DEFAULT_BRIDGE_PREFIX
                },
            ): selector.TextSelector(),
            vol.Required(
                OPTION_BRIDGE_RETAIN,
                default=bool(options.get(OPTION_BRIDGE_RETAIN, DEFAULT_BRIDGE_RETAIN)),
            ): selector.BooleanSelector(),
        }
        if per_vehicle:
            schema = vol.Schema(
                {
                    **car_fields,
                    vol.Required(SHARED_SECTION): section(
                        vol.Schema(shared_fields), {"collapsed": True}
                    ),
                }
            )
        else:
            schema = vol.Schema({**car_fields, **shared_fields})

        def _form(errors: Optional[Dict[str, str]] = None) -> ConfigFlowResult:
            return self.async_show_form(
                step_id="action_evcc_bridge",
                data_schema=schema,
                errors=errors,
                description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
            )

        if user_input is None:
            return _form()

        submitted, shared = split_shared(user_input, BRIDGE_OPTIONS)

        # Refuse to switch on a bridge with nowhere to publish. Tested on the
        # *presence* of an MQTT config entry, not on it being loaded: "you have
        # not set up MQTT" is unambiguous and actionable, while a broker that
        # happens to be reconnecting is not the user's mistake to fix here.
        if submitted.get(OPTION_BRIDGE_ENABLED) and not self.hass.config_entries.async_entries(
            "mqtt"
        ):
            return _form({"base": "mqtt_missing"})

        options.update(shared)
        options = store_values(options, submitted, BRIDGE_OPTIONS, vin=vin, per_vehicle=per_vehicle)
        after = BridgeConfig.from_options(options)
        runtime = self._get_runtime()
        bridge = None if runtime is None else runtime.bridge
        if runtime is not None and bridge is not None:
            # Switched off -- the whole bridge or one car: remove what was
            # published. Otherwise a charge controller keeps charging against a
            # state of charge that stopped updating the moment the switch was
            # cleared -- the one genuinely dangerous failure mode of pushing
            # data out.
            if before.any_enabled and not after.any_enabled:
                await bridge.async_clear_all()
            else:
                for known in runtime.coordinator.data:
                    if before.publishes(known) and not after.publishes(known):
                        await bridge.async_clear_vehicle(known)
            # Applied in place rather than by reloading the entry: BMW allows
            # one concurrent stream per account, so a reload risks racing the
            # reconnect (same reason as the energy-sources step).
            bridge.apply_config(after)
            if bridge.enabled:
                await bridge.async_publish_all(force=True)
        self._bridge_options = options
        return await self.async_step_evcc_snippet()

    async def async_step_evcc_snippet(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Hand over the paste-ready evcc config, then save.

        Shown here as well as through the ``get_evcc_config`` action because
        this is the moment it is wanted: the user has just switched the bridge
        on, and the next thing they do is edit ``evcc.yaml``.
        """

        options = self._bridge_options or dict(self._config_entry.options)
        if user_input is None:
            return self.async_show_form(
                step_id="evcc_snippet",
                data_schema=vol.Schema({}),
                description_placeholders={"snippet": self._evcc_snippet(options)},
            )
        return self.async_create_entry(title="", data=options)

    def _evcc_snippet(self, options: Dict[str, Any]) -> str:
        """Every known car as one evcc ``vehicles:`` block.

        Only topics that are actually being published are referenced -- a
        plugin pointed at a topic no message ever arrives on makes evcc log a
        read error on every update cycle.
        """

        config = BridgeConfig.from_options(options)
        runtime = self._get_runtime()
        if not config.any_enabled or runtime is None:
            return "(the bridge is switched off -- nothing is published)"
        coordinator = runtime.coordinator
        blocks = []
        for vin in coordinator.data:
            if not config.publishes(vin):
                continue
            metadata = coordinator.device_metadata.get(vin, {})
            blocks.append(
                evcc_yaml(
                    prefix=config.prefix,
                    vin=vin,
                    title=metadata.get("name") or coordinator.names.get(vin),
                    capacity_kwh=coordinator.battery_capacity_kwh(vin),
                    topics=tuple(bridge_payloads(coordinator.bridge_snapshot(vin))) or None,
                )
            )
        return evcc_vehicles(blocks)

    # --- trips --------------------------------------------------------------

    async def async_step_action_trips(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Configure trip classification, detection and address resolution.

        Most trip settings describe a car (or whoever drives it), so an account
        with more than one car picks the car first (``trips_vehicle``) and its
        form stores a complete set for that car alone (see
        ``trip_settings.py``). A single-car account sees no picker and keeps
        editing the account-wide keys. Address resolution and the trip-capture
        diagnostics stay account-wide; with several cars they sit in a
        collapsed "All vehicles" section, so it is plain they are shared.

        The work zone drives commute classification (home <-> work); the default
        type is what every other trip gets, and the commute stop tolerance lets a
        commute survive an errand on the way (see ``history/classify.py``).
        Address resolution is off by default: turning it on sends the coordinates
        of trip endpoints outside a known zone to OpenStreetMap's Nominatim -- the
        resulting address string is stored, never the coordinates themselves.
        Route recording is a separate opt-in: turning it on stores each trip's
        GPS track (raw coordinates, each stamped with its time) so a map can draw
        where -- and when -- the car went. The one trip setting that persists
        coordinates to disk.
        """

        vehicles, per_vehicle, vin = self._vehicle_context()
        if vin is _PICK:
            return await self.async_step_trips_vehicle()

        options = dict(self._config_entry.options)
        # What applies to this car right now: its own settings if it has any.
        values = values_for(options, vin, TRIP_OPTIONS)
        car_fields = {
            vol.Optional(
                OPTION_TRIP_WORK_ZONE,
                description={"suggested_value": values.get(OPTION_TRIP_WORK_ZONE)},
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="zone")),
            vol.Required(
                OPTION_TRIP_DEFAULT_CLASS,
                default=values.get(OPTION_TRIP_DEFAULT_CLASS, DEFAULT_TRIP_DEFAULT_CLASS),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(DEFAULT_CLASS_CHOICES),
                    translation_key=OPTION_TRIP_DEFAULT_CLASS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                OPTION_TRIP_COMMUTE_GAP,
                default=values.get(OPTION_TRIP_COMMUTE_GAP, DEFAULT_TRIP_COMMUTE_GAP_MIN),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=180,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                OPTION_TRIP_DETECTION,
                default=values.get(OPTION_TRIP_DETECTION, TRIP_DETECTION_LIVE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(TRIP_DETECTION_CHOICES),
                    translation_key=OPTION_TRIP_DETECTION,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                OPTION_TRIP_TRACK,
                default=values.get(OPTION_TRIP_TRACK, False),
            ): selector.BooleanSelector(),
        }
        shared_fields = {
            vol.Required(
                OPTION_TRIP_GEOCODE,
                default=options.get(OPTION_TRIP_GEOCODE, False),
            ): selector.BooleanSelector(),
            vol.Required(
                OPTION_TRIP_DEBUG,
                default=options.get(OPTION_TRIP_DEBUG, False),
            ): selector.BooleanSelector(),
        }
        if per_vehicle:
            schema = vol.Schema(
                {
                    **car_fields,
                    vol.Required(SHARED_SECTION): section(
                        vol.Schema(shared_fields), {"collapsed": True}
                    ),
                }
            )
        else:
            schema = vol.Schema({**car_fields, **shared_fields})

        if user_input is None:
            return self.async_show_form(
                step_id="action_trips",
                data_schema=schema,
                description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
                # *Next* where the phone screen follows with the settings in
                # force; a form can't know what is about to be picked.
                last_step=not phone_route_follows(values),
            )

        submitted = dict(user_input)
        shared = submitted.pop(SHARED_SECTION, None) or {
            key: submitted[key]
            for key in (OPTION_TRIP_GEOCODE, OPTION_TRIP_DEBUG)
            if key in submitted
        }
        # Not on this form: kept as they are unless the phone screen changes them.
        for key in PHONE_KEYS:
            submitted[key] = values.get(key)
        if phone_route_follows(submitted):
            self._trips_pending = (submitted, shared)
            return await self.async_step_trips_phone()
        return self._save_trips(submitted, shared)

    async def async_step_trips_phone(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Where the route of a car on end-of-trip reports comes from.

        Shown after Trips only to a car that records its route and reports only
        once parked: such a car sends nothing while driving, so its route can
        come from a phone that rides along (``phone_route.py``).
        """

        vehicles, _per_vehicle, vin = self._vehicle_context()
        submitted, shared = self._trips_pending
        errors: dict[str, str] = {}
        if user_input is not None:
            given = {key: user_input.get(key) or None for key in PHONE_KEYS}
            for tracker_key, in_car_key in PHONE_PAIRS:
                if given[in_car_key] and not given[tracker_key]:
                    errors[in_car_key] = "phone_required"
            if not errors:
                return self._save_trips({**submitted, **given}, shared)
            submitted = {
                **submitted,
                **{tracker_key: given[tracker_key] for tracker_key, _ in PHONE_PAIRS},
            }
        schema = vol.Schema(
            {
                vol.Optional(
                    OPTION_TRIP_PHONE_TRACKER,
                    description={"suggested_value": submitted.get(OPTION_TRIP_PHONE_TRACKER)},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="device_tracker")),
                vol.Optional(
                    OPTION_TRIP_PHONE_IN_CAR,
                    description={"suggested_value": submitted.get(OPTION_TRIP_PHONE_IN_CAR)},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
                vol.Optional(
                    OPTION_TRIP_PHONE_TRACKER_2,
                    description={"suggested_value": submitted.get(OPTION_TRIP_PHONE_TRACKER_2)},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="device_tracker")),
                vol.Optional(
                    OPTION_TRIP_PHONE_IN_CAR_2,
                    description={"suggested_value": submitted.get(OPTION_TRIP_PHONE_IN_CAR_2)},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            }
        )
        return self.async_show_form(
            step_id="trips_phone",
            data_schema=schema,
            errors=errors,
            description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
        )

    def _save_trips(self, submitted: Dict[str, Any], shared: Mapping[str, Any]) -> ConfigFlowResult:
        _vehicles, per_vehicle, vin = self._vehicle_context()
        options = dict(self._config_entry.options)
        options = store_values(options, submitted, TRIP_OPTIONS, vin=vin, per_vehicle=per_vehicle)
        options[OPTION_TRIP_GEOCODE] = bool(shared.get(OPTION_TRIP_GEOCODE))
        options[OPTION_TRIP_DEBUG] = bool(shared.get(OPTION_TRIP_DEBUG))
        # Apply immediately rather than reloading (one concurrent stream per
        # account means a reload risks racing the reconnect).
        runtime = self._get_runtime()
        if runtime is not None:
            coordinator = runtime.coordinator
            # Classification settings apply to the next trip that closes; already
            # stored trips keep the class they were recorded with (correct them
            # from the card). Route recording takes effect on the next trip that
            # opens. Detection applies from the next report or fix on: a live
            # trip already open still closes on its timers; in ``reports`` mode
            # no new one opens.
            coordinator.trip_settings = account_trip_settings(options)
            coordinator.vehicle_trip_settings = vehicle_trip_settings(options)
            # A phone route starts with the next trip; positions held so far
            # are let go, since they were gathered under the old settings.
            coordinator.watch_phone_routes()
            if coordinator.geocoder is not None:
                coordinator.geocoder.enabled = options[OPTION_TRIP_GEOCODE]
            # Trip-capture diagnostics apply immediately (next message onward).
            coordinator.trip_debug = options[OPTION_TRIP_DEBUG]
        return self.async_create_entry(title="", data=options)

    # --- mileage allowance --------------------------------------------------
    #
    # A lease or a yearly limit (insurance) per car, compared with the odometer
    # log (``mileage.py``). Always the car's own -- even on a single-car account
    # -- so a car added later never inherits a lease. Two screens: the kind,
    # then that kind's form (HA forms can't show fields conditionally).

    def _miles(self, vin: str) -> bool:
        """Whether the car counts in miles (``distance.car_distance_unit``).

        Not Home Assistant's unit system alone: a UK install is metric but its
        car counts in miles, and typed miles were stored as km (issue #126).
        """

        runtime = self._get_runtime()
        coordinator = runtime.coordinator if runtime is not None else None
        return car_distance_unit(self.hass, coordinator, vin) == "mi"

    def _mileage_number(self, *, maximum: int, miles: bool) -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=maximum,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="mi" if miles else "km",
            )
        )

    async def async_step_action_mileage(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Which kind of mileage allowance the car has, if any."""

        vehicles, _per_vehicle, vin = self._vehicle_context()
        if vin is _PICK:
            return await self.async_step_mileage_vehicle()
        current = mileage_values(self._config_entry.options, vin)
        kind = current.get(MILEAGE_KIND) if allowance_settings(current) else KIND_NONE
        schema = vol.Schema(
            {
                vol.Required(MILEAGE_KIND, default=kind): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(MILEAGE_KINDS),
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key=MILEAGE_KIND,
                    )
                )
            }
        )
        placeholders = {"vehicle": self._vehicle_label(vehicles, vin)}
        errors: Dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get(MILEAGE_KIND)
            if vin is None:
                errors["base"] = "no_vins"
            elif chosen == KIND_LEASE:
                return await self.async_step_mileage_lease()
            elif chosen == KIND_YEARLY:
                return await self.async_step_mileage_yearly()
            else:
                return self._save_mileage(vin, {MILEAGE_KIND: KIND_NONE})
        # Not the last step unless "None" is picked; "Next" fits every choice.
        return self.async_show_form(
            step_id="action_mileage",
            data_schema=schema,
            description_placeholders=placeholders,
            errors=errors,
            last_step=False,
        )

    def _save_mileage(self, vin: str, values: Dict[str, Any]) -> ConfigFlowResult:
        options = store_mileage(dict(self._config_entry.options), vin, values)
        runtime = self._get_runtime()
        if runtime is not None:
            # Applies at once: the sensor and the card recompute on the signal.
            runtime.coordinator.mileage_allowances = mileage_allowances(options)
            async_dispatcher_send(self.hass, runtime.coordinator.signal_mileage, vin)
        return self.async_create_entry(title="", data=options)

    async def async_step_mileage_lease(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """A lease or financing: start, end, allowance a year, odometer at handover."""

        return await self._mileage_form(user_input, step_id="mileage_lease", kind=KIND_LEASE)

    async def async_step_mileage_yearly(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """A yearly limit: the day it renews, the allowance, the odometer that day."""

        return await self._mileage_form(user_input, step_id="mileage_yearly", kind=KIND_YEARLY)

    async def _mileage_form(
        self, user_input: Optional[Dict[str, Any]], *, step_id: str, kind: str
    ) -> ConfigFlowResult:
        vehicles, _per_vehicle, vin = self._vehicle_context()
        assert isinstance(vin, str)  # the kind step refuses an account without a car
        miles = self._miles(vin)
        current = mileage_values(self._config_entry.options, vin)
        values = dict(current) if current.get(MILEAGE_KIND) == kind else {}
        if user_input is not None:
            values = mileage_typed(user_input, miles)
        else:
            values = mileage_prefill(values, kind, dt_util.now().date())
        start_km = values.get(MILEAGE_START_KM)
        if start_km is None and user_input is None:
            # The handover reading is unknown to us; today's is the honest
            # prefill, and the field's help line says so. A car on screen means
            # a running entry: without one there are no cars to pick.
            runtime = self._get_runtime()
            start_km = None if runtime is None else runtime.coordinator.odometer_km(vin)
        fields: Dict[Any, Any] = {
            vol.Required(
                MILEAGE_START, description={"suggested_value": values.get(MILEAGE_START)}
            ): selector.DateSelector(),
        }
        if kind == KIND_LEASE:
            fields[
                vol.Required(MILEAGE_END, description={"suggested_value": values.get(MILEAGE_END)})
            ] = selector.DateSelector()
        fields[
            vol.Required(
                MILEAGE_PER_YEAR,
                description={"suggested_value": to_display(values.get(MILEAGE_PER_YEAR), miles)},
            )
        ] = self._mileage_number(maximum=200000, miles=miles)
        fields[
            vol.Required(
                MILEAGE_START_KM, description={"suggested_value": to_display(start_km, miles)}
            )
        ] = self._mileage_number(maximum=2000000, miles=miles)
        placeholders = {"vehicle": self._vehicle_label(vehicles, vin)}

        errors: Dict[str, str] = {}
        if user_input is not None:
            stored, errors = mileage_to_store(kind, values, dt_util.now().date())
            if not errors:
                return self._save_mileage(vin, stored)
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(fields),
            description_placeholders=placeholders,
            errors=errors,
        )
