"""The in-browser stream activator both flows share.

BMW's stream selection has no API and lives behind an interactive portal
session, so activation runs in the user's *own* browser: Home Assistant hosts a
helper page carrying a one-click bookmarklet (plus a console fallback) and a
one-time webhook the activator reports its result back to. First-time setup
(``config_flow.py``) and "Choose streamed data" under Configure
(``options_flow.py``) both mix in :class:`StreamActivatorFlow`, so they walk
the same wait / paste / done screens.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Dict, Optional

from aiohttp import web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN
from .flow_answers import activation_done_placeholders
from .onboarding import (
    OnboardingParseError,
    OnboardingResult,
    activation_link_placeholders,
    build_bookmarklet,
    build_console_snippet,
    build_helper_page,
    parse_onboarding_result,
)

LOGGER = logging.getLogger(__name__)

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


def schedule_vehicle_discovery(hass: HomeAssistant, entry_id: str) -> None:
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


class StreamActivatorFlow:
    """Shared plumbing for the in-browser stream-field activator.

    Both first-time guided setup (``CardataConfigFlow``) and the later "Choose
    streamed data" reconfiguration (``CardataOptionsFlowHandler``) drive the
    very same activator, so neither path needs any copy-paste. Relies only on
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

    @callback
    def async_remove(self) -> None:
        """Flow teardown (abort/cancel/finish): drop the webhook + wait task.

        Home Assistant calls this synchronously and never awaits it: as a
        coroutine it only raised a "never awaited" warning, and a flow closed
        mid-wait kept its webhook registered (#142's log).
        """

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

        if user_input is None:
            placeholders = activation_done_placeholders(self._onboarding)
            self._cleanup_onboarding()
            return self.async_show_form(
                step_id="activate_stream_done",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        return await self._activation_finished()
