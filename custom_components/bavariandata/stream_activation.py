"""Activate BMW CarData MQTT-stream attributes via the customer portal.

Stream *attribute selection* ("Datenauswahl" / stream setup) is the one piece of
CarData that has **no documented REST API**. It lives only behind the market
portal (``https://www.bmw.at``, ``https://www.bmw.de`` …), whose stream-setup
page saves the selection with a single request:

    POST {base_url}/{locale}/utilities/bmw/api/cd/streams/{mapped_vehicle_id}
    Content-Type: application/json
    {"attributes": ["vehicle.isMoving", …]}   # replace the whole list

The portal authenticates that call with an **interactive browser session**
(cookies incl. ``gcdmToken`` plus Akamai bot-defense cookies), *not* the OAuth
device-flow token the rest of the integration uses against
``api-cardata.bmwgroup.com``. Those two are different hosts and different
credentials — the API Bearer token is rejected here. This module therefore
takes a **captured portal session** supplied by the caller; it deliberately does
**not** try to mint or refresh one (that would mean replicating the web login /
browser automation, which is out of scope and defeated by the bot-defense layer).

Kept free of Home Assistant imports so it is unit-testable in isolation (see
``tests/conftest.py``) and reusable from setup, reconfiguration or a manual
"sync stream fields" service.

Confirmed contract (reverse-engineered from a real portal HAR, 2026-07-25):

* Request body is the **flat** ``{"attributes": [...]}`` — note it is *not*
  wrapped in ``{"data": {...}}`` (that wrapper only appears in the *response*).
* The POST is a **set/replace**: the response echoes back the full list under
  ``data.attributes``.
* Success is HTTP ``201`` with a JSON envelope
  ``{"data": {...}, "success": true, "status": ..., "message": ...}``.
* ``GET …/streams/{id}?includeAttributes=true`` returns the currently selected
  attributes (``data.attributes``) plus the MQTT connection details.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import aiohttp

try:  # normal case: imported as part of the package
    from .descriptors import default_sections, descriptors_for_sections
except ImportError:  # pragma: no cover - loaded standalone (tests / tools)
    from descriptors import default_sections, descriptors_for_sections

_LOGGER = logging.getLogger(__name__)

# A stream-setup save is a quick same-origin call; keep the timeout tight so a
# hung portal never blocks the caller (mirrors api.DEFAULT_TIMEOUT).
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# A neutral desktop UA. The portal / Akamai layer expects a browser-shaped
# request; the captured session was taken from one, so we present the same kind
# of client. Overridable via PortalSession.user_agent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Firefox/153.0"
)


def default_stream_attributes() -> list[str]:
    """The default desired attribute list: every descriptor in the default clusters.

    Sourced from the same catalogue-driven cluster derivation the config flow and
    the portal snippet use (:func:`descriptors.descriptors_for_sections`), so
    "activate stream fields" and "Choose streamed data" can never disagree about
    what a cluster contains. Deduplicated and sorted (the derivation already
    sorts); callers may override by passing their own list or ``sections``.
    """

    return descriptors_for_sections(default_sections())


# Maintained default. Kept as a module constant per the feature spec so it is
# easy to import and later override from config options; it is derived, not
# hand-listed, so it can never drift from the descriptor catalogue.
DEFAULT_STREAM_ATTRIBUTES: list[str] = default_stream_attributes()


def normalize_attributes(attributes: Iterable[str]) -> list[str]:
    """Deduplicate, trim and sort an attribute list for a deterministic request.

    Sorting + de-duping means posting "the same selection" always produces the
    exact same bytes, which is what makes the call idempotent from our side even
    though BMW treats it as create-or-replace internally.
    """

    return sorted({a.strip() for a in attributes if a and a.strip()})


class StreamActivationError(Exception):
    """Raised when a portal stream-attribute request fails.

    ``kind`` classifies the failure so callers can react without re-parsing HTTP
    codes: ``auth`` (session expired / rejected — 401/403), ``validation`` (bad
    attribute / payload — 400), ``not_found`` (wrong mapped-vehicle id or locale
    — 404), ``server`` (retryable BMW backend issue — 5xx), ``network`` (transport
    error) or ``unexpected``. ``retryable`` is set for the transient kinds.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        kind: str = "unexpected",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retryable = retryable


