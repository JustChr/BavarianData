"""The token-refresh loop must survive a failed refresh and retry in time.

``_refresh_loop`` is what renews the ID token the MQTT stream authenticates
with, and -- through ``async_update_credentials`` -- what restarts a stream that
is down. Home Assistant runs it as a config-entry background task, and nothing
reports an exception that escapes such a task: the loop is simply gone until
Home Assistant restarts.

Seen in the field: during a DNS outage the scheduled refresh raised an
``aiohttp`` connection error, which the loop did not catch. It ended without a
log line, and the streams of both BMW accounts stayed down for nine hours.

``__init__.py`` imports Home Assistant, so -- like the rest of this suite -- the
test compiles the real function out of the shipped source. It runs it with a
scripted ``_refresh_tokens`` and an ``asyncio`` whose ``sleep`` records how long
the loop wanted to wait instead of waiting.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import logging
import pathlib
import types

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


class _Clock:
    """Stands in for ``asyncio.sleep``: records each wait, then ends the run."""

    def __init__(self, stop_after: int) -> None:
        self.waits: list[float] = []
        self._stop_after = stop_after

    async def sleep(self, delay, result=None):
        self.waits.append(delay)
        if len(self.waits) > self._stop_after:
            # What unloading the config entry does to the task.
            raise asyncio.CancelledError
        return result


def _run_refresh_loop(outcomes: list[Exception | None]) -> tuple[list[float], int]:
    """Run the real loop for one refresh per outcome (``None`` = success).

    Returns every wait the loop asked for and how many refreshes it attempted.
    """

    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_refresh_loop"
    ]
    assert len(wanted) == 1, "_refresh_loop moved"
    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )

    clock = _Clock(stop_after=len(outcomes))
    recording_asyncio = types.ModuleType("asyncio")
    recording_asyncio.__dict__.update(vars(asyncio))
    recording_asyncio.sleep = clock.sleep

    script = list(outcomes)
    refreshes = 0

    async def _refresh_tokens(entry, session, manager, container_manager=None) -> None:
        nonlocal refreshes
        refreshes += 1
        outcome = script.pop(0)
        if outcome is not None:
            raise outcome

    namespace: dict = {
        name: value for name, value in vars(_CONST).items() if name.isupper()
    }
    namespace.update(
        {
            "asyncio": recording_asyncio,
            "aiohttp": aiohttp,
            "_LOGGER": logging.getLogger(__name__),
            "CardataAuthError": _DEVICE_FLOW.CardataAuthError,
            "_refresh_tokens": _refresh_tokens,
        }
    )
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    asyncio.run(namespace["_refresh_loop"](None, None, None, None, None))
    return clock.waits, refreshes


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


def test_a_lasting_outage_backs_off_to_the_regular_interval():
    waits, refreshes = _run_refresh_loop([_dns_outage() for _ in range(12)])
    regular, retries = waits[0], waits[1:]

    assert refreshes == 12
    assert retries == sorted(retries)
    assert retries[1] > retries[0]
    assert retries[-1] == regular
