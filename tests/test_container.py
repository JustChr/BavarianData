"""The HV battery container manager.

Coverage reporting found this module at 0%: 130 statements, no Home Assistant
imports, and nothing exercising them. It matters more than its size suggests --
it is on the REST path, and REST is the resource this integration is poorest in
(50 requests per account per 24 h), so a container created twice, or deleted
before its replacement exists, is paid for in the user's daily budget.

The behaviour worth pinning is the ordering in ``async_reset_hv_container``:
the replacement is created *before* the old containers are deleted, so a
rejected creation leaves the working container in place rather than leaving the
entry with none.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import FakeResponse, FakeSession, load_module

container = load_module("container")
const = load_module("const")

CardataContainerManager = container.CardataContainerManager
CardataContainerError = container.CardataContainerError

NAME = const.HV_BATTERY_CONTAINER_NAME
PURPOSE = const.HV_BATTERY_CONTAINER_PURPOSE

TOKEN = "token-abc"


def _manager(responses, *, container_id=None):
    session = FakeSession(responses)
    manager = CardataContainerManager(
        session=session, entry_id="entry1", initial_container_id=container_id
    )
    return manager, session


def _run(coro):
    return asyncio.run(coro)


def _methods(session) -> list[str]:
    return [call["method"] for call in session.calls]


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------


def test_the_signature_ignores_order_and_duplicates():
    sig = CardataContainerManager.compute_signature
    assert sig(["b", "a"]) == sig(["a", "b"])
    assert sig(["a", "a", "b"]) == sig(["a", "b"])
    assert sig(["a"]) != sig(["b"])


# --------------------------------------------------------------------------
# Ensure
# --------------------------------------------------------------------------


def test_a_known_container_is_reused_without_spending_a_request():
    manager, session = _manager([], container_id="existing")
    assert _run(manager.async_ensure_hv_container(TOKEN)) == "existing"
    assert session.calls == [], "a cached container must cost no REST quota"


def test_without_a_token_nothing_is_requested():
    manager, session = _manager([], container_id="existing")
    assert _run(manager.async_ensure_hv_container(None)) == "existing"
    assert session.calls == []


def test_a_missing_container_is_created_and_remembered():
    manager, session = _manager([FakeResponse(201, {"containerId": "new-1"})])
    assert _run(manager.async_ensure_hv_container(TOKEN)) == "new-1"
    assert manager.container_id == "new-1"
    assert _methods(session) == ["POST"]


def test_a_creation_response_without_an_id_is_an_error():
    manager, _ = _manager([FakeResponse(201, {"nothing": "useful"})])
    with pytest.raises(CardataContainerError):
        _run(manager.async_ensure_hv_container(TOKEN))


def test_sync_from_entry_adopts_the_stored_id():
    manager, _ = _manager([])
    manager.sync_from_entry("from-entry")
    assert manager.container_id == "from-entry"


# --------------------------------------------------------------------------
# Reset
# --------------------------------------------------------------------------


def test_the_replacement_is_created_before_anything_is_deleted():
    """Deleting first would leave the entry with no container if BMW says no."""

    manager, session = _manager(
        [
            FakeResponse(200, [{"containerId": "old-1", "purpose": PURPOSE}]),
            FakeResponse(201, {"containerId": "new-1"}),
            FakeResponse(204, None),
        ]
    )
    assert _run(manager.async_reset_hv_container(TOKEN)) == "new-1"
    assert _methods(session) == ["GET", "POST", "DELETE"], (
        "the POST must precede the DELETE"
    )


def test_the_new_container_is_never_deleted():
    manager, session = _manager(
        [
            FakeResponse(200, [{"containerId": "new-1", "purpose": PURPOSE}]),
            FakeResponse(201, {"containerId": "new-1"}),
        ]
    )
    assert _run(manager.async_reset_hv_container(TOKEN)) == "new-1"
    assert "DELETE" not in _methods(session)


def test_containers_belonging_to_someone_else_are_left_alone():
    manager, session = _manager(
        [
            FakeResponse(200, [{"containerId": "other", "purpose": "something-else"}]),
            FakeResponse(201, {"containerId": "new-1"}),
        ]
    )
    _run(manager.async_reset_hv_container(TOKEN))
    assert "DELETE" not in _methods(session)


def test_a_container_that_cannot_be_deleted_does_not_stop_the_rest():
    manager, session = _manager(
        [
            FakeResponse(
                200,
                [
                    {"containerId": "old-1", "purpose": PURPOSE},
                    {"containerId": "old-2", "name": NAME},
                ],
            ),
            FakeResponse(201, {"containerId": "new-1"}),
            FakeResponse(500, "boom"),
            FakeResponse(204, None),
        ]
    )
    assert _run(manager.async_reset_hv_container(TOKEN)) == "new-1"
    assert _methods(session) == ["GET", "POST", "DELETE", "DELETE"]


def test_a_container_already_gone_is_not_an_error():
    manager, session = _manager(
        [
            FakeResponse(200, [{"containerId": "old-1", "purpose": PURPOSE}]),
            FakeResponse(201, {"containerId": "new-1"}),
            FakeResponse(404, "gone"),
        ]
    )
    assert _run(manager.async_reset_hv_container(TOKEN)) == "new-1"


def test_resetting_without_a_token_does_nothing():
    manager, session = _manager([], container_id="existing")
    assert _run(manager.async_reset_hv_container(None)) == "existing"
    assert session.calls == []


# --------------------------------------------------------------------------
# Listing and matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        [{"containerId": "a"}],                      # a bare list
        {"containers": [{"containerId": "a"}]},      # wrapped in an object
        {"containers": "not a list"},                # wrapped, but not a list
        {"containers": [1, 2, "three"]},             # a list of non-objects
        123,                                         # valid JSON, wrong type
        "null",                                      # JSON null
    ],
)
def test_the_listing_survives_every_json_shape_bmw_could_return(body):
    """Wrong *shape* is handled; a body that is not JSON at all is aiohttp's to reject."""

    manager, _ = _manager([FakeResponse(200, body), FakeResponse(201, {"containerId": "n"})])
    assert _run(manager.async_reset_hv_container(TOKEN)) == "n"


def test_a_container_matches_on_purpose_name_or_descriptor_signature():
    manager, _ = _manager([])
    assert manager._matches_hv_container({"purpose": PURPOSE})
    assert manager._matches_hv_container({"name": NAME})
    assert not manager._matches_hv_container({"purpose": "x", "name": "y"})
    assert not manager._matches_hv_container({})


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def test_an_http_error_carries_its_status():
    manager, _ = _manager([FakeResponse(403, "forbidden")])
    with pytest.raises(CardataContainerError) as excinfo:
        _run(manager.async_ensure_hv_container(TOKEN))
    assert excinfo.value.status == 403
    assert "403" in str(excinfo.value)


def test_the_request_is_authorised_and_versioned():
    manager, session = _manager([FakeResponse(201, {"containerId": "n"})])
    _run(manager.async_ensure_hv_container(TOKEN))
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["x-version"] == const.API_VERSION
