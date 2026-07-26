"""Config flow for BMW CarData integration."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import secrets
import string
import time
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
import voluptuous as vol

import logging

from homeassistant import config_entries
from homeassistant.components import persistent_notification, webhook
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from . import async_manual_refresh_tokens
from .container import CardataContainerError
from .const import (
    DEBUG_LOG,
    DEFAULT_HISTORY_RETAIN_MONTHS,
    DEFAULT_SCOPE,
    DOMAIN,
    OPTION_CHARGING_LOSS_PERCENT,
    OPTION_DEBUG_LOG,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_HISTORY_RETAIN_MONTHS,
    OPTION_PRICE_CURRENCY,
    OPTION_PRICE_ENTITY,
    OPTION_PRICE_FIXED,
    OPTION_PRICE_MODE,
    OPTION_STREAM_SECTIONS,
    OPTION_STATISTICS_IMPORT,
    DEFAULT_STATISTICS_IMPORT,
    OPTION_TRIP_GEOCODE,
    OPTION_TRIP_WORK_ZONE,
    VEHICLE_METADATA,
)
from .debug import set_debug_enabled
from .history.pricing import DEFAULT_CURRENCY, MODE_ENTITY, MODE_FIXED, MODE_NONE, PricingConfig
from .descriptors import (
    build_portal_snippet,
    default_sections,
    descriptors_for_sections,
    section_labels,
)
from .onboarding import (
    OnboardingParseError,
    OnboardingResult,
    build_bookmarklet,
    build_console_snippet,
    build_helper_page,
    default_activator_attributes,
    parse_onboarding_result,
)
from .device_flow import CardataAuthError, poll_for_tokens, request_device_code

DATA_SCHEMA = vol.Schema({vol.Required("client_id"): str})

# Guided onboarding: the served helper page (hosts the draggable bookmarklet) and
# the per-flow webhook the in-browser activator reports its result to. The page is
# served from Home Assistant's own origin because a config-flow dialog sanitizes a
# ``javascript:`` link; the webhook lets the activator hand the result back with no
# copy-paste. Both are set up on demand when a guided flow starts (there is no
# entry yet at first-time setup, so this can't wait for async_setup_entry).
ONBOARDING_VIEW_URL = f"/{DOMAIN}/onboarding"
_ONBOARDING_STORE = "_onboarding_flows"
_ONBOARDING_VIEW_REGISTERED = "_onboarding_view_registered"
# How long the guided flow waits for the activator to report before offering the
# manual paste fallback. Only reached on an https instance where the webhook is
# viable but doesn't arrive (rare); http instances skip the wait entirely.
ONBOARDING_WAIT_TIMEOUT = 300


class _OnboardingHelperView(HomeAssistantView):
    """Serve the (prebuilt) onboarding helper page for a pending guided flow."""

    url = ONBOARDING_VIEW_URL
    name = f"{DOMAIN}:onboarding"
    requires_auth = False  # opened directly in the user's browser

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        token = request.query.get("token", "")
        flows = hass.data.get(DOMAIN, {}).get(_ONBOARDING_STORE, {})
        pending = flows.get(token)
        if not pending:
            return web.Response(
                status=404,
                text="This setup link has expired — restart setup in Home Assistant.",
            )
        return web.Response(text=pending["html"], content_type="text/html")


async def _handle_onboarding_webhook(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    """Receive the activator's result and wake the waiting guided flow."""

    flows = hass.data.get(DOMAIN, {}).get(_ONBOARDING_STORE, {})
    pending = flows.get(webhook_id)
    if not pending:
        return web.Response(status=404, text="unknown")
    try:
        result = parse_onboarding_result(await request.text())
    except OnboardingParseError as err:
        return web.Response(status=400, text=str(err))
    pending["result"] = result
    pending["event"].set()
    return web.Response(status=200, text="Received — you can return to Home Assistant.")

# Hassfest forbids literal URLs in translation strings, so the BMW portal links
# used in the onboarding step are injected as description placeholders instead.
USER_STEP_PLACEHOLDERS = {
    "portal_url": "https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Technical-registration_Step-1",
    "portal_uk": "https://www.bmw.co.uk/en-gb/mybmw/vehicle-overview",
    "portal_de": "https://www.bmw.de/de-de/mybmw/vehicle-overview",
}


