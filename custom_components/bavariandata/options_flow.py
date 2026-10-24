"""The Configure menu (options flow) of a BavarianData account.

Settings first, the one-off tools one level down: the account-wide screens
(streamed data, the car's picture, energy sources, units, refresh on start),
"Fetch now" (each entry spends quota) and "Connection" (sign in again, renew
the token, reset the containers, debug logging). The screens with settings a
car holds for itself are in ``options_cars.py``; streamed data runs the
activator in ``flow_activator.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import account_vins, async_manual_refresh_tokens
from .const import (
    DEBUG_LOG,
    DEFAULT_IMAGE_SOURCE,
    DEFAULT_REFRESH_ON_START,
    DOMAIN,
    IMAGE_SOURCE_CONFIGURATOR,
    IMAGE_SOURCE_OFFICIAL,
    OPTION_BATTERY_POWER_ENTITY,
    OPTION_BATTERY_POWER_INVERT,
    OPTION_DEBUG_LOG,
    OPTION_GRID_POWER_ENTITY,
    OPTION_IMAGE_SOURCE,
    OPTION_PRICE_SOLAR,
    OPTION_PV_POWER_ENTITY,
    OPTION_REFRESH_ON_START,
    OPTION_STREAM_SECTIONS,
    OPTION_UNIT_DISTANCE,
    OPTION_UNIT_FUEL,
    OPTION_UNIT_PRESSURE,
    OPTION_UNIT_TEMPERATURE,
    SIGNAL_VEHICLE_IMAGE,
)
from .container import KINDS, CardataContainerError
from .debug import set_debug_enabled
from .descriptors import default_sections, descriptors_for_sections, section_labels
from .device_flow import CardataAuthError
from .distance import async_apply_sensor_units, car_distance_unit, car_fuel_unit
from .flow_activator import StreamActivatorFlow, schedule_vehicle_discovery
from .flow_answers import in_catalogue_order, units_line
from .history.pricing import PricingConfig, vehicle_pricing
from .options_cars import PerCarScreens
from .units import UNIT_CHOICES, unit_choices


class CardataOptionsFlowHandler(StreamActivatorFlow, PerCarScreens, config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        # Carried between the bridge step and the snippet screen that follows it,
        # so the snippet describes the settings just submitted rather than the
        # ones still on disk.
        self._bridge_options: Optional[Dict[str, Any]] = None
        # The car whose settings are being edited (multi-car accounts).
        self._picked_vin: Optional[str] = None
        # A Trips form waiting for the phone screen: ``(car values, shared)``.
        self._trips_pending: tuple[Dict[str, Any], Dict[str, Any]] = ({}, {})
        self._init_activator_state()

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        # Labels/descriptions come from translations (options.step.init.menu_options),
        # so the menu is localizable and stays in sync with each action step.
        # Settings first; the one-off tools sit one level down, so the menu
        # people open to change a setting is a list of settings.
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "action_select_clusters",
                "action_fetch_image",
                "action_charging_costs",
                "action_energy_sources",
                "action_evcc_bridge",
                "action_trips",
                "action_mileage",
                "action_units",
                "action_data_refresh",
                "fetch_now",
                "connection",
            ],
        )

    async def async_step_fetch_now(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Ask BMW for something now; every entry spends quota."""

        return self.async_show_menu(
            step_id="fetch_now",
            menu_options=[
                "action_fetch_mappings",
                "action_fetch_telematic",
                "action_fetch_basic",
                "action_fetch_charging_history",
                "action_fetch_tyre",
                "action_fetch_location_charging",
            ],
        )

    async def async_step_connection(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Keep the link to BMW working, and log for a bug report."""

        return self.async_show_menu(
            step_id="connection",
            menu_options=[
                "action_reauth",
                "action_refresh_tokens",
                "action_reset_container",
                "action_debug_logging",
            ],
        )

    def _show_confirm(
        self,
        *,
        step_id: str,
        errors: Optional[Dict[str, str]] = None,
        placeholders: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        # An empty schema renders as a plain confirmation dialog: pressing "Submit"
        # confirms the action. No checkbox to tick first.
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _get_runtime(self):
        return getattr(self._config_entry, "runtime_data", None)

    def _finish(self) -> FlowResult:
        # Finishing an options flow overwrites entry.options with this data, so
        # carry the existing options forward (e.g. debug_log) instead of
        # clearing them every time an action step runs.
        return self.async_create_entry(title="", data=dict(self._config_entry.options))

    # --- connection ---------------------------------------------------------

    async def async_step_action_refresh_tokens(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        if user_input is None:
            return self._show_confirm(step_id="action_refresh_tokens")
        try:
            await async_manual_refresh_tokens(self.hass, self._config_entry)
        except CardataAuthError as err:
            return self._show_confirm(
                step_id="action_refresh_tokens",
                errors={"base": "refresh_failed"},
                placeholders={"error": str(err)},
            )
        return self._finish()

    async def async_step_action_reauth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Sign in to BMW again with the account's Client ID.

        Another Client ID is Reconfigure's job (``config_flow``), where a
        client of another BMW account is refused.
        """

        if user_input is None:
            return self._show_confirm(step_id="action_reauth")
        entry = self._config_entry
        client_id = entry.data.get("client_id") or ""
        runtime = self._get_runtime()
        if runtime:
            runtime.reauth.started_by_user()

        flow_result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            # Home Assistant requires a reauth flow's context to link the entry
            # (via entry_id); without it async_init raises "Cannot initialize a
            # reauth flow without a link to the config entry".
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data={"client_id": client_id, "entry_id": entry.entry_id},
        )
        if flow_result["type"] == FlowResultType.ABORT:
            return self.async_abort(
                reason=flow_result.get("reason", "reauth_failed"),
                description_placeholders=flow_result.get("description_placeholders"),
            )
        return self.async_abort(reason="reauth_started")

    async def async_step_action_reset_container(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_reset_container",
                errors={"base": "runtime_missing"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_reset_container")

        entry = self._config_entry
        updated = dict(entry.data)
        try:
            async with runtime.container_lock:
                for kind in KINDS:
                    manager = runtime.containers[kind.key]
                    new_id = await manager.async_reset_hv_container()
                    updated[kind.id_key] = new_id
                    updated[kind.signature_key] = manager.descriptor_signature
        except CardataAuthError as err:
            self.hass.config_entries.async_update_entry(entry, data=updated)
            return self._show_confirm(
                step_id="action_reset_container",
                errors={"base": "refresh_failed"},
                placeholders={"error": str(err)},
            )
        except CardataContainerError as err:
            self.hass.config_entries.async_update_entry(entry, data=updated)
            return self._show_confirm(
                step_id="action_reset_container",
                errors={"base": "reset_failed"},
                placeholders={"error": str(err)},
            )
        self.hass.config_entries.async_update_entry(entry, data=updated)

        return self._finish()

    async def async_step_action_debug_logging(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Toggle the integration's verbose debug logging.

        This gates the ``debug_enabled()`` calls throughout the integration; it
        is separate from Home Assistant's generic per-integration log level.
        """

        current = bool(self._config_entry.options.get(OPTION_DEBUG_LOG, DEBUG_LOG))
        schema = vol.Schema(
            {
                vol.Required(OPTION_DEBUG_LOG, default=current): bool,
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="action_debug_logging",
                data_schema=schema,
            )
        enabled = bool(user_input.get(OPTION_DEBUG_LOG, False))
        # Apply immediately: options changes don't trigger a reload, and
        # set_debug_enabled() is otherwise only called from async_setup_entry.
        # Recorded against this entry, so switching it off here cannot silence
        # a second account that still has it on (see debug.py).
        set_debug_enabled(enabled, entry_id=self._config_entry.entry_id)
        options = dict(self._config_entry.options)
        options[OPTION_DEBUG_LOG] = enabled
        return self.async_create_entry(title="", data=options)

    # --- fetch now ----------------------------------------------------------

    def _collect_vins(self) -> list[str]:
        runtime = self._get_runtime()
        return account_vins(self.hass, self._config_entry, runtime.coordinator if runtime else None)

    async def async_step_action_fetch_mappings(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_mappings",
                errors={"base": "runtime_missing"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_mappings")
        return await self._call_for_each("action_fetch_mappings", "fetch_vehicle_mappings", [None])

    async def async_step_action_fetch_basic(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_basic",
                errors={"base": "runtime_missing"},
            )
        vins = self._collect_vins()
        if not vins:
            return self._show_confirm(
                step_id="action_fetch_basic",
                errors={"base": "no_vins"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_basic")
        return await self._call_for_each("action_fetch_basic", "fetch_basic_data", sorted(vins))

    async def async_step_action_fetch_telematic(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_telematic",
                errors={"base": "runtime_missing"},
            )
        if user_input is None:
            return self._show_confirm(step_id="action_fetch_telematic")
        return await self._call_for_each("action_fetch_telematic", "fetch_telematic_data", [None])

    async def async_step_action_fetch_charging_history(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._run_simple_service(
            step_id="action_fetch_charging_history",
            service="fetch_charging_history",
            user_input=user_input,
        )

    async def async_step_action_fetch_tyre(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._run_simple_service(
            step_id="action_fetch_tyre",
            service="fetch_tyre_diagnosis",
            user_input=user_input,
        )

    async def async_step_action_fetch_location_charging(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._run_simple_service(
            step_id="action_fetch_location_charging",
            service="fetch_location_charging_settings",
            user_input=user_input,
        )

    async def _call_for_each(
        self, step_id: str, service: str, vins: list[Optional[str]]
    ) -> FlowResult:
        """Call ``service`` once per car (``None``: for the account) and finish.

        One call per car because a call naming none is refused once the account
        has two. A refused call -- no quota left, no token -- comes back on the
        form rather than as Home Assistant's "Unknown error".
        """

        for vin in vins:
            data: Dict[str, Any] = {"entry_id": self._config_entry.entry_id}
            if vin is not None:
                data["vin"] = vin
            try:
                await self.hass.services.async_call(DOMAIN, service, data, blocking=True)
            except HomeAssistantError as err:
                return self._show_confirm(
                    step_id=step_id,
                    errors={"base": "fetch_failed"},
                    placeholders={"error": str(err)},
                )
        return self._finish()

    async def _run_simple_service(
        self, *, step_id: str, service: str, user_input: Optional[Dict[str, Any]]
    ) -> FlowResult:
        """Confirm-then-call helper for read-only VIN-scoped API services."""

        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(step_id=step_id, errors={"base": "runtime_missing"})
        vins = sorted(self._collect_vins())
        if not vins:
            return self._show_confirm(step_id=step_id, errors={"base": "no_vins"})
        if user_input is None:
            return self._show_confirm(step_id=step_id)
        return await self._call_for_each(step_id, service, vins)

    # --- account-wide settings ----------------------------------------------

    async def async_step_action_energy_sources(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Point the integration at the house's own meters.

        Separate from the tariff step on purpose: "what did this kilowatt-hour
        cost" and "where did it come from" are different questions, and either
        is worth answering without the other. Both power entities are needed
        before anything is attributed -- with only one the split would have to
        assume the other, which is the guesswork this replaces.
        """

        options = dict(self._config_entry.options)
        schema = vol.Schema(
            {
                vol.Optional(
                    OPTION_PV_POWER_ENTITY,
                    description={"suggested_value": options.get(OPTION_PV_POWER_ENTITY)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Optional(
                    OPTION_GRID_POWER_ENTITY,
                    description={"suggested_value": options.get(OPTION_GRID_POWER_ENTITY)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Optional(
                    OPTION_BATTERY_POWER_ENTITY,
                    description={"suggested_value": options.get(OPTION_BATTERY_POWER_ENTITY)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(
                    OPTION_BATTERY_POWER_INVERT,
                    default=bool(options.get(OPTION_BATTERY_POWER_INVERT, False)),
                ): selector.BooleanSelector(),
                vol.Optional(
                    OPTION_PRICE_SOLAR,
                    description={"suggested_value": options.get(OPTION_PRICE_SOLAR)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, step="any", mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="action_energy_sources", data_schema=schema)

        # One meter without the other cannot attribute anything, so say so here
        # rather than letting every session record an "unknown" mix.
        if bool(user_input.get(OPTION_PV_POWER_ENTITY)) != bool(
            user_input.get(OPTION_GRID_POWER_ENTITY)
        ):
            return self.async_show_form(
                step_id="action_energy_sources",
                data_schema=schema,
                errors={"base": "mix_needs_both"},
            )

        options.update(user_input)
        # Applied in place rather than by reloading the entry: BMW allows one
        # concurrent stream per account, so a reload risks racing the reconnect.
        runtime = self._get_runtime()
        if runtime is not None:
            runtime.coordinator.pricing = PricingConfig.from_options(options)
            runtime.coordinator.vehicle_pricing = vehicle_pricing(options)
        return self.async_create_entry(title="", data=options)

    async def async_step_action_data_refresh(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Choose whether to catch up over REST after Home Assistant starts.

        The rules that keep it cheap live in ``startup_refresh.py``. Read once per
        start, so there is nothing to apply now.
        """

        options = dict(self._config_entry.options)
        schema = vol.Schema(
            {
                vol.Required(
                    OPTION_REFRESH_ON_START,
                    default=options.get(OPTION_REFRESH_ON_START, DEFAULT_REFRESH_ON_START),
                ): selector.BooleanSelector(),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="action_data_refresh", data_schema=schema)
        options[OPTION_REFRESH_ON_START] = bool(user_input.get(OPTION_REFRESH_ON_START))
        return self.async_create_entry(title="", data=options)

    # --- units --------------------------------------------------------------
    #
    # How values are shown, account-wide (``units.py``). Display only: what is
    # recorded stays km, litres, °C and bar, so switching never changes history.

    _UNIT_FIELDS = (
        (OPTION_UNIT_DISTANCE, "distance"),
        (OPTION_UNIT_FUEL, "fuel"),
        (OPTION_UNIT_TEMPERATURE, "temperature"),
        (OPTION_UNIT_PRESSURE, "pressure"),
    )

    def _has_fuel_car(self) -> bool:
        runtime = self._get_runtime()
        if runtime is None:
            return False
        coordinator = runtime.coordinator
        return any(
            coordinator.is_combustion_only(vin) or coordinator.is_plug_in_hybrid(vin)
            for vin in coordinator.data
        )

    def _units_now(self, fuel: bool) -> str:
        """What each car is shown in right now (``flow_answers.units_line``)."""

        runtime = self._get_runtime()
        coordinator = runtime.coordinator if runtime is not None else None
        return units_line(
            (
                label,
                car_distance_unit(self.hass, coordinator, vin),
                car_fuel_unit(self.hass, coordinator, vin) if fuel else None,
            )
            for vin, label in self._vehicles()
        )

    async def async_step_action_units(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Choose the units values are shown in; Automatic keeps today's rules."""

        options = dict(self._config_entry.options)
        current = unit_choices(options)
        fuel = self._has_fuel_car()
        if user_input is not None:
            for key, quantity in self._UNIT_FIELDS:
                if key in user_input and user_input[key] in UNIT_CHOICES[quantity]:
                    options[key] = user_input[key]
            runtime = self._get_runtime()
            if runtime is not None:
                runtime.coordinator.display_units = unit_choices(options)
                async_apply_sensor_units(
                    self.hass, self._config_entry.entry_id, runtime.coordinator
                )
                # The card reads the choice off the connection sensor.
                async_dispatcher_send(self.hass, runtime.coordinator.signal_diagnostics)
            return self.async_create_entry(title="", data=options)
        fields: Dict[Any, Any] = {}
        for key, quantity in self._UNIT_FIELDS:
            if quantity == "fuel" and not fuel:
                continue
            fields[vol.Required(key, default=current[quantity])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(UNIT_CHOICES[quantity]),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=key,
                )
            )
        return self.async_show_form(
            step_id="action_units",
            data_schema=vol.Schema(fields),
            description_placeholders={"now": self._units_now(fuel)},
        )

    # --- the car's picture --------------------------------------------------

    async def async_step_action_fetch_image(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Choose where the car's picture comes from, and fetch it.

        The official picture is CarData's single side view (one request per
        car). The configurator draws the exact car as BMW's online configurator
        does, a 360-degree turntable plus its paint colour for the card: no
        quota, but an unofficial BMW service (see configurator.py). It is
        account-wide: saved when at least one car could be drawn, and a car the
        configurator can't draw keeps its official picture, which the closing
        message names. Nothing is saved when no car could be drawn.
        """

        # The image platform imports Home Assistant's image component; only
        # pull it in when this screen is actually used.
        from .image import (  # noqa: PLC0415
            async_official_fallback,
            async_refresh_configurator_render,
            async_schedule_full_render,
        )

        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(
                step_id="action_fetch_image", errors={"base": "runtime_missing"}
            )
        options = dict(self._config_entry.options)
        source = options.get(OPTION_IMAGE_SOURCE, DEFAULT_IMAGE_SOURCE)
        errors: Dict[str, str] = {}
        vehicle = ""
        error_text = ""
        failed: Dict[str, str] = {}
        if user_input is not None:
            source = user_input.get(OPTION_IMAGE_SOURCE, source)
            vins = list(runtime.coordinator.data.keys())
            if not vins:
                errors["base"] = "no_vins"
            elif source == IMAGE_SOURCE_CONFIGURATOR:
                for vin in vins:
                    # The plain spin only: the rest (about a minute) follows in
                    # the background once the setting is saved.
                    reason = await async_refresh_configurator_render(
                        self.hass, runtime, vin, full=False
                    )
                    if reason:
                        failed[vin] = reason
                if len(failed) == len(vins):
                    first = next(iter(failed))
                    errors[OPTION_IMAGE_SOURCE] = f"configurator_{failed[first]}"
                    vehicle = runtime.coordinator.names.get(first) or ""
            else:
                for vin in vins:
                    try:
                        await self.hass.services.async_call(
                            DOMAIN,
                            "fetch_vehicle_image",
                            {"entry_id": self._config_entry.entry_id, "vin": vin},
                            blocking=True,
                        )
                    except HomeAssistantError as err:
                        errors["base"] = "fetch_failed"
                        error_text = str(err)
                        break
            if not errors:
                options[OPTION_IMAGE_SOURCE] = source
                # Saved before the entities are told, so they read the new source.
                self.hass.config_entries.async_update_entry(self._config_entry, options=options)
                for vin in vins:
                    if vin in failed:
                        await async_official_fallback(self.hass, self._config_entry, runtime, vin)
                    async_dispatcher_send(self.hass, SIGNAL_VEHICLE_IMAGE, vin)
                    if source == IMAGE_SOURCE_CONFIGURATOR and vin not in failed:
                        async_schedule_full_render(self.hass, runtime, vin)
                if failed:
                    # Saved already; this only closes the dialog with the news.
                    vehicles = self._vehicles()
                    names = ", ".join(self._vehicle_label(vehicles, vin) for vin in failed)
                    return self.async_abort(
                        reason="configurator_partial", description_placeholders={"vehicles": names}
                    )
                return self.async_create_entry(title="", data=options)
        schema = vol.Schema(
            {
                vol.Required(OPTION_IMAGE_SOURCE, default=source): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[IMAGE_SOURCE_OFFICIAL, IMAGE_SOURCE_CONFIGURATOR],
                        translation_key="image_source",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="action_fetch_image",
            data_schema=schema,
            errors=errors,
            description_placeholders={"vehicle": vehicle, "error": error_text},
        )

    # --- streamed data ------------------------------------------------------

    async def async_step_action_select_clusters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Pick which data clusters to stream, then activate them in the browser.

        BMW has no API to set the stream selection — it lives behind the portal's
        interactive session — so this reuses the same in-browser activator as
        guided setup (``flow_activator.StreamActivatorFlow``): the user runs a
        one-click bookmarklet on the portal's stream-setup page and Home Assistant
        continues automatically. The activator is **additive** (it never removes a
        field you already stream), so widening a selection just works; to *stop*
        streaming a field, untick it in the portal's Data Selection.
        """

        current = self._config_entry.data.get(OPTION_STREAM_SECTIONS) or default_sections()
        labels = section_labels()
        schema = vol.Schema(
            {
                vol.Required("sections", default=list(current)): cv.multi_select(labels),
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="action_select_clusters",
                data_schema=schema,
            )

        chosen = in_catalogue_order(labels, user_input.get("sections", []))

        # Remember the choice so the picker re-opens pre-filled next time, and so
        # the activate_stream_fields service / a container reset can reuse it.
        entry = self.hass.config_entries.async_get_entry(self._config_entry.entry_id)
        if entry is not None:
            updated = dict(entry.data)
            updated[OPTION_STREAM_SECTIONS] = chosen
            self.hass.config_entries.async_update_entry(entry, data=updated)
            self._config_entry = entry

        attributes = descriptors_for_sections(chosen)
        if not attributes:
            # An empty selection has nothing to activate; just persist and finish.
            return self._finish()

        return await self._begin_activation(attributes)

    async def _activation_finished(self) -> FlowResult:
        """The selection was already saved; close the options flow.

        The portal step is what maps a second car, so look for it now instead of
        leaving it invisible until it streams.
        """

        schedule_vehicle_discovery(self.hass, self._config_entry.entry_id)
        return self._finish()