def _classify(status: int) -> tuple[str, bool]:
    """Map an HTTP status onto (kind, retryable)."""

    if status in (401, 403):
        return "auth", False
    if status == 400:
        return "validation", False
    if status == 404:
        return "not_found", False
    if status >= 500:
        return "server", True
    return "unexpected", False


@dataclass(frozen=True)
class PortalSession:
    """A captured, authenticated BMW *portal* session for one market.

    ``base_url`` is the market origin (``https://www.bmw.at``), ``locale`` the
    path segment (``de-at``), and ``cookie`` the raw ``Cookie`` header string
    copied from an authenticated portal request. ``cookie`` is a secret and is
    never logged.
    """

    base_url: str
    locale: str
    cookie: str
    user_agent: str = DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        # Normalize so callers can pass a trailing slash without doubling it up.
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "locale", self.locale.strip("/"))

    def stream_url(self, mapped_vehicle_id: str) -> str:
        return (
            f"{self.base_url}/{self.locale}"
            f"/utilities/bmw/api/cd/streams/{mapped_vehicle_id}"
        )

    def referer(self, mapped_vehicle_id: str) -> str:
        return (
            f"{self.base_url}/{self.locale}"
            f"/mybmw/mapped-vehicle/{mapped_vehicle_id}/cardata/stream-setup"
        )

    def headers(self, mapped_vehicle_id: str) -> dict[str, str]:
        """Build the header set the portal's stream-setup page sends.

        The cookie carries the session; the rest mirror the captured browser
        request. The ``Sec-Fetch-*`` / ``Accept-Language`` / ``Accept-Encoding``
        set is **not optional decoration**: the portal sits behind Akamai
        bot-defense, which *stalls* (no response, connection hangs) a request
        that doesn't look browser-shaped. Verified against the live endpoint —
        without these the GET/POST time out; with them it returns 200/201.

        ``Accept-Encoding`` is limited to ``gzip, deflate`` (what aiohttp always
        decodes) rather than the browser's ``br, zstd`` so the response is never
        an undecodable body in an environment lacking those codecs.
        """

        return {
            "Accept": "application/json",
            "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": self.referer(mapped_vehicle_id),
            "x-ocp-stage": "prod",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.user_agent,
            "Connection": "keep-alive",
            "Cookie": self.cookie,
        }


@dataclass
class StreamSelection:
    """The current stream selection as returned by the portal (read side)."""

    attributes: list[str] = field(default_factory=list)
    # MQTT connection facts the portal returns alongside the selection. Handy for
    # diagnostics; never required by this module.
    host: Optional[str] = None
    port: Optional[int] = None
    topic: Optional[str] = None
    username: Optional[str] = None


@dataclass
class StreamActivationResult:
    """Outcome of a set/replace call."""

    mapped_vehicle_id: str
    requested: list[str]
    accepted: list[str]
    unchanged: bool = False

    @property
    def requested_count(self) -> int:
        return len(self.requested)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)


def _mask(mapped_vehicle_id: str) -> str:
    """A short, non-identifying handle for logs (the id is account-specific)."""

    return f"{mapped_vehicle_id[:8]}…" if len(mapped_vehicle_id) > 8 else "…"


def _parse_envelope(status: int, text: str, *, action: str) -> Any:
    """Parse a portal JSON envelope, raising a classified error on failure."""

    try:
        payload: Any = json.loads(text) if text else None
    except json.JSONDecodeError:
        payload = None

    if status in (200, 201):
        # A 2xx with success:false is still a failure — surface the message.
        if isinstance(payload, dict) and payload.get("success") is False:
            message = payload.get("message") or "portal reported success=false"
            raise StreamActivationError(
                f"{action} rejected by portal: {message}",
                status=status,
                kind="validation",
            )
        return payload

    kind, retryable = _classify(status)
    detail = ""
    if isinstance(payload, dict):
        detail = payload.get("message") or payload.get("status") or ""
    raise StreamActivationError(
        f"{action} failed: HTTP {status}" + (f" ({detail})" if detail else ""),
        status=status,
        kind=kind,
        retryable=retryable,
    )


