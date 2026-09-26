"""The token-refresh loop must survive failures, retry in time, and wake on request.

``_refresh_loop`` renews the ID token the MQTT stream authenticates with, and --
through ``async_update_credentials`` -- restarts a stream that is down or waiting
for credentials after BMW refused its login. Home Assistant runs it as a
config-entry background task, and nothing reports an exception that escapes such
a task: the loop is simply gone until Home Assistant restarts.

Seen in the field: during a DNS outage the scheduled refresh raised an
``aiohttp`` connection error, which the loop did not catch. It ended without a
log line, and the streams of both BMW accounts stayed down for nine hours.

``__init__.py`` imports Home Assistant, so -- like the rest of this suite -- the
tests compile the real functions out of the shipped source. The loop runs with a
scripted ``_refresh_tokens`` and a ``_wait_for_refresh`` that records how long
the loop wanted to wait instead of waiting.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import itertools
import logging
import pathlib
from contextlib import suppress

import aiohttp
import pytest

from .conftest import load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
_CONST = load_module("const")
_DEVICE_FLOW = load_module("device_flow")

# BMW CarData: "The ID token is used to access the CarData data stream, and it is
# valid for one hour (3600 seconds)." -- docs/reference/bmw-cardata-api-reference.md
ID_TOKEN_LIFETIME = 3600
FIVE_MINUTES = 5 * 60


def _compile_from_init(name: str, **namespace) -> object:
    """Compile one top-level function out of ``__init__.py`` into ``namespace``."""

    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    assert len(wanted) == 1, f"{name} moved"
    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace[name]


def _loop_namespace(**overrides) -> dict:
    namespace: dict = {name: value for name, value in vars(_CONST).items() if name.isupper()}
    namespace.update(
        {
            "asyncio": asyncio,
            "aiohttp": aiohttp,
            "_LOGGER": logging.getLogger(__name__),
            "CardataAuthError": _DEVICE_FLOW.CardataAuthError,
        }
    )
    namespace.update(overrides)
    return namespace


def _dns_outage() -> Exception:
    return aiohttp.ClientConnectionError(
        "Cannot connect to host customer.bmwgroup.com:443 ssl:default [Try again]"
    )


def _timeout() -> Exception:
    return asyncio.TimeoutError()


def _non_json_response() -> Exception:
    # ``refresh_tokens`` parses the body with ``resp.json(content_type=None)``;
    # an HTML error page from a proxy or captive portal fails right there.
    return ValueError("Expecting value: line 1 column 1 (char 0)")


def _token_endpoint_unavailable() -> Exception:
    return _DEVICE_FLOW.CardataAuthError("Token refresh failed (HTTP 503)")


class _RecordingWait:
    """Stands in for ``_wait_for_refresh``: records each wait, then ends the run."""

    def __init__(self, wake: asyncio.Event, stop_after: int) -> None:
        self.waits: list[float] = []
        self._wake = wake
        self._stop_after = stop_after

    async def __call__(self, wake: asyncio.Event, delay: float) -> None:
        assert wake is self._wake, "the loop must wait on the wake it was given"
        self.waits.append(delay)
        if len(self.waits) > self._stop_after:
            # What unloading the config entry does to the task.
            raise asyncio.CancelledError


def _run_refresh_loop(outcomes: list[Exception | None]) -> tuple[list[float], int]:
    """Run the real loop for one refresh per outcome (``None`` = success).

    Returns every wait the loop asked for and how many refreshes it attempted.
    """

    script = list(outcomes)
    refreshes = 0

    async def _refresh_tokens(entry, session, manager, container_manager=None) -> None:
        nonlocal refreshes
        refreshes += 1
        outcome = script.pop(0)
        if outcome is not None:
            raise outcome

    async def scenario() -> list[float]:
        wake = asyncio.Event()
        wait = _RecordingWait(wake, stop_after=len(outcomes))
        refresh_loop = _compile_from_init(
            "_refresh_loop",
            **_loop_namespace(_refresh_tokens=_refresh_tokens, _wait_for_refresh=wait),
        )
        await refresh_loop(None, None, None, None, None, wake)
        return wait.waits

    waits = asyncio.run(scenario())
    return waits, refreshes


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(_dns_outage, id="dns-outage"),
        pytest.param(_timeout, id="timeout"),
        pytest.param(_non_json_response, id="non-json-response"),
    ],
)
def test_a_failed_refresh_does_not_end_the_loop(failure):
    _waits, refreshes = _run_refresh_loop([failure(), None])

    assert refreshes == 2


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(_dns_outage, id="dns-outage"),
        pytest.param(_token_endpoint_unavailable, id="token-endpoint-503"),
    ],
)
def test_a_failed_refresh_is_retried_before_the_id_token_expires(failure):
    waits, _refreshes = _run_refresh_loop([failure(), None])

    # The token the failed refresh should have replaced was issued one regular
    # wait before it, so the retry has to land within that token's lifetime.
    assert waits[0] + waits[1] < ID_TOKEN_LIFETIME


def test_a_successful_refresh_starts_over_as_if_nothing_failed():
    waits, _refreshes = _run_refresh_loop([_dns_outage(), None, _dns_outage(), None])

    # Back to the regular cadence...
    assert waits[2] == waits[0]
    # ...and a later outage is retried as quickly as the first one was.
    assert waits[3] == waits[1]


def test_a_lasting_outage_keeps_retrying_at_least_every_five_minutes():
    waits, refreshes = _run_refresh_loop([_dns_outage() for _ in range(12)])
    retries = waits[1:]

    assert refreshes == 12
    assert retries == sorted(retries)
    assert retries[1] > retries[0]
    # A new token follows within five minutes of the network coming back.
    assert max(retries) <= FIVE_MINUTES


def test_a_lasting_outage_still_tries_shortly_before_the_token_expires():
    waits, _refreshes = _run_refresh_loop([_dns_outage() for _ in range(8)])
    # The token was issued at 0; refresh attempt k happens after the first k waits.
    attempts_at = list(itertools.accumulate(waits))[:-1]

    assert any(ID_TOKEN_LIFETIME - FIVE_MINUTES <= at < ID_TOKEN_LIFETIME for at in attempts_at)


def test_unloading_during_a_refresh_ends_the_loop():
    refreshes = 0

    async def scenario() -> bool:
        nonlocal refreshes
        in_flight = asyncio.Event()

        async def _refresh_tokens(entry, session, manager, container_manager=None) -> None:
            nonlocal refreshes
            refreshes += 1
            in_flight.set()
            await asyncio.Event().wait()  # the token endpoint never answers

        async def no_wait(wake: asyncio.Event, delay: float) -> None:
            return

        refresh_loop = _compile_from_init(
            "_refresh_loop",
            **_loop_namespace(_refresh_tokens=_refresh_tokens, _wait_for_refresh=no_wait),
        )
        task = asyncio.get_running_loop().create_task(
            refresh_loop(None, None, None, None, None, asyncio.Event())
        )
        await in_flight.wait()
        task.cancel()  # what unloading the config entry does
        done, _pending = await asyncio.wait({task}, timeout=1)
        return task in done

    ended = asyncio.run(scenario())

    assert ended
    assert refreshes == 1


def _wait_for_refresh():
    return _compile_from_init("_wait_for_refresh", asyncio=asyncio, suppress=suppress)


def test_a_refresh_request_cuts_the_wait_short():
    wait_for_refresh = _wait_for_refresh()

    async def scenario() -> tuple[float, bool]:
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        loop.call_later(0.01, wake.set)
        started = loop.time()
        await wait_for_refresh(wake, 5)
        return loop.time() - started, wake.is_set()

    elapsed, still_requested = asyncio.run(scenario())

    assert elapsed < 1
    # The request has been served; the next wait must not end at once.
    assert not still_requested


def test_without_a_request_the_full_delay_is_waited():
    wait_for_refresh = _wait_for_refresh()

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await wait_for_refresh(asyncio.Event(), 0.05)
        return loop.time() - started

    elapsed = asyncio.run(scenario())

    assert 0.05 <= elapsed < 1