def _build_code_verifier() -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(86))


def _generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class _StreamActivatorFlow:
    """Shared plumbing for the in-browser stream-field activator.

    BMW's stream selection has no API and lives behind an interactive portal
    session, so activation runs in the user's *own* browser: we host a helper
    page carrying a one-click bookmarklet (plus a console fallback) and a
    one-time webhook the activator reports its result back to. Both first-time
    guided setup (:class:`CardataConfigFlow`) and the later "Choose streamed
    data" reconfiguration (:class:`CardataOptionsFlowHandler`) drive the very
    same activator, so neither path needs any copy-paste. Relies only on
    ``self.hass``.
    """

    def _init_activator_state(self) -> None:
        """Initialise the per-flow activator state (call from ``__init__``)."""

        self._onboarding: Optional[OnboardingResult] = None
        self._onboarding_webhook_id: Optional[str] = None
        self._onboarding_event: Optional[asyncio.Event] = None
        self._onboarding_page_url: str = ""
        self._onboarding_wait_task: Optional[asyncio.Task] = None
        self._onboarding_auto: bool = False

    @callback
    def _ensure_onboarding_view(self) -> None:
        """Register the helper-page view once per Home Assistant instance.

        Guarded by a flag, and the actual registration is wrapped: the flag lives
        in ``hass.data[DOMAIN]`` which is dropped when the last entry unloads, so a
        later flow could try to register the (still-live) view again -- suppress
        that rather than error.
        """

        domain_data = self.hass.data.setdefault(DOMAIN, {})
        if domain_data.get(_ONBOARDING_VIEW_REGISTERED):
            return
        with contextlib.suppress(Exception):
            self.hass.http.register_view(_OnboardingHelperView())
        domain_data[_ONBOARDING_VIEW_REGISTERED] = True

    def _start_activator(self, attributes: list[str]) -> None:
        """Host the helper page + webhook for one activator run.

        The activator runs on ``https://www.bmw.at`` and can only POST its result
        to an **https** Home Assistant; an http instance blocks that as mixed
        content. So the webhook (and the auto-continue wait) is only wired when HA
        is https -- otherwise the activator is clipboard-only and the caller goes
        straight to the paste screen. Sets ``_onboarding_auto`` and
        ``_onboarding_page_url`` for the caller to route on.
        """

        self._ensure_onboarding_view()

        token = webhook.async_generate_id()
        try:
            webhook_url = webhook.async_generate_url(self.hass, token)
        except NoURLAvailableError:
            webhook_url = ""
        auto = webhook_url.startswith("https://")
        report_url = webhook_url if auto else ""
        self._onboarding_auto = auto

        bookmarklet = build_bookmarklet(attributes, report_url=report_url)
        console = build_console_snippet(attributes, report_url=report_url)
        html = build_helper_page(
            bookmarklet=bookmarklet, console_js=console, attribute_count=len(attributes)
        )

        event = asyncio.Event()
        store = self.hass.data.setdefault(DOMAIN, {}).setdefault(_ONBOARDING_STORE, {})
        store[token] = {"event": event, "result": None, "html": html}
        self._onboarding_webhook_id = token
        self._onboarding_event = event
        if auto:
            webhook.async_register(
                self.hass, DOMAIN, "BavarianData onboarding", token, _handle_onboarding_webhook
            )

        try:
            self._onboarding_page_url = (
                get_url(self.hass) + ONBOARDING_VIEW_URL + f"?token={token}"
            )
        except NoURLAvailableError:
            self._onboarding_page_url = f"{ONBOARDING_VIEW_URL}?token={token}"

    def _cleanup_onboarding(self) -> None:
        """Unregister the webhook and drop the pending record for this flow."""

        token = self._onboarding_webhook_id
        if not token:
            return
        if self._onboarding_auto:
            with contextlib.suppress(ValueError, KeyError):
                webhook.async_unregister(self.hass, token)
        self.hass.data.get(DOMAIN, {}).get(_ONBOARDING_STORE, {}).pop(token, None)
        self._onboarding_webhook_id = None
        self._onboarding_event = None

    async def _await_onboarding_result(self) -> Optional[OnboardingResult]:
        """Wait (bounded) for the activator to POST its result to the webhook."""

        assert self._onboarding_event is not None
        try:
            await asyncio.wait_for(
                self._onboarding_event.wait(), timeout=ONBOARDING_WAIT_TIMEOUT
            )
        except asyncio.TimeoutError:
            return None
        pending = self.hass.data.get(DOMAIN, {}).get(_ONBOARDING_STORE, {}).get(
            self._onboarding_webhook_id
        )
        return pending.get("result") if pending else None

    async def async_remove(self) -> None:
        """Flow teardown (abort/cancel/finish): drop the webhook + wait task."""

        self._cleanup_onboarding()
        if self._onboarding_wait_task and not self._onboarding_wait_task.done():
            self._onboarding_wait_task.cancel()