class PortalStreamClient:
    """Send stream-attribute selections to the BMW market portal.

    Carries the :class:`PortalSession` (auth context) so the public methods keep
    the ``(mapped_vehicle_id, attributes)`` shape asked for by the feature spec.
    """

    def __init__(self, http: aiohttp.ClientSession, session: PortalSession) -> None:
        self._http = http
        self._session = session

    async def async_get_stream_attributes(
        self, mapped_vehicle_id: str
    ) -> StreamSelection:
        """Read the currently selected stream attributes for a mapped vehicle.

        GET ``…/streams/{id}?includeAttributes=true``. Used for read-before-write
        so we can skip a no-op replace, and as a standalone discovery helper.
        """

        url = self._session.stream_url(mapped_vehicle_id)
        headers = self._session.headers(mapped_vehicle_id)
        # A read needs no request body.
        headers.pop("Content-Type", None)
        try:
            async with self._http.get(
                url,
                headers=headers,
                params={"includeAttributes": "true"},
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                text = await response.text()
                payload = _parse_envelope(
                    response.status, text, action="Reading stream attributes"
                )
        except asyncio.TimeoutError as err:
            raise StreamActivationError(
                "Timed out reading stream attributes: the portal did not respond "
                "(often Akamai bot-defense throttling repeated automated calls). "
                "Retry with a freshly captured session.",
                kind="network",
                retryable=True,
            ) from err
        except aiohttp.ClientError as err:
            raise StreamActivationError(
                f"Network error reading stream attributes: {err}",
                kind="network",
                retryable=True,
            ) from err

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return StreamSelection()
        attrs = data.get("attributes")
        return StreamSelection(
            attributes=list(attrs) if isinstance(attrs, list) else [],
            host=data.get("host"),
            port=data.get("port"),
            topic=data.get("topic"),
            username=data.get("username"),
        )

    async def async_set_stream_attributes(
        self,
        mapped_vehicle_id: str,
        attributes: Iterable[str],
        *,
        skip_if_unchanged: bool = True,
    ) -> StreamActivationResult:
        """Set (replace) the streamed attribute list for a mapped vehicle.

        The list is deduplicated and sorted first, so re-running with the same
        selection is a no-op from our side. With ``skip_if_unchanged`` (default)
        the current selection is read first and the POST is skipped when it
        already matches — saving a redundant write while staying idempotent.

        Logs the requested vs. accepted counts; never logs cookies or attribute
        values that could reveal the session.
        """

        desired = normalize_attributes(attributes)

        if skip_if_unchanged:
            try:
                current = await self.async_get_stream_attributes(mapped_vehicle_id)
            except StreamActivationError as err:
                # A failed pre-read shouldn't block the write we were asked to do;
                # fall through and POST. (A hard auth failure will resurface there.)
                _LOGGER.debug(
                    "Stream activation for %s: pre-read failed (%s); posting anyway",
                    _mask(mapped_vehicle_id),
                    err.kind,
                )
            else:
                if normalize_attributes(current.attributes) == desired:
                    _LOGGER.info(
                        "Stream activation for %s: %d attributes already selected; "
                        "nothing to do",
                        _mask(mapped_vehicle_id),
                        len(desired),
                    )
                    return StreamActivationResult(
                        mapped_vehicle_id=mapped_vehicle_id,
                        requested=desired,
                        accepted=list(current.attributes),
                        unchanged=True,
                    )

        url = self._session.stream_url(mapped_vehicle_id)
        headers = self._session.headers(mapped_vehicle_id)
        body = {"attributes": desired}
        try:
            async with self._http.post(
                url,
                headers=headers,
                json=body,
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                text = await response.text()
                payload = _parse_envelope(
                    response.status, text, action="Setting stream attributes"
                )
        except asyncio.TimeoutError as err:
            raise StreamActivationError(
                "Timed out setting stream attributes: the portal did not respond "
                "(often Akamai bot-defense throttling repeated automated calls). "
                "Retry with a freshly captured session.",
                kind="network",
                retryable=True,
            ) from err
        except aiohttp.ClientError as err:
            raise StreamActivationError(
                f"Network error setting stream attributes: {err}",
                kind="network",
                retryable=True,
            ) from err

        accepted = desired
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("attributes"), list):
                accepted = list(data["attributes"])

        _LOGGER.info(
            "Stream activation for %s: requested %d attributes, portal accepted %d",
            _mask(mapped_vehicle_id),
            len(desired),
            len(accepted),
        )
        return StreamActivationResult(
            mapped_vehicle_id=mapped_vehicle_id,
            requested=desired,
            accepted=accepted,
        )
