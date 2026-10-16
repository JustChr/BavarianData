"""Config flow for BMW CarData integration."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import secrets
import string
import time
from datetime import timedelta
from typing import Any, Dict, Mapping, Optional

import aiohttp
from aiohttp import web
import voluptuous as vol

import logging

from homeassistant import config_entries
from homeassistant.components import persistent_notification, webhook
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, FlowResultType, section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from . import async_manual_refresh_tokens
from .container import CardataContainerError
from .service_calendar import KM_PER_MILE
from .evcc import BridgeConfig, bridge_payloads, evcc_yaml
from .const import (
    DEBUG_LOG,
    DEFAULT_HISTORY_RETAIN_MONTHS,
    DEFAULT_SCOPE,
    DOMAIN,
    SIGNAL_VEHICLE_IMAGE,
    OPTION_BATTERY_POWER_ENTITY,
    OPTION_BATTERY_POWER_INVERT,
    OPTION_CHARGING_LOSS_PERCENT,
    DEFAULT_BRIDGE_PREFIX,
    DEFAULT_BRIDGE_RETAIN,
    OPTION_BRIDGE_ENABLED,
    OPTION_BRIDGE_PREFIX,
    OPTION_BRIDGE_RETAIN,
    OPTION_DEBUG_LOG,
    DEFAULT_IMAGE_SOURCE,
    IMAGE_SOURCE_CONFIGURATOR,
    IMAGE_SOURCE_OFFICIAL,
    OPTION_IMAGE_SOURCE,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_GRID_POWER_ENTITY,
    OPTION_PRICE_SOLAR,
    OPTION_PV_POWER_ENTITY,
    OPTION_HISTORY_RETAIN_MONTHS,
    OPTION_PRICE_CURRENCY,
    OPTION_PRICE_ENTITY,
    OPTION_PRICE_FIXED,
    OPTION_PRICE_MODE,
    OPTION_STREAM_SECTIONS,
    OPTION_STATISTICS_IMPORT,
    DEFAULT_STATISTICS_IMPORT,
    OPTION_REFRESH_ON_START,
    DEFAULT_REFRESH_ON_START,
    OPTION_TRIP_COMMUTE_GAP,
    DEFAULT_TRIP_COMMUTE_GAP_MIN,
    OPTION_TRIP_DEBUG,
    OPTION_TRIP_DEFAULT_CLASS,
    DEFAULT_TRIP_DEFAULT_CLASS,
    OPTION_TRIP_GEOCODE,
    OPTION_TRIP_TRACK,
    OPTION_TRIP_WORK_ZONE,
    VEHICLE_METADATA,
    OPTION_TRIP_DETECTION,
    OPTION_TRIP_PHONE_IN_CAR,
    OPTION_TRIP_PHONE_TRACKER,
    TRIP_DETECTION_CHOICES,
    TRIP_DETECTION_LIVE,
    TRIP_DETECTION_REPORTS,
)
from .debug import set_debug_enabled
from .distance import car_distance_unit
from .history.classify import DEFAULT_CLASS_CHOICES
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
from .mileage import (
    KIND_LEASE,
    KIND_NONE,
    KIND_YEARLY,
    KINDS as MILEAGE_KINDS,
    KEY_END as MILEAGE_END,
    KEY_KIND as MILEAGE_KIND,
    KEY_PER_YEAR as MILEAGE_PER_YEAR,
    KEY_START as MILEAGE_START,
    KEY_START_KM as MILEAGE_START_KM,
    allowance_settings,
    anniversary as _anniversary,
    parse_date as _parse_date,
    yearly_period,
)
from .history.pricing import (
    DEFAULT_CURRENCY,
    MODE_ENTITY,
    MODE_FIXED,
    MODE_NONE,
    PricingConfig,
    vehicle_pricing,
)
from .descriptors import (
    default_sections,
    descriptors_for_sections,
    section_labels,
)
from .onboarding import (
    OnboardingParseError,
    OnboardingResult,
    activation_link_placeholders,
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
    """Serve the (prebuilt) onboarding helper page for a pending guided flow.

    This view is deliberately unauthenticated, so it is worth being explicit
    about why that is safe here:

    * The page has to open in the browser tab the user then carries to BMW's
      portal, straight from a link in the config-flow dialog. That navigation
      does not carry Home Assistant's auth headers, so an authenticated view
      would simply 401.
    * Access is gated on a one-time capability token (``webhook.async_generate_id()``,
      the same generator Home Assistant uses for webhook ids). Without the exact
      token the view returns 404 -- it enumerates nothing.
    * It is GET-only and read-only. It serves a page built entirely from
      constants plus the descriptor list the user just picked; no VIN, GCID,
      client id or token is ever rendered into it, and nothing about the
      instance can be mutated through it.
    * The record is dropped in ``_cleanup_onboarding`` when the flow finishes or
      aborts, so the URL stops resolving as soon as setup is over.

    The paired result webhook is a regular Home Assistant webhook and unauthenticated
    for the same structural reason: it is posted to by a script running on BMW's
    origin, which has no Home Assistant credentials.
    """

    url = ONBOARDING_VIEW_URL
    name = f"{DOMAIN}:onboarding"
    requires_auth = False  # see the class docstring for why this is safe

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


def _schedule_vehicle_discovery(hass: HomeAssistant, entry_id: str) -> None:
    """Look for cars on the account that the entry has not met, in the background.

    Bootstrap runs once per entry and BMW replays nothing, so a car mapped later
    stays invisible until it happens to stream. Both flows that can change which
    cars are mapped (re-authorizing, choosing streamed data) call this once they
    finish; the service only spends a request when it finds a car.
    """

    async def _run() -> None:
        if not hass.services.has_service(DOMAIN, "fetch_vehicle_mappings"):
            return
        try:
            await hass.services.async_call(
                DOMAIN, "fetch_vehicle_mappings", {"entry_id": entry_id}, blocking=True
            )
        except Exception:  # noqa: BLE001 - a finished flow is not worth failing on this
            LOGGER.debug("Vehicle discovery after flow failed", exc_info=True)

    hass.async_create_background_task(_run(), f"{DOMAIN}_discover_after_flow")


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

    The wait / paste / done steps live here too, so both flows walk the same
    three screens; each flow says what happens afterwards in
    :meth:`_activation_finished`. That includes manual first-time setup, which
    used to hand out a console snippet for the portal's Data Selection page
    instead -- a second route to the same end that matched checkboxes by the
    portal's CSS class names.
    """

    # First-time setup may finish without activating (the fields can already be
    # ticked in the portal); reconfiguring exists only to activate, so its paste
    # screen insists on a result.
    _activation_paste_optional: bool = False

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

    def _webhook_report_urls(self, token: str) -> list[str]:
        """Every https address the activator could report this webhook to.

        External first: a browser that can reach both is usually the remote one,
        and the internal address may be a private name it cannot resolve. Only
        https survives -- the activator runs on an https portal page, so a POST
        to an http Home Assistant is blocked as mixed content before it is sent.
        """

        urls: list[str] = []
        for kwargs in ({"allow_internal": False}, {"allow_external": False}):
            try:
                url = webhook.async_generate_url(self.hass, token, **kwargs)
            except NoURLAvailableError:
                continue
            if url.startswith("https://") and url not in urls:
                urls.append(url)
        return urls

    def _start_activator(self, attributes: list[str]) -> None:
        """Host the helper page + webhook for one activator run.

        The activator runs on ``https://www.bmw.at`` and can only POST its result
        to an **https** Home Assistant; an http instance blocks that as mixed
        content. So the webhook (and the auto-continue wait) is only wired when HA
        is https -- otherwise the activator is clipboard-only and the caller goes
        straight to the paste screen. Sets ``_onboarding_auto`` and
        ``_onboarding_page_url`` for the caller to route on.

        The activator is handed *every* https address this instance answers on,
        not just the preferred one: on a split-horizon network the external URL
        resolves only from outside, so a browser on the couch could never report
        back (issue #23). It tries them in order and stops at the first that
        answers.
        """

        self._ensure_onboarding_view()

        token = webhook.async_generate_id()
        report_candidates = self._webhook_report_urls(token)
        auto = bool(report_candidates)
        self._onboarding_auto = auto

        bookmarklet = build_bookmarklet(attributes, report_url=report_candidates)
        console = build_console_snippet(attributes, report_url=report_candidates)
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
            self._onboarding_page_url = get_url(self.hass) + ONBOARDING_VIEW_URL + f"?token={token}"
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
            await asyncio.wait_for(self._onboarding_event.wait(), timeout=ONBOARDING_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        pending = (
            self.hass.data.get(DOMAIN, {})
            .get(_ONBOARDING_STORE, {})
            .get(self._onboarding_webhook_id)
        )
        return pending.get("result") if pending else None

    async def async_remove(self) -> None:
        """Flow teardown (abort/cancel/finish): drop the webhook + wait task."""

        self._cleanup_onboarding()
        if self._onboarding_wait_task and not self._onboarding_wait_task.done():
            self._onboarding_wait_task.cancel()

    async def _begin_activation(self, attributes: list[str]) -> FlowResult:
        """Run the activator for ``attributes``, then wait for it or ask for a paste."""

        self._start_activator(attributes)
        if self._onboarding_auto:
            return await self.async_step_activate_stream_wait()
        return await self.async_step_activate_stream_paste()

    async def _activation_finished(self) -> FlowResult:
        """Where the flow goes once activation is confirmed (or skipped)."""

        raise NotImplementedError

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
                description_placeholders=activation_link_placeholders(self._onboarding_page_url),
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

        field = (
            vol.Optional("result", default="")
            if self._activation_paste_optional
            else vol.Required("result")
        )
        schema = vol.Schema({field: str})
        if user_input is None:
            return self.async_show_form(
                step_id="activate_stream_paste",
                data_schema=schema,
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": "",
                },
            )

        raw = (user_input.get("result") or "").strip()
        if not raw and self._activation_paste_optional:
            self._cleanup_onboarding()
            return await self._activation_finished()
        try:
            result = parse_onboarding_result(raw)
        except OnboardingParseError as err:
            return self.async_show_form(
                step_id="activate_stream_paste",
                data_schema=schema,
                errors={"result": "invalid_result"},
                description_placeholders={
                    **activation_link_placeholders(self._onboarding_page_url),
                    "error": str(err),
                },
            )
        self._onboarding = result
        return await self.async_step_activate_stream_done()

    async def async_step_activate_stream_done(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm what the activator turned on, then hand back to the flow."""

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
        return await self._activation_finished()


class CardataConfigFlow(_StreamActivatorFlow, config_entries.ConfigFlow, domain=DOMAIN):
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
            _schedule_vehicle_discovery(self.hass, self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        self._entry_title = (
            f"BavarianData: Connect Home Assistant to BMW CarData ({self._client_id[:8]})"
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
        async_step_action_select_clusters`) and runs the same in-browser
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

        # Preserve the catalogue's cluster order regardless of checkbox order.
        chosen = [slug for slug in labels if slug in set(user_input.get("sections", []))]
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
                    runtime.reauth_in_progress = False
                    runtime.reauth_flow_id = None
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


LOGGER = logging.getLogger(__name__)


# Form keys that are not options, shared by every screen with per-car
# settings (Trips, Charging costs, evcc bridge): the vehicle picker's field,
# and the section holding the settings every car shares.
_VEHICLE_FIELD = "vehicle"
# ``_vehicle_context``'s "no car picked yet" (``None`` means "no car known").
_PICK = object()
_SHARED_SECTION = "all_vehicles"


def _phone_route_follows(values: Mapping[str, Any]) -> bool:
    """True when Trips is followed by the phone screen: a car on end-of-trip
    reports that records its route, and only such a car."""

    return values.get(OPTION_TRIP_DETECTION) == TRIP_DETECTION_REPORTS and bool(
        values.get(OPTION_TRIP_TRACK)
    )


class CardataOptionsFlowHandler(_StreamActivatorFlow, config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._reauth_client_id: Optional[str] = None
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
                "action_data_refresh",
                "action_charging_costs",
                "action_energy_sources",
                "action_evcc_bridge",
                "action_trips",
                "action_mileage",
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
        current_client_id = self._reauth_client_id or self._config_entry.data.get("client_id") or ""
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
        }
        if per_vehicle:
            # Expanded, unlike the other screens' shared sections: the price is
            # the main thing this screen is for.
            schema = vol.Schema(
                {
                    **car_fields,
                    vol.Required(_SHARED_SECTION): section(
                        vol.Schema({**price_fields, **history_fields}), {"collapsed": False}
                    ),
                }
            )
        else:
            schema = vol.Schema({**price_fields, **car_fields, **history_fields})

        def _form(errors: Optional[Dict[str, str]] = None) -> FlowResult:
            return self.async_show_form(
                step_id="action_charging_costs",
                data_schema=schema,
                errors=errors,
                description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
            )

        if user_input is None:
            return _form()

        submitted = dict(user_input)
        shared = submitted.pop(_SHARED_SECTION, None)
        if shared is None:
            shared = {
                key: submitted.pop(key) for key in list(submitted) if key not in CHARGING_OPTIONS
            }

        mode = shared.get(OPTION_PRICE_MODE, MODE_NONE)
        if mode == MODE_FIXED and shared.get(OPTION_PRICE_FIXED) is None:
            return _form({"base": "price_required"})
        if mode == MODE_ENTITY and not shared.get(OPTION_PRICE_ENTITY):
            return _form({"base": "price_entity_required"})

        options.update(shared)
        options = store_values(
            options, submitted, CHARGING_OPTIONS, vin=vin, per_vehicle=per_vehicle
        )
        # Apply immediately rather than reloading the entry: BMW allows only one
        # concurrent stream per account, so a reload risks racing the reconnect.
        runtime = getattr(self._config_entry, "runtime_data", None)
        if runtime is not None:
            runtime.coordinator.pricing = PricingConfig.from_options(options)
            runtime.coordinator.vehicle_pricing = vehicle_pricing(options)
            if runtime.history is not None:
                months = int(shared.get(OPTION_HISTORY_RETAIN_MONTHS, 0) or 0)
                runtime.history.retain_months = months if months > 0 else None
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
        runtime = getattr(self._config_entry, "runtime_data", None)
        if runtime is not None:
            runtime.coordinator.pricing = PricingConfig.from_options(options)
            runtime.coordinator.vehicle_pricing = vehicle_pricing(options)
        return self.async_create_entry(title="", data=options)

    async def async_step_action_evcc_bridge(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
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
                    vol.Required(_SHARED_SECTION): section(
                        vol.Schema(shared_fields), {"collapsed": True}
                    ),
                }
            )
        else:
            schema = vol.Schema({**car_fields, **shared_fields})

        def _form(errors: Optional[Dict[str, str]] = None) -> FlowResult:
            return self.async_show_form(
                step_id="action_evcc_bridge",
                data_schema=schema,
                errors=errors,
                description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
            )

        if user_input is None:
            return _form()

        submitted = dict(user_input)
        shared = submitted.pop(_SHARED_SECTION, None)
        if shared is None:
            shared = {
                key: submitted.pop(key) for key in list(submitted) if key not in BRIDGE_OPTIONS
            }

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
        if bridge is not None:
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
    ) -> FlowResult:
        """Hand over the paste-ready evcc config, then save.

        Shown here as well as through the ``get_evcc_config`` action because
        this is the moment it is wanted: the user has just switched the bridge
        on, and the next thing they do is edit ``evcc.yaml``.
        """

        options = getattr(self, "_bridge_options", None) or dict(self._config_entry.options)
        if user_input is None:
            return self.async_show_form(
                step_id="evcc_snippet",
                data_schema=vol.Schema({}),
                description_placeholders={"snippet": self._evcc_snippet(options)},
            )
        return self.async_create_entry(title="", data=options)

    def _evcc_snippet(self, options: Dict[str, Any]) -> str:
        """Every known car as one evcc ``vehicles:`` block.

        One ``vehicles:`` key however many cars: repeating the key in a single
        YAML file is invalid and evcc refuses to start on it. Only topics that
        are actually being published are referenced -- a plugin pointed at a
        topic no message ever arrives on makes evcc log a read error on every
        update cycle.
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
        if not blocks:
            return "(no vehicle seen yet -- wait for the first stream message)"
        joined = "\n".join(
            block if index == 0 else block.split("\n", 1)[1] for index, block in enumerate(blocks)
        )
        return joined.strip()

    def _vehicles(self) -> list[tuple[str, str]]:
        """``(vin, label)`` for each car on the account, in name order.

        Two cars of the same model share a name, so a repeated name gets the
        end of its VIN -- the picker must never offer two identical entries.
        """

        runtime = self._get_runtime()
        if runtime is None:
            return []
        coordinator = runtime.coordinator
        named = [
            (
                vin,
                (coordinator.device_metadata.get(vin) or {}).get("name")
                or coordinator.names.get(vin)
                or vin,
            )
            for vin in coordinator.data
        ]
        counts: Dict[str, int] = {}
        for _vin, name in named:
            counts[name] = counts.get(name, 0) + 1
        labelled = [
            (vin, f"{name} (…{vin[-4:]})" if counts[name] > 1 else name) for vin, name in named
        ]
        return sorted(labelled, key=lambda item: item[1].casefold())

    async def async_step_action_trips(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
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
                    vol.Required(_SHARED_SECTION): section(
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
                last_step=not _phone_route_follows(values),
            )

        submitted = dict(user_input)
        shared = submitted.pop(_SHARED_SECTION, None) or {
            key: submitted[key]
            for key in (OPTION_TRIP_GEOCODE, OPTION_TRIP_DEBUG)
            if key in submitted
        }
        # Not on this form: kept as they are unless the phone screen changes them.
        for key in (OPTION_TRIP_PHONE_TRACKER, OPTION_TRIP_PHONE_IN_CAR):
            submitted[key] = values.get(key)
        if _phone_route_follows(submitted):
            self._trips_pending = (submitted, shared)
            return await self.async_step_trips_phone()
        return self._save_trips(submitted, shared)

    async def async_step_trips_phone(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Where the route of a car on end-of-trip reports comes from.

        Shown after Trips only to a car that records its route and reports only
        once parked: such a car sends nothing while driving, so its route can
        come from a phone that rides along (``phone_route.py``).
        """

        vehicles, _per_vehicle, vin = self._vehicle_context()
        submitted, shared = self._trips_pending
        errors: dict[str, str] = {}
        if user_input is not None:
            tracker = user_input.get(OPTION_TRIP_PHONE_TRACKER) or None
            in_car = user_input.get(OPTION_TRIP_PHONE_IN_CAR) or None
            if in_car and not tracker:
                errors[OPTION_TRIP_PHONE_IN_CAR] = "phone_required"
            else:
                submitted = {
                    **submitted,
                    OPTION_TRIP_PHONE_TRACKER: tracker,
                    OPTION_TRIP_PHONE_IN_CAR: in_car,
                }
                return self._save_trips(submitted, shared)
            submitted = {**submitted, OPTION_TRIP_PHONE_TRACKER: tracker}
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
            }
        )
        return self.async_show_form(
            step_id="trips_phone",
            data_schema=schema,
            errors=errors,
            description_placeholders={"vehicle": self._vehicle_label(vehicles, vin)},
        )

    def _save_trips(self, submitted: Dict[str, Any], shared: Mapping[str, Any]) -> FlowResult:
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
        return dict(vehicles).get(vin) or self._config_entry.title

    async def _vehicle_picker(
        self, *, step_id: str, user_input: Optional[Dict[str, Any]], then: Any
    ) -> FlowResult:
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
    ) -> FlowResult:
        return await self._vehicle_picker(
            step_id="trips_vehicle", user_input=user_input, then=self.async_step_action_trips
        )

    async def async_step_charging_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._vehicle_picker(
            step_id="charging_vehicle",
            user_input=user_input,
            then=self.async_step_action_charging_costs,
        )

    async def async_step_bridge_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._vehicle_picker(
            step_id="bridge_vehicle", user_input=user_input, then=self.async_step_action_evcc_bridge
        )

    async def async_step_mileage_vehicle(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        return await self._vehicle_picker(
            step_id="mileage_vehicle", user_input=user_input, then=self.async_step_action_mileage
        )

    # --- Mileage allowance ---------------------------------------------------
    #
    # A lease or a yearly limit (insurance) per car, compared with the odometer
    # log (``mileage.py``). Always the car's own -- even on a single-car account
    # -- so a car added later never inherits a lease. Two screens: the kind,
    # then that kind's form (HA forms can't show fields conditionally).

    def _miles(self) -> bool:
        """Whether the car on screen counts in miles (``distance.car_distance_unit``).

        Not Home Assistant's unit system alone: a UK install is metric but its
        car counts in miles, and typed miles were stored as km (issue #126).
        """

        runtime = self._get_runtime()
        coordinator = runtime.coordinator if runtime is not None else None
        _vehicles, _per_vehicle, vin = self._vehicle_context()
        if not isinstance(vin, str):  # no car picked yet
            vin = None
        return car_distance_unit(self.hass, coordinator, vin) == "mi"

    def _to_display(self, km: Optional[float]) -> Optional[float]:
        if km is None:
            return None
        return round(km / KM_PER_MILE if self._miles() else km)

    def _to_km(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        number = float(value)
        return round(number * KM_PER_MILE if self._miles() else number, 1)

    def _mileage_number(self, *, maximum: int) -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=maximum,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="mi" if self._miles() else "km",
            )
        )

    def _odometer_now(self, vin: Optional[str]) -> Optional[float]:
        runtime = self._get_runtime()
        if runtime is None or vin is None:
            return None
        return runtime.coordinator.odometer_km(vin)

    async def async_step_action_mileage(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
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

    def _save_mileage(self, vin: str, values: Dict[str, Any]) -> FlowResult:
        options = store_mileage(dict(self._config_entry.options), vin, values)
        runtime = self._get_runtime()
        if runtime is not None:
            # Applies at once: the sensor and the card recompute on the signal.
            runtime.coordinator.mileage_allowances = mileage_allowances(options)
            async_dispatcher_send(self.hass, runtime.coordinator.signal_mileage, vin)
        return self.async_create_entry(title="", data=options)

    async def async_step_mileage_lease(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """A lease or financing: start, end, allowance a year, odometer at handover."""

        return await self._mileage_form(user_input, step_id="mileage_lease", kind=KIND_LEASE)

    async def async_step_mileage_yearly(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """A yearly limit: the day it renews, the allowance, the odometer that day."""

        return await self._mileage_form(user_input, step_id="mileage_yearly", kind=KIND_YEARLY)

    async def _mileage_form(
        self, user_input: Optional[Dict[str, Any]], *, step_id: str, kind: str
    ) -> FlowResult:
        vehicles, _per_vehicle, vin = self._vehicle_context()
        current = mileage_values(self._config_entry.options, vin)
        values = dict(current) if current.get(MILEAGE_KIND) == kind else {}
        if user_input is not None:
            values = {
                MILEAGE_START: user_input.get(MILEAGE_START),
                MILEAGE_END: user_input.get(MILEAGE_END),
                MILEAGE_PER_YEAR: self._to_km(user_input.get(MILEAGE_PER_YEAR)),
                MILEAGE_START_KM: self._to_km(user_input.get(MILEAGE_START_KM)),
            }
        if user_input is None and not values.get(MILEAGE_START):
            # A fresh form shows dates that make sense together -- an empty date
            # field reads as today in HA, which made start and end the same day.
            # Three years is the usual lease; the help lines say what to change.
            today = dt_util.now().date()
            values[MILEAGE_START] = today.isoformat()
            if kind == KIND_LEASE:
                values[MILEAGE_END] = (
                    _anniversary(today, today.year + 3) - timedelta(days=1)
                ).isoformat()
        start_km = values.get(MILEAGE_START_KM)
        if start_km is None and user_input is None:
            # The handover reading is unknown to us; today's is the honest
            # prefill, and the field's help line says so.
            start_km = self._odometer_now(vin)
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
                description={"suggested_value": self._to_display(values.get(MILEAGE_PER_YEAR))},
            )
        ] = self._mileage_number(maximum=200000)
        fields[
            vol.Required(
                MILEAGE_START_KM, description={"suggested_value": self._to_display(start_km)}
            )
        ] = self._mileage_number(maximum=2000000)
        placeholders = {"vehicle": self._vehicle_label(vehicles, vin)}

        errors: Dict[str, str] = {}
        if user_input is not None:
            stored = {MILEAGE_KIND: kind, **values}
            if kind != KIND_LEASE:
                stored[MILEAGE_END] = None
                # The odometer typed in belongs to the latest start of the
                # year, so that is the start stored; later years read theirs
                # from the log.
                if (first := _parse_date(values.get(MILEAGE_START))) is not None:
                    today = dt_util.now().date()
                    if first <= today:
                        stored[MILEAGE_START] = yearly_period(first, today)[0].isoformat()
            if not values.get(MILEAGE_PER_YEAR):
                errors[MILEAGE_PER_YEAR] = "invalid_int"
            elif allowance_settings(stored) is None:
                errors[MILEAGE_END] = "mileage_end_before_start"
            elif vin is None:
                errors["base"] = "no_vins"
            else:
                return self._save_mileage(vin, stored)
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(fields),
            description_placeholders=placeholders,
            errors=errors,
        )

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
                await self.hass.services.async_call(
                    DOMAIN,
                    "fetch_vehicle_image",
                    {"entry_id": self._config_entry.entry_id},
                    blocking=True,
                )
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
            description_placeholders={"vehicle": vehicle},
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

        return await self._begin_activation(attributes)

    async def _activation_finished(self) -> FlowResult:
        """The selection was already saved; close the options flow.

        The portal step is what maps a second car, so look for it now instead of
        leaving it invisible until it streams.
        """

        _schedule_vehicle_discovery(self.hass, self._config_entry.entry_id)
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


async def async_get_options_flow(
    config_entry: config_entries.ConfigEntry,
) -> config_entries.OptionsFlow:
    return CardataOptionsFlowHandler(config_entry)