class CardataConfigFlow(_StreamActivatorFlow, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for BMW CarData."""

    VERSION = 1

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
        self._cluster_snippet: str = ""
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

    async def async_step_manual(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manual path: user pastes a client id they created in the portal."""

        if user_input is None:
            return self.async_show_form(
                step_id="manual",
                data_schema=DATA_SCHEMA,
                description_placeholders=dict(USER_STEP_PLACEHOLDERS),
            )

        client_id = user_input["client_id"].strip()

        for entry in list(self._async_current_entries()):
            existing_client_id = entry.data.get("client_id") if hasattr(entry, "data") else None
            if entry.unique_id == client_id or existing_client_id == client_id:
                await self.hass.config_entries.async_remove(entry.entry_id)

        await self.async_set_unique_id(client_id)

        self._client_id = client_id

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

    async def async_step_guided(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
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
                description_placeholders={"url": self._onboarding_page_url},
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
            self._auth_error = self._format_auth_error(err)
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
                description_placeholders={"url": self._onboarding_page_url, "error": ""},
            )

        try:
            result = parse_onboarding_result(user_input["result"])
        except OnboardingParseError as err:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                errors={"result": "invalid_result"},
                description_placeholders={"url": self._onboarding_page_url, "error": str(err)},
            )
        client_id = result.primary_client_id
        if not client_id:
            return self.async_show_form(
                step_id="guided_paste",
                data_schema=schema,
                errors={"result": "no_client_id"},
                description_placeholders={"url": self._onboarding_page_url, "error": ""},
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
                description_placeholders={"url": self._onboarding_page_url, "error": str(err)},
            )
        return await self.async_step_authorize()

    async def _adopt_client_id(self, client_id: str) -> None:
        """Remove any duplicate entry for this client id and claim it as unique id."""

        for entry in list(self._async_current_entries()):
            existing_client_id = entry.data.get("client_id") if hasattr(entry, "data") else None
            if entry.unique_id == client_id or existing_client_id == client_id:
                await self.hass.config_entries.async_remove(entry.entry_id)
        await self.async_set_unique_id(client_id)
        self._client_id = client_id

    async def _request_device_code(self) -> None:
        assert self._client_id is not None
        # BMW's device-code endpoint only accepts the coarse streaming scope;
        # granular per-descriptor scopes are rejected (see
        # docs/reference/stream-scope-investigation.md), so always request
        # DEFAULT_SCOPE. Stream descriptor selection is done in the portal.
        self._requested_scope = DEFAULT_SCOPE
        self._code_verifier = _build_code_verifier()
        async with aiohttp.ClientSession() as session:
            self._device_data = await request_device_code(
                session,
                client_id=self._client_id,
                scope=self._requested_scope,
                code_challenge=_generate_code_challenge(self._code_verifier),
            )

    async def _async_poll_for_tokens(self) -> Dict[str, Any]:
        """Background task: poll BMW until the user approves the device."""

        assert self._client_id is not None
        assert self._device_data is not None
        assert self._code_verifier is not None

        device_code = self._device_data["device_code"]
        interval = int(self._device_data.get("interval", 5))
        async with aiohttp.ClientSession() as session:
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
            self._auth_error = self._format_auth_error(err)
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

    def _format_auth_error(self, err: CardataAuthError) -> str:
        """Render a multi-line details block for the authorize-failed screen.

        Surfaces the raw OAuth pieces BMW returned so the user (or a bug report)
        can see exactly what was rejected instead of a single opaque line.
        """

        lines = [str(err)]
        if err.status is not None:
            lines.append(f"- HTTP status: {err.status}")
        if err.error_code:
            lines.append(f"- Error code: {err.error_code}")
        if err.error_description:
            lines.append(f"- Description: {err.error_description}")
        if err.correlation_id:
            lines.append(f"- BMW reference: {err.correlation_id}")
        # The scope is the usual culprit behind an `access_denied`, so always show
        # what we asked BMW to grant.
        lines.append(f"- Requested scope: {self._requested_scope}")
        return "\n".join(lines)

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
        token_data = self._token_data

        entry_data = {
            "client_id": self._client_id,
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "id_token": token_data.get("id_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "gcid": token_data.get("gcid"),
            "token_type": token_data.get("token_type"),
            "received_at": time.time(),
        }

        if self._reauth_entry:
            merged = dict(self._reauth_entry.data)
            merged.update(entry_data)
            merged.pop("reauth_pending", None)
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=merged)
            runtime = getattr(self._reauth_entry, "runtime_data", None)
            if runtime:
                runtime.reauth_in_progress = False
                runtime.reauth_flow_id = None
                runtime.last_reauth_attempt = 0.0
                runtime.last_refresh_attempt = 0.0
                runtime.reauth_pending = False
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
            return self.async_abort(reason="reauth_successful")

        self._entry_title = (
            "BavarianData: Connect Home Assistant to BMW CarData "
            f"({self._client_id[:8]})"
        )
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
        # descriptors are ticked in the portal's Data Selection. Route into the
        # cluster picker so the user leaves setup with a ready-to-paste snippet
        # instead of an empty stream.
        return await self.async_step_select_clusters()

    async def async_step_guided_done(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Guided path, final: confirm what was activated, then create the entry."""

        assert self._entry_data is not None
        if user_input is None:
            result = self._onboarding
            count = result.activated_count if result else None
            placeholders = {
                "field_count": str(count) if count is not None else "the selected",
                "vehicle": (result.mapped_vehicle_id[:8] + "…")
                if result and result.mapped_vehicle_id
                else "your vehicle",
            }
            return self.async_show_form(
                step_id="guided_done",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        return self.async_create_entry(title=self._entry_title, data=self._entry_data)

    async def async_step_select_clusters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Pick which data clusters to stream, right after authorization.

        Mirrors the options-flow picker (:meth:`CardataOptionsFlowHandler.
        async_step_action_select_clusters`) but runs inside initial setup so a
        first-time user is handed a portal snippet without hunting through
        Configure afterwards. BMW has no API to set the selection — it is done in
        the portal — so this builds a browser-console snippet instead.
        """

        labels = section_labels()
        schema = vol.Schema(
            {
                vol.Required(
                    "sections", default=default_sections()
                ): cv.multi_select(labels),
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="select_clusters",
                data_schema=schema,
            )

        # Preserve the catalogue's cluster order regardless of checkbox order.
        chosen = [slug for slug in labels if slug in set(user_input.get("sections", []))]
        assert self._entry_data is not None
        self._entry_data[OPTION_STREAM_SECTIONS] = chosen
        self._cluster_snippet = build_portal_snippet(chosen)
        return await self.async_step_cluster_snippet()

    async def async_step_cluster_snippet(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Show the generated Data Selection snippet, then create the entry."""

        if user_input is None:
            return self.async_show_form(
                step_id="cluster_snippet",
                data_schema=vol.Schema({}),
                description_placeholders={"snippet": self._cluster_snippet},
            )
        assert self._entry_data is not None
        return self.async_create_entry(
            title=self._entry_title, data=self._entry_data
        )

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
                    runtime.reauth_in_progress = False
                    runtime.reauth_flow_id = None
            return self.async_abort(
                reason="reauth_device_code_failed",
                description_placeholders={"error": str(err)},
            )
        return await self.async_step_authorize()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return CardataOptionsFlowHandler(config_entry)

LOGGER = logging.getLogger(__name__)


class CardataOptionsFlowHandler(_StreamActivatorFlow, config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._reauth_client_id: Optional[str] = None
        self._init_activator_state()

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        # Labels/descriptions come from translations (options.step.init.menu_options),
        # so the menu is localizable and stays in sync with each action step.
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "action_select_clusters",
                "action_refresh_tokens",
                "action_reauth",
                "action_reset_container",
                "action_fetch_mappings",
                "action_fetch_basic",
                "action_fetch_telematic",
                "action_fetch_charging_history",
                "action_fetch_tyre",
                "action_fetch_location_charging",
                "action_fetch_image",
                "action_charging_costs",
                "action_trips",
                "action_debug_logging",
            ],
        )

    def _confirm_schema(self) -> vol.Schema:
        # An empty schema renders as a plain confirmation dialog: pressing "Submit"
        # confirms the action. No checkbox to tick first.
        return vol.Schema({})

    def _show_confirm(
        self,
        *,
        step_id: str,
        errors: Optional[Dict[str, str]] = None,
        placeholders: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._confirm_schema(),
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
        current_client_id = (
            self._reauth_client_id
            or self._config_entry.data.get("client_id")
            or ""
        )
        schema = vol.Schema(
            {
                vol.Required("client_id", default=current_client_id): str,
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="action_reauth", data_schema=schema)
        client_id = user_input.get("client_id", "")
        if isinstance(client_id, str):
            client_id = client_id.strip()
        else:
            client_id = ""
        if not client_id:
            return self.async_show_form(
                step_id="action_reauth",
                data_schema=schema,
                errors={"client_id": "invalid_client_id"},
            )
        self._reauth_client_id = client_id
        return await self._handle_reauth()

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
        await self.hass.services.async_call(
            DOMAIN,
            "fetch_vehicle_mappings",
            {"entry_id": self._config_entry.entry_id},
            blocking=True,
        )
        return self._finish()

    def _collect_vins(self) -> list[str]:
        runtime = self._get_runtime()
        vins = set()
        if runtime:
            vins.update(runtime.coordinator.data.keys())
        metadata = self._config_entry.data.get(VEHICLE_METADATA)
        if isinstance(metadata, dict):
            vins.update(metadata.keys())
        if entry_vin := self._config_entry.data.get("vin"):
            vins.add(entry_vin)
        return [vin for vin in vins if isinstance(vin, str)]

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
        for vin in sorted(vins):
            await self.hass.services.async_call(
                DOMAIN,
                "fetch_basic_data",
                {"entry_id": self._config_entry.entry_id, "vin": vin},
                blocking=True,
            )
        return self._finish()

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
        await self.hass.services.async_call(
            DOMAIN,
            "fetch_telematic_data",
            {"entry_id": self._config_entry.entry_id},
            blocking=True,
        )
        return self._finish()

    async def _run_simple_service(
        self, *, step_id: str, service: str, user_input: Optional[Dict[str, Any]]
    ) -> FlowResult:
        """Confirm-then-call helper for read-only VIN-scoped API services."""

        runtime = self._get_runtime()
        if runtime is None:
            return self._show_confirm(step_id=step_id, errors={"base": "runtime_missing"})
        if user_input is None:
            return self._show_confirm(step_id=step_id)
        await self.hass.services.async_call(
            DOMAIN,
            service,
            {"entry_id": self._config_entry.entry_id},
            blocking=True,
        )
        return self._finish()

    async def async_step_action_charging_costs(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure how charging energy is turned into money.

        Deliberately not a tariff editor: users already have a price entity from
        Tibber/Nordpool/aWATTar, or a flat rate they know. Until a mode other
        than "none" is picked, no cost entity is created at all.
        """

        options = dict(self._config_entry.options)
        schema = vol.Schema(
            {
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
                vol.Optional(
                    OPTION_GRID_ENERGY_ENTITY,
                    description={
                        "suggested_value": options.get(OPTION_GRID_ENERGY_ENTITY)
                    },
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    OPTION_CHARGING_LOSS_PERCENT,
                    default=options.get(OPTION_CHARGING_LOSS_PERCENT, 0),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=30, step=0.5, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPTION_HISTORY_RETAIN_MONTHS,
                    default=options.get(
                        OPTION_HISTORY_RETAIN_MONTHS, DEFAULT_HISTORY_RETAIN_MONTHS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPTION_STATISTICS_IMPORT,
                    default=options.get(
                        OPTION_STATISTICS_IMPORT, DEFAULT_STATISTICS_IMPORT
                    ),
                ): selector.BooleanSelector(),
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="action_charging_costs", data_schema=schema
            )

        mode = user_input.get(OPTION_PRICE_MODE, MODE_NONE)
        if mode == MODE_FIXED and user_input.get(OPTION_PRICE_FIXED) is None:
            return self.async_show_form(
                step_id="action_charging_costs",
                data_schema=schema,
                errors={"base": "price_required"},
            )
        if mode == MODE_ENTITY and not user_input.get(OPTION_PRICE_ENTITY):
            return self.async_show_form(
                step_id="action_charging_costs",
                data_schema=schema,
                errors={"base": "price_entity_required"},
            )

        options.update(user_input)
        # Apply immediately rather than reloading the entry: BMW allows only one
        # concurrent stream per account, so a reload risks racing the reconnect.
        runtime = getattr(self._config_entry, "runtime_data", None)
        if runtime is not None:
            runtime.coordinator.pricing = PricingConfig.from_options(options)
            if runtime.history is not None:
                months = int(user_input.get(OPTION_HISTORY_RETAIN_MONTHS, 0) or 0)
                runtime.history.retain_months = months if months > 0 else None
            if runtime.statistics is not None:
                enabled = bool(user_input.get(OPTION_STATISTICS_IMPORT, True))
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

    async def async_step_action_trips(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure trip classification and address resolution.

        The work zone drives commute classification (home <-> work). Address
        resolution is off by default: turning it on sends the coordinates of
        trip endpoints outside a known zone to OpenStreetMap's Nominatim -- the
        resulting address string is stored, never the coordinates themselves.
        """

        options = dict(self._config_entry.options)
        schema = vol.Schema(
            {
                vol.Optional(
                    OPTION_TRIP_WORK_ZONE,
                    description={
                        "suggested_value": options.get(OPTION_TRIP_WORK_ZONE)
                    },
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Required(
                    OPTION_TRIP_GEOCODE,
                    default=options.get(OPTION_TRIP_GEOCODE, False),
                ): selector.BooleanSelector(),
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="action_trips", data_schema=schema)

        options.update(user_input)
        # Apply immediately rather than reloading (one concurrent stream per
        # account means a reload risks racing the reconnect).
        runtime = getattr(self._config_entry, "runtime_data", None)
        if runtime is not None:
            coordinator = runtime.coordinator
            coordinator.work_zone_entity = (
                user_input.get(OPTION_TRIP_WORK_ZONE) or None
            )
            if coordinator.geocoder is not None:
                coordinator.geocoder.enabled = bool(
                    user_input.get(OPTION_TRIP_GEOCODE)
                )
        return self.async_create_entry(title="", data=options)

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
        set_debug_enabled(enabled)
        options = dict(self._config_entry.options)
        options[OPTION_DEBUG_LOG] = enabled
        return self.async_create_entry(title="", data=options)

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

    async def async_step_action_fetch_image(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._run_simple_service(
            step_id="action_fetch_image",
            service="fetch_vehicle_image",
            user_input=user_input,
        )

    async def async_step_action_select_clusters(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Pick which data clusters to stream, then activate them in the browser.

        BMW has no API to set the stream selection — it lives behind the portal's
        interactive session — so this reuses the same in-browser activator as
        guided setup (:class:`_StreamActivatorFlow`): the user runs a one-click
        bookmarklet on the portal's stream-setup page and Home Assistant continues
        automatically. The activator is **additive** (it never removes a field you
        already stream), so widening a selection just works; to *stop* streaming a
        field, untick it in the portal's Data Selection.
        """

        current = (
            self._config_entry.data.get(OPTION_STREAM_SECTIONS)
            or default_sections()
        )
        labels = section_labels()
        schema = vol.Schema(
            {
                vol.Required("sections", default=list(current)): cv.multi_select(
                    labels
                ),
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="action_select_clusters",
                data_schema=schema,
            )

        # Preserve the catalogue's cluster order regardless of checkbox order.
        chosen = [slug for slug in labels if slug in set(user_input.get("sections", []))]

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

        self._start_activator(attributes)
        if self._onboarding_auto:
            return await self.async_step_activate_stream_wait()
        return await self.async_step_activate_stream_paste()

    async def async_step_activate_stream_wait(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Wait for the browser activator to report the stream fields it turned on."""

        if self._onboarding_wait_task is None:
            self._onboarding_wait_task = self.hass.async_create_task(
                self._await_onboarding_result()
            )

        if not self._onboarding_wait_task.done():
            return self.async_show_progress(
                step_id="activate_stream_wait",
                progress_action="wait_for_stream_activation",
                description_placeholders={"url": self._onboarding_page_url},
                progress_task=self._onboarding_wait_task,
            )

        result = self._onboarding_wait_task.result()
        self._onboarding_wait_task = None
        if result is None:
            # No report within the window -- offer the manual paste fallback.
            return self.async_show_progress_done(next_step_id="activate_stream_paste")
        self._onboarding = result
        return self.async_show_progress_done(next_step_id="activate_stream_done")

    async def async_step_activate_stream_paste(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Fallback: paste the activator result if the auto-report didn't arrive."""

        schema = vol.Schema({vol.Required("result"): str})
        if user_input is None:
            return self.async_show_form(
                step_id="activate_stream_paste",
                data_schema=schema,
                description_placeholders={"url": self._onboarding_page_url, "error": ""},
            )

        try:
            result = parse_onboarding_result(user_input["result"])
        except OnboardingParseError as err:
            return self.async_show_form(
                step_id="activate_stream_paste",
                data_schema=schema,
                errors={"result": "invalid_result"},
                description_placeholders={"url": self._onboarding_page_url, "error": str(err)},
            )
        self._onboarding = result
        return await self.async_step_activate_stream_done()

    async def async_step_activate_stream_done(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm what the activator turned on, then persist and finish."""

        result = self._onboarding
        if user_input is None:
            total = result.activated_count if result else None
            added = result.added_count if result else None
            ok = bool(result and result.activation_ok)
            # The done screen text works for both outcomes; on a problem, surface
            # the last reported error as an extra line (empty on success).
            error = "" if ok else (result.errors[-1] if result and result.errors else "")
            placeholders = {
                "field_count": str(total) if total is not None else "the selected",
                "added_count": str(added) if added is not None else "0",
                "error": f"\n\n⚠️ {error}" if error else "",
            }
            self._cleanup_onboarding()
            return self.async_show_form(
                step_id="activate_stream_done",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        return self._finish()

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

        entry = self.hass.config_entries.async_get_entry(self._config_entry.entry_id)
        if entry is None:
            return self._show_confirm(
                step_id="action_reset_container",
                errors={"base": "runtime_missing"},
            )

        access_token = entry.data.get("access_token")
        if not access_token:
            try:
                await async_manual_refresh_tokens(self.hass, entry)
            except CardataAuthError as err:
                return self._show_confirm(
                    step_id="action_reset_container",
                    errors={"base": "refresh_failed"},
                    placeholders={"error": str(err)},
                )
            entry = self.hass.config_entries.async_get_entry(entry.entry_id)
            if entry is None:
                return self._show_confirm(
                    step_id="action_reset_container",
                    errors={"base": "runtime_missing"},
                )
            access_token = entry.data.get("access_token")
            if not access_token:
                return self._show_confirm(
                    step_id="action_reset_container",
                    errors={"base": "missing_token"},
                )

        try:
            new_id = await runtime.container_manager.async_reset_hv_container(access_token)
        except CardataContainerError as err:
            return self._show_confirm(
                step_id="action_reset_container",
                errors={"base": "reset_failed"},
                placeholders={"error": str(err)},
            )

        updated = dict(entry.data)
        if new_id:
            updated["hv_container_id"] = new_id
            updated["hv_descriptor_signature"] = runtime.container_manager.descriptor_signature
        else:
            updated.pop("hv_container_id", None)
            updated.pop("hv_descriptor_signature", None)
        self.hass.config_entries.async_update_entry(entry, data=updated)

        return self._finish()

    async def _handle_reauth(self) -> FlowResult:
        entry = self._config_entry
        if entry is None:
            return self.async_abort(reason="unknown")
        client_id = (self._reauth_client_id or entry.data.get("client_id") or "").strip()
        self._reauth_client_id = None
        if not client_id:
            return self.async_abort(reason="reauth_missing_client_id")

        updated = dict(entry.data)
        updated["client_id"] = client_id
        runtime = self._get_runtime()
        if runtime:
            runtime.reauth_in_progress = True
            runtime.reauth_pending = True
        self.hass.config_entries.async_update_entry(entry, data=updated)

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



async def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
    return CardataOptionsFlowHandler(config_entry)
