"""Helpers for the MyBMW Device Code OAuth 2.0 flow."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp

from .const import DEVICE_CODE_URL, TOKEN_URL


class CardataAuthError(Exception):
    """Raised when the BMW OAuth service rejects a request.

    Carries the structured pieces of the failure (HTTP status, OAuth ``error``
    code, human description and a BMW correlation id when present) so the config
    flow can render a useful details pane instead of an opaque one-liner.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        error_code: Optional[str] = None,
        error_description: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.error_description = error_description
        self.correlation_id = correlation_id


# Network timeout for individual OAuth requests. A single poll/refresh must never
# hang the event loop indefinitely.
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Response headers BMW / its gateway use to tag a request for support tracing.
# The first one present is surfaced so a user can quote it when contacting BMW.
_CORRELATION_HEADERS = (
    "x-correlation-id",
    "x-correlationid",
    "correlation-id",
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
    "x-amzn-trace-id",
)


def _correlation_id(headers: Any) -> Optional[str]:
    """Extract a BMW/gateway correlation id from response headers, if any."""

    if not headers:
        return None
    for name in _CORRELATION_HEADERS:
        try:
            value = headers.get(name)
        except AttributeError:
            return None
        if value:
            return str(value)
    return None


def _safe_error(status: int, data: Any, *, headers: Any = None) -> str:
    """Build an error string without leaking tokens from the response body.

    Successful (200) responses carry access/refresh/id tokens. Only the OAuth
    error fields are safe to surface in exceptions/logs.
    """

    if isinstance(data, dict):
        code = data.get("error")
        description = data.get("error_description")
        if code and description and description != code:
            detail = f"{code}: {description}"
        else:
            detail = description or code or "unknown_error"
    else:
        detail = "non-JSON response"
    message = f"{status}: {detail}"
    correlation = _correlation_id(headers)
    if correlation:
        message = f"{message} [ref: {correlation}]"
    return message


def _auth_error(status: int, data: Any, headers: Any = None) -> "CardataAuthError":
    """Build a structured :class:`CardataAuthError` from an OAuth error response."""

    code = data.get("error") if isinstance(data, dict) else None
    description = data.get("error_description") if isinstance(data, dict) else None
    return CardataAuthError(
        f"Token polling failed ({_safe_error(status, data, headers=headers)})",
        status=status,
        error_code=code,
        error_description=description,
        correlation_id=_correlation_id(headers),
    )


def _access_denied_error(
    status: int, data: Any, headers: Any = None, *, saw_server_error: bool = False
) -> "CardataAuthError":
    """Build the terminal ``access_denied`` error with a clearer explanation.

    By the time this is raised we have already polled through
    ``ACCESS_DENIED_GRACE``, so a genuine just-approved grant would have
    propagated. A persistent ``access_denied`` here is almost always BMW's
    device-authorization backend failing to finalize consent — frequently right
    after a 5xx while finalizing — rather than a decline the user actually made.
    Say so, so people don't chase client-id / scope problems that aren't there.
    """

    hint = (
        "BMW reported access_denied after approval — a known BMW-side backend "
        "issue finalizing consent, not a decline you made"
    )
    if saw_server_error:
        hint += "; a BMW server error (5xx) occurred earlier in this attempt"
    return CardataAuthError(
        f"{hint} ({_safe_error(status, data, headers=headers)})",
        status=status,
        error_code=data.get("error") if isinstance(data, dict) else None,
        error_description=data.get("error_description") if isinstance(data, dict) else None,
        correlation_id=_correlation_id(headers),
    )


