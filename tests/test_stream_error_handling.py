"""A stream waiting for credentials must neither stall nor start reauth on a network error.

When BMW's broker refuses the MQTT login (typically because the ID token expired
during an outage that outlasted it), the stream stops and waits for new
credentials, and ``_handle_stream_error`` refreshes the token. If that refresh
fails because the network is still unreliable, the credentials are not wrong:
starting a reauth flow would ask the user to log in again for nothing, and giving
up would leave the stream waiting for the next regular refresh, up to 45 minutes
later. It has to ask the refresh loop to try again soon instead.

``__init__.py`` imports Home Assistant, so the test compiles the real handler out
of the shipped source and runs it against stand-ins for the notification and
config-flow APIs it calls.
"""

from __future__ import annotations

import __future__
import ast
import asyncio
import logging
import pathlib
import time
import types
from contextlib import suppress

import aiohttp

from .conftest import load_module

_INIT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "bavariandata"
    / "__init__.py"
)
_CONST = load_module("const")
_DEVICE_FLOW = load_module("device_flow")


class _Notifications:
    """Stand-in for ``homeassistant.components.persistent_notification``."""

    def __init__(self) -> None:
        self.created: list[str] = []

    def async_create(self, hass, message, title=None, notification_id=None) -> None:
        self.created.append(notification_id)

    def async_dismiss(self, hass, notification_id) -> None:
        pass


class _Flows:
    """Stand-in for ``hass.config_entries.flow``."""

    def __init__(self) -> None:
        self.started: list[dict] = []

    async def async_init(self, domain, *, context=None, data=None) -> dict:
        self.started.append(context)
        return {"flow_id": f"flow-{len(self.started)}"}

    async def async_abort(self, flow_id) -> None:
        pass


def _compile_handle_stream_error(refresh_error: Exception, notifications: _Notifications):
    tree = ast.parse(_INIT.read_text(encoding="utf-8"), filename=str(_INIT))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_stream_error"
    ]
    assert len(wanted) == 1, "_handle_stream_error moved"
    code = compile(
        ast.Module(body=wanted, type_ignores=[]),
        str(_INIT),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )

    async def _refresh_tokens(entry, session, manager, container_manager=None) -> None:
        raise refresh_error

    namespace: dict = {
        "DOMAIN": _CONST.DOMAIN,
        "SOURCE_REAUTH": "reauth",
        "time": time,
        "suppress": suppress,
        "_LOGGER": logging.getLogger(__name__),
        "CardataAuthError": _DEVICE_FLOW.CardataAuthError,
        "persistent_notification": notifications,
        "_refresh_tokens": _refresh_tokens,
    }
    exec(code, namespace)  # noqa: S102 - trusted, shipped source
    return namespace["_handle_stream_error"]


def _scenario(refresh_error: Exception):
    notifications = _Notifications()
    flows = _Flows()
    handle_stream_error = _compile_handle_stream_error(refresh_error, notifications)
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(flow=flows))
    runtime = types.SimpleNamespace(
        reauth_in_progress=False,
        reauth_flow_id=None,
        last_reauth_attempt=0.0,
        last_refresh_attempt=0.0,
        reauth_pending=False,
        session=None,
        stream=None,
        container_manager=None,
        refresh_wake=asyncio.Event(),
    )
    entry = types.SimpleNamespace(entry_id="entry-id", data={}, runtime_data=runtime)
    asyncio.run(handle_stream_error(hass, entry, "unauthorized"))
    return notifications, flows, runtime


def test_a_network_error_during_the_refresh_asks_for_a_retry_instead_of_reauth():
    notifications, flows, runtime = _scenario(
        aiohttp.ClientConnectionError("Cannot connect to host customer.bmwgroup.com:443")
    )

    assert flows.started == []
    assert notifications.created == []
    assert runtime.refresh_wake.is_set()


def test_a_refresh_bmw_rejects_still_starts_reauth():
    notifications, flows, runtime = _scenario(
        _DEVICE_FLOW.CardataAuthError("Token refresh failed (invalid_grant)")
    )

    assert len(flows.started) == 1
    assert len(notifications.created) == 1
    assert not runtime.refresh_wake.is_set()
