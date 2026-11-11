"""One way into BMW's REST API: a valid token, a quota slot, then the request.

Every CarData request needs an access token and spends one of the account's 50
daily requests. Both steps used to be repeated by each caller, each a little
differently: most renewed the tokens before every request, and a renewal hands
the stream a new ID token, which costs it a reconnect (about ten seconds of
nothing heard, since BMW refuses a reconnect sooner). So a manual fetch, or a
restart's catch-up of two cars, dropped the stream once per request.

:class:`TokenKeeper` renews only when the access token is about to lapse, one
renewal at a time; the token-refresh loop (every 45 minutes) keeps it fresh,
so a request normally renews nothing. :class:`RestGateway` claims the quota
after the token is in hand, so a failed renewal spends nothing.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeVar

from .api import CardataApiError
from .device_flow import CardataAuthError

T = TypeVar("T")

# Renew this long before the access token lapses, so a request that waits on a
# slow network still carries a token BMW accepts.
TOKEN_MARGIN_S = 5 * 60


class CardataQuotaError(Exception):
    """Raised when a request would exceed the daily quota."""


class TokenKeeper:
    """Hands out the access token, renewing it only when it is about to lapse.

    ``renew`` renews and stores the tokens (and gives the stream its new ID
    token); ``stored`` returns the stored token fields: ``access_token``,
    ``received_at`` (epoch seconds) and ``expires_in`` (seconds).
    """

    def __init__(
        self,
        renew: Callable[[], Awaitable[None]],
        stored: Callable[[], Mapping[str, Any]],
        *,
        clock: Callable[[], float] = time.time,
        margin_s: float = TOKEN_MARGIN_S,
    ) -> None:
        self._renew = renew
        self._stored = stored
        self._clock = clock
        self._margin_s = margin_s
        self._lock = asyncio.Lock()

    def _usable(self) -> Optional[str]:
        data = self._stored()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            return None
        try:
            expires_at = float(data["received_at"]) + float(data["expires_in"])
        except KeyError, TypeError, ValueError:
            # Of unknown age: renew rather than send one BMW may refuse.
            return None
        return token if self._clock() < expires_at - self._margin_s else None

    async def access_token(self) -> str:
        """A token valid for at least the margin, renewing it if need be."""

        token = self._usable()
        if token is not None:
            return token
        async with self._lock:
            # A request waiting here behind another's renewal uses its result.
            token = self._usable()
            if token is not None:
                return token
            await self._renew()
            token = self._stored().get("access_token")
        if not isinstance(token, str) or not token:
            raise CardataAuthError("No access token after renewing the tokens")
        return token

    async def renew(self) -> None:
        """Renew now, whatever the token's age (the refresh loop, a refused stream)."""

        async with self._lock:
            await self._renew()


def _iso(at: Optional[float]) -> Optional[str]:
    return None if at is None else datetime.fromtimestamp(at, timezone.utc).isoformat()


class RestOutcomes:
    """How BMW answered this run's REST requests, for the diagnostics (#296).

    A refused request is otherwise only a log line, so a diagnostics file could
    not say whether REST works at all: the stream can run for days while every
    fetch is refused. Counts, the last success and the last failure: the
    request's name, the HTTP status and BMW's ``CU-`` code, never its path (it
    holds the VIN) or its body.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.succeeded = 0
        self.failed = 0
        self.last_success_at: Optional[float] = None
        self.last_failure: Optional[dict[str, Any]] = None

    def success(self) -> None:
        self.succeeded += 1
        self.last_success_at = self._clock()

    def failure(self, what: str, err: CardataApiError) -> None:
        self.failed += 1
        self.last_failure = {
            "at": self._clock(),
            "request": what,
            # No status: BMW was never reached (a timeout, no network).
            "status": err.status,
            "error_id": err.error_id,
        }

    def as_dict(self) -> dict[str, Any]:
        failure = self.last_failure
        return {
            "succeeded": self.succeeded,
            "failed": self.failed,
            "last_success_at": _iso(self.last_success_at),
            "last_failure": None if failure is None else {**failure, "at": _iso(failure["at"])},
        }


def _request_name(request: Callable[..., Any]) -> str:
    return getattr(request, "__name__", "request").removeprefix("async_")


class RestGateway:
    """Runs a CarData request with a valid token and a claimed quota slot.

    ``claim`` is the quota manager's claim (it raises :class:`CardataQuotaError`
    when the day's requests are spent); ``None`` claims nothing. Every request
    BMW answers, or fails to, is counted in :attr:`outcomes`.
    """

    def __init__(
        self,
        session: Any,
        tokens: TokenKeeper,
        claim: Optional[Callable[[], Awaitable[None]]] = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session = session
        self.tokens = tokens
        self._claim = claim
        self.outcomes = RestOutcomes(clock)

    async def claim(self) -> None:
        """Spend one request of the day's quota."""

        if self._claim is not None:
            await self._claim()

    async def call(
        self, request: Callable[..., Awaitable[T]], *args: Any, what: Optional[str] = None
    ) -> T:
        """``request(session, access_token, *args)``, once the token and a slot are in hand.

        Raises ``CardataAuthError`` when no token can be had (nothing is
        claimed then), :class:`CardataQuotaError` when the quota is spent, and
        whatever the request raises (``CardataApiError``). ``what`` names the
        request in :attr:`outcomes`; it defaults to the request function's name.
        """

        token = await self.tokens.access_token()
        await self.claim()
        return await self.send(request, token, *args, what=what)

    async def send(
        self,
        request: Callable[..., Awaitable[T]],
        access_token: str,
        *args: Any,
        what: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """``request(session, access_token, *args, **kwargs)`` with token and slot already in hand.

        For a caller that claimed the slot itself (the fetch services); the
        outcome is counted all the same.
        """

        try:
            result = await request(self.session, access_token, *args, **kwargs)
        except CardataApiError as err:
            self.outcomes.failure(what or _request_name(request), err)
            raise
        self.outcomes.success()
        return result