async def request_device_code(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    scope: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
) -> Dict[str, Any]:
    """Request a device & user code pair from BMW."""

    data = {
        "client_id": client_id,
        "scope": scope,
        "response_type": "device_code",
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    async with session.post(DEVICE_CODE_URL, data=data, timeout=HTTP_TIMEOUT) as resp:
        payload = await resp.json(content_type=None)
        if resp.status != 200:
            raise CardataAuthError(
                f"Device code request failed ({_safe_error(resp.status, payload)})"
            )
        return payload


# BMW notes that after the user approves the device it can take up to ~1 minute
# for the access grant to actually propagate. During that window the token
# endpoint transiently returns ``access_denied`` even though the user did NOT
# decline. Keep polling through this grace window before treating access_denied
# as a genuine decline.
ACCESS_DENIED_GRACE = 120

# Never poll the device-code endpoint faster than this, to avoid flooding BMW's
# service even if the server-supplied interval is missing or too small.
MIN_POLL_INTERVAL = 5


async def poll_for_tokens(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    device_code: str,
    code_verifier: str,
    interval: int,
    timeout: int = 900,
    token_url: str = TOKEN_URL,
) -> Dict[str, Any]:
    """Poll the token endpoint until tokens are issued or timeout elapsed."""

    start = time.monotonic()
    payload = {
        "client_id": client_id,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "code_verifier": code_verifier,
    }

    # Respect BMW's recommended interval but never poll faster than the floor.
    interval = max(int(interval), MIN_POLL_INTERVAL)

    # BMW's token backend intermittently returns 5xx (and occasionally 429) for a
    # few seconds right after the user approves the device, while it finalizes the
    # grant. Treat those as transient and keep polling within the timeout window
    # instead of aborting the whole flow. A network hiccup raising during the POST
    # is handled the same way.
    consecutive_transient = 0
    max_consecutive_transient = 10
    # Timestamp of the first access_denied seen; used to bound the grace window.
    access_denied_since: Optional[float] = None
    # Whether BMW returned a 5xx/429 at any point this attempt. A terminal
    # access_denied that follows one is the tell-tale of the backend consent bug.
    saw_server_error = False

    while True:
        if time.monotonic() - start > timeout:
            raise CardataAuthError("Timed out waiting for device authorization")

        try:
            async with session.post(token_url, data=payload, timeout=HTTP_TIMEOUT) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 200:
                    return data

                error = data.get("error") if isinstance(data, dict) else None
                if error in {"authorization_pending", "slow_down"}:
                    consecutive_transient = 0
                    access_denied_since = None
                    # slow_down means BMW wants us to back off: raise the interval
                    # permanently (per the OAuth device-flow spec) so we don't keep
                    # hammering the endpoint.
                    if error == "slow_down":
                        interval += 5
                    await asyncio.sleep(interval)
                    continue

                # A just-approved grant can take up to ~1 minute to propagate, during
                # which BMW reports access_denied even though the user approved. Keep
                # polling through the grace window before surfacing it as a decline.
                if error == "access_denied":
                    now = time.monotonic()
                    if access_denied_since is None:
                        access_denied_since = now
                    if now - access_denied_since <= ACCESS_DENIED_GRACE:
                        await asyncio.sleep(interval)
                        continue
                    raise _access_denied_error(
                        resp.status,
                        data,
                        resp.headers,
                        saw_server_error=saw_server_error,
                    )

                # Transient server-side failure: retry rather than fail the flow.
                if resp.status >= 500 or resp.status == 429:
                    saw_server_error = True
                    consecutive_transient += 1
                    if consecutive_transient > max_consecutive_transient:
                        raise _auth_error(resp.status, data, resp.headers)
                    await asyncio.sleep(interval + 5)
                    continue

                raise _auth_error(resp.status, data, resp.headers)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            consecutive_transient += 1
            if consecutive_transient > max_consecutive_transient:
                raise CardataAuthError(
                    f"Token polling failed (network error: {err})"
                ) from err
            await asyncio.sleep(interval + 5)
            continue


async def refresh_tokens(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    refresh_token: str,
    scope: Optional[str] = None,
    token_url: str = TOKEN_URL,
) -> Dict[str, Any]:
    """Refresh access/ID tokens using the stored refresh token."""

    payload = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if scope:
        payload["scope"] = scope

    async with session.post(token_url, data=payload, timeout=HTTP_TIMEOUT) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            raise CardataAuthError(
                f"Token refresh failed ({_safe_error(resp.status, data)})"
            )
        return data
