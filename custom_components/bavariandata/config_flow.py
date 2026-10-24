"""Config flow for BMW CarData: first-time setup and signing in again.

Guided setup runs the in-browser activator first (it finds the client id and
switches the stream on), then BMW's device login; manual setup takes a typed
client id, logs in, then picks the clusters and runs the same activator
(``flow_activator.py``). The Configure menu is ``options_flow.py``; what the
screens work out is HA-free in ``flow_answers.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_SCOPE, DOMAIN, OPTION_STREAM_SECTIONS
from .descriptors import default_sections, descriptors_for_sections, section_labels
from .device_flow import (
    CardataAuthError,
    code_challenge,
    code_verifier,
    poll_for_tokens,
    request_device_code,
)
from .flow_activator import StreamActivatorFlow, schedule_vehicle_discovery
from .flow_answers import (
    auth_error_details,
    guided_done_placeholders,
    in_catalogue_order,
    token_entry_data,
)
from .onboarding import (
    OnboardingParseError,
    activation_link_placeholders,
    default_activator_attributes,
    parse_onboarding_result,
)
from .options_flow import CardataOptionsFlowHandler
from .vehicles import setup_title

LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({vol.Required("client_id"): str})

# Hassfest forbids literal URLs in translation strings, so the BMW portal links
# used in the onboarding step are injected as description placeholders instead.
USER_STEP_PLACEHOLDERS = {
    "portal_url": "https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Technical-registration_Step-1",
    "portal_uk": "https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview",
    "portal_de": "https://www.bmw.de/de-de/mybmw/vehicle-overview",
}


class CardataConfigFlow(StreamActivatorFlow, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for BMW CarData."""

    VERSION = 1
    _activation_paste_optional = True

    def __init__(self) -> None:
        self._client_id: Optional[str] = None
        self._device_data: Optional[Dict[str, Any]] = None
        self._code_verifier: Optional[str] = None
        self._token_data: Optional[Dict[str, Any]] = None
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._auth_error: Optional[str] = None
        self._requested_scope: str = DEFAULT_SCOPE
        self._entry_data: Optional[Dict[str, Any]] = None
        self._entry_title: str = ""
        # Guided onboarding: the in-browser snippet discovers the client id and
        # activates the stream before device auth, so the flow order differs from
        # the manual path (clusters -> discover -> auth vs. auth -> clusters).
        self._guided: bool = False
        self._guided_sections: list[str] = []
        self._init_activator_state()

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Entry point: choose guided (recommended) or manual setup."""

        return self.async_show_menu(
            step_id="user",
            menu_options=["guided", "manual"],
        )

    async def async_step_manual(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Manual path: user pastes a client id they created in the portal."""

        if user_input is None:
            return self.async_show_form(
                step_id="manual",
                data_schema=DATA_SCHEMA,
                description_placeholders=dict(USER_STEP_PLACEHOLDERS),
            )

        client_id = user_input["client_id"].strip()

        await self._adopt_client_id(client_id)

        try:
            await self._request_device_code()
        except CardataAuthError as err:
            return self.async_show_form(
                step_id="manual",
                data_schema=DATA_SCHEMA,
                errors={"base": "device_code_failed"},
                description_placeholders={**USER_STEP_PLACEHOLDERS, "error": str(err)},
            )

        return await self.async_step_authorize()

    async def async_step_guided(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Guided path, step 1: set up the served helper page + webhook.

        Activation happens in the user's own browser (the only place the portal
        session lives). We host a page with a one-click bookmarklet and a webhook
        the activator reports back to, then wait for it -- no client id to copy,
        no snippet to paste. The relevant default cluster set is activated;
        clusters can be tuned later under Configure.
        """

        self._guided = True
        self._guided_sections = default_sections()
        self._start_activator(default_activator_attributes())

        if self._onboarding_auto:
            return await self.async_step_guided_wait()
        return await self.async_step_guided_paste()

    async def async_step_guided_wait(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Guided path, step 2: wait for the browser activator to report back."""

        if self._onboarding_wait_task is None:
            self._onboarding_wait_task = self.hass.async_create_task(
                self._await_onboarding_result()
            )

        if not self._onboarding_wait_task.done():
            return self.async_show_progress(
                step_id="guided_wait",
                progress_action="wait_for_activation",
                description_placeholders=activation_link_placeholders(self._onboarding_page_url),
                progress_task=self._onboarding_wait_task,
            )

        result = self._onboarding_wait_task.result()
        self._onboarding_wait_task = None
        if result is None:
            # No report within the window -- offer the manual paste fallback.
            return self.async_show_progress_done(next_step_id="guided_paste")
        self._onboarding = result
        return self.async_show_progress_done(next_step_id="guided_confirm")

    async def async_step_guided_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Guided path, step 3: adopt the discovered client id, then device auth."""

        result = self._onboarding
        client_id = result.primary_client_id if result else None
        if not client_id:
            self._cleanup_onboarding()
            return self.async_abort(reason="no_client_id")

        self._cleanup_onboarding()
        await self._adopt_client_id(client_id)
        try:
            await self._request_device_code()
        except CardataAuthError as err:
            self._auth_error = auth_error_details(err, self._requested_scope)
            return await self.async_step_authorize_failed()
        return await self.async_step_authorize()

    async def async_step_guided_paste(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Guided fallback: paste the activator result if the auto-report didn't arrive."""

        schema = vol.Schema({vol.Required("result"): str})
        if user_input is None:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": "",
                },
            )

        try:
            result = parse_onboarding_result(user_input["result"])
        except OnboardingParseError as err:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                errors={"result": "invalid_result"},
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": str(err),
                },
            )
        client_id = result.primary_client_id
        if not client_id:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                errors={"result": "no_client_id"},
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": "",
                },
            )

        self._onboarding = result
        self._cleanup_onboarding()
        await self._adopt_client_id(client_id)
        try:
            await self._request_device_code()
        except CardataAuthError as err:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                errors={"base": "device_code_failed"},
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": str(err),
                },
            )
        return await self.async_step_authorize()

    async def _adopt_client_id(self, client_id: str) -> None:
        """Claim the client id; an entry that already has it is updated, not replaced.

        Removing the old entry used to be the way to "re-add" one, but removal
        deletes the entry's recorded history (see ``async_remove_entry``), so
        adding the same account again -- typically to pick up a second car --
        wiped every stored trip and charge. The existing entry is instead
        re-authorized in place at the token step, keeping its id and its store.
        """

        for entry in list(self._async_current_entries()):
            existing_client_id = entry.data.get("client_id") if hasattr(entry, "data") else None
            if entry.unique_id == client_id or existing_client_id == client_id:
                self._reauth_entry = entry
                break
        await self.async_set_unique_id(client_id)
        self._client_id = client_id

    async def _request_device_code(self) -> None:
        assert self._client_id is not None
        # BMW's device-code endpoint only accepts the coarse streaming scope;
        # granular per-descriptor scopes are rejected (see
        # docs/reference/stream-scope-investigation.md), so always request
        # DEFAULT_SCOPE. Stream descriptor selection is done in the portal.
        self._requested_scope = DEFAULT_SCOPE
        self._code_verifier = code_verifier()
        session = async_get_clientsession(self.hass)
        self._device_data = await request_device_code(
            session,
            client_id=self._client_id,
            scope=self._requested_scope,
            code_challenge=code_challenge(self._code_verifier),
        )

    async def _async_poll_for_tokens(self) -> Dict[str, Any]:
        """Background task: poll BMW until the user approves the device."""

        assert self._client_id is not None
        assert self._device_data is not None
        assert self._code_verifier is not None

        device_code = self._device_data["device_code"]
        interval = int(self._device_data.get("interval", 5))
        session = async_get_clientsession(self.hass)
        return await poll_for_tokens(
            session,
            client_id=self._client_id,
            device_code=device_code,
            code_verifier=self._code_verifier,
            interval=interval,
            timeout=int(self._device_data.get("expires_in", 600)),
        )

    async def async_step_authorize(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        assert self._device_data is not None

        # Deliberately prefer the *plain* verification URI over
        # verification_uri_complete: BMW's backend frequently records a decline
        # when the approval page is opened with the code pre-filled in the URL
        # (stale/cached codes), while manually typing the code on the plain page
        # succeeds.
        placeholders = {
            "verification_url": self._device_data.get("verification_uri")
            or self._device_data.get("verification_uri_complete"),
            "user_code": self._device_data.get("user_code", ""),
        }

        # Kick off the device-code polling in the background. Home Assistant shows a
        # progress dialog and re-enters this step when the task finishes, so the user
        # never has to time a "Submit" click — the flow advances the moment they
        # approve the device on BMW's site.
        if self._poll_task is None:
            self._poll_task = self.hass.async_create_task(self._async_poll_for_tokens())

        if not self._poll_task.done():
            return self.async_show_progress(
                step_id="authorize",
                progress_action="wait_for_authorization",
                description_placeholders=placeholders,
                progress_task=self._poll_task,
            )

        try:
            self._token_data = self._poll_task.result()
        except CardataAuthError as err:
            LOGGER.warning("BMW authorization failed: %s", err)
            self._auth_error = auth_error_details(err, self._requested_scope)
            self._poll_task = None
            return self.async_show_progress_done(next_step_id="authorize_failed")

        token_data = self._token_data or {}
        LOGGER.debug(
            "Received token: scope=%s id_token_length=%s",
            token_data.get("scope"),
            len(token_data.get("id_token") or ""),
        )
        self._poll_task = None
        return self.async_show_progress_done(next_step_id="tokens")

    async def async_step_authorize_failed(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Show why authorization failed and let the user retry with a fresh code."""

        if user_input is None:
            return self.async_show_form(
                step_id="authorize_failed",
                data_schema=vol.Schema({}),
                description_placeholders={"error": self._auth_error or ""},
            )

        # Retry: request a brand-new device/user code and restart the wait.
        try:
            await self._request_device_code()
        except CardataAuthError as err:
            return self.async_show_form(
                step_id="authorize_failed",
                data_schema=vol.Schema({}),
                errors={"base": "device_code_failed"},
                description_placeholders={"error": str(err)},
            )
        return await self.async_step_authorize()

    async def async_step_tokens(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        assert self._client_id is not None
        entry_data = token_entry_data(self._client_id, self._token_data, time.time())

        # One account, one entry. The unique id is the Client ID, but a user can
        # mint a second API client in the BMW portal for the *same* account --
        # typically believing an entry is per car -- and that passes the Client
        # ID check while colliding on everything that actually matters: BMW
        # allows one MQTT connection per GCID (the two would evict each other
        # forever), the entities of both entries share VIN-based unique ids, and
        # the 50-request daily quota is counted per account, not per client.
        # The GCID is only known here, once the tokens are in hand.
        if not self._reauth_entry and (gcid := entry_data.get("gcid")):
            for existing in self._async_current_entries():
                if existing.data.get("gcid") == gcid:
                    return self.async_abort(reason="account_already_configured")

        if self._reauth_entry:
            merged = dict(self._reauth_entry.data)
            merged.update(entry_data)
            if self._guided:
                # Re-added through the guided path: the portal stream was just
                # re-activated for these clusters, so record them.
                merged[OPTION_STREAM_SECTIONS] = self._guided_sections
                if self._onboarding and self._onboarding.mapped_vehicle_id:
                    merged["mapped_vehicle_id"] = self._onboarding.mapped_vehicle_id
            merged.pop("reauth_pending", None)
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=merged)
            runtime = getattr(self._reauth_entry, "runtime_data", None)
            if runtime:
                runtime.reauth.authorized()
                new_token = entry_data.get("id_token")
                new_gcid = entry_data.get("gcid")
                if new_token or new_gcid:
                    self.hass.async_create_task(
                        runtime.stream.async_update_credentials(
                            gcid=new_gcid,
                            id_token=new_token,
                        )
                    )
            notification_id = f"{DOMAIN}_reauth_{self._reauth_entry.entry_id}"
            persistent_notification.async_dismiss(self.hass, notification_id)
            schedule_vehicle_discovery(self.hass, self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        # Replaced by the car names once BMW's basic data names them.
        self._entry_title = setup_title(self._client_id)
        self._entry_data = entry_data

        # Guided path: clusters were already chosen and the snippet already
        # activated the stream before auth, so skip the picker and finish. Persist
        # the cluster choice (and the portal mapped-vehicle id, for later
        # re-activation) so the install matches what was just streamed.
        if self._guided:
            entry_data[OPTION_STREAM_SECTIONS] = self._guided_sections
            if self._onboarding and self._onboarding.mapped_vehicle_id:
                entry_data["mapped_vehicle_id"] = self._onboarding.mapped_vehicle_id
            return await self.async_step_guided_done()

        # Manual path: authorization succeeded but BMW streams nothing until
        # descriptors are activated in the portal. Route into the cluster picker
        # and the activator, so the user leaves setup with a live stream rather
        # than an empty one.
        return await self.async_step_select_clusters()

    async def async_step_guided_done(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Guided path, final: confirm what was activated, then create the entry."""

        assert self._entry_data is not None
        if user_input is None:
            return self.async_show_form(
                step_id="guided_done",
                data_schema=vol.Schema({}),
                description_placeholders=guided_done_placeholders(self._onboarding),
            )
        return self.async_create_entry(title=self._entry_title, data=self._entry_data)

    async def async_step_select_clusters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Pick which data clusters to stream, right after authorization.

        Mirrors the options-flow picker (``CardataOptionsFlowHandler.
        async_step_action_select_clusters``) and runs the same in-browser
        activator, inside initial setup, so a first-time user leaves with the
        stream switched on rather than hunting through Configure afterwards.
        """

        labels = section_labels()
        schema = vol.Schema(
            {
                vol.Required("sections", default=default_sections()): cv.multi_select(labels),
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="select_clusters",
                data_schema=schema,
            )

        chosen = in_catalogue_order(labels, user_input.get("sections", []))
        assert self._entry_data is not None
        self._entry_data[OPTION_STREAM_SECTIONS] = chosen
        attributes = descriptors_for_sections(chosen)
        if not attributes:
            # Nothing selected, nothing to activate.
            return await self._activation_finished()
        return await self._begin_activation(attributes)

    async def _activation_finished(self) -> FlowResult:
        """Create the entry, keeping the portal vehicle id for re-activation."""

        assert self._entry_data is not None
        if self._onboarding and self._onboarding.mapped_vehicle_id:
            self._entry_data["mapped_vehicle_id"] = self._onboarding.mapped_vehicle_id
        return self.async_create_entry(title=self._entry_title, data=self._entry_data)

    async def async_step_reauth(self, entry_data: Dict[str, Any]) -> FlowResult:
        entry_id = entry_data.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        self._client_id = entry_data.get("client_id")
        if not self._client_id:
            LOGGER.error("Reauth requested but client_id missing for entry %s", entry_id)
            return self.async_abort(reason="reauth_missing_client_id")
        try:
            await self._request_device_code()
        except CardataAuthError as err:
            LOGGER.error(
                "Unable to request BMW device authorization code for entry %s: %s",
                entry_id,
                err,
            )
            if self._reauth_entry:
                runtime = getattr(self._reauth_entry, "runtime_data", None)
                if runtime:
                    runtime.reauth.abandoned()
            return self.async_abort(
                reason="reauth_device_code_failed",
                description_placeholders={"error": str(err)},
            )
        return await self.async_step_authorize()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CardataOptionsFlowHandler(config_entry)
