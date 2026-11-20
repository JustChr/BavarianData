"""The rc=5 repair says which of its two causes it is.

BMW refuses a second stream client with the same rc=5 as a bad login, and keeps
the account's one stream for whichever client holds it (measured live on
2026-11-20: a second client was refused at once, the connected one untouched).
When a renewed token is refused as well, the login works, so the repair names
the other program instead of asking for new tokens that cannot help.
"""

from __future__ import annotations

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

stream_health = load_module("stream_health")
AFTER_MIN = stream_health.UNAUTHORIZED_REPAIR_AFTER_S / 60


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def _repair(h: CoordinatorHarness):
    return h.hass.issues.get(f"stream_unauthorized_{h.coordinator.entry_id}")


def test_a_stream_held_elsewhere_gets_a_repair_that_says_so(h):
    h.advance_to(10)
    h.connection("unauthorized", reason="MQTT rc=5")
    h.connection("unauthorized", reason=stream_health.REFUSED_RENEWED)
    h.advance_to(10 + AFTER_MIN + 1)

    repair = _repair(h)
    assert repair is not None
    assert repair["translation_key"] == "stream_held_elsewhere"
    assert repair["learn_more_url"].endswith("#only-one-stream-per-account")

    h.connection("connected")
    assert _repair(h) is None


def test_a_refusal_no_renewed_token_followed_keeps_the_login_repair(h):
    h.advance_to(10)
    h.connection("unauthorized", reason="MQTT rc=5")
    h.advance_to(10 + AFTER_MIN + 1)

    repair = _repair(h)
    assert repair is not None
    assert repair["translation_key"] == "stream_unauthorized"
    assert repair["learn_more_url"].endswith("#stream-authorization-failing")


def test_the_login_repair_turns_into_the_held_one_when_the_evidence_comes(h):
    h.advance_to(10)
    h.connection("unauthorized", reason="MQTT rc=5")
    h.advance_to(10 + AFTER_MIN + 1)
    assert _repair(h)["translation_key"] == "stream_unauthorized"

    h.connection("unauthorized", reason=stream_health.REFUSED_RENEWED)
    assert _repair(h)["translation_key"] == "stream_held_elsewhere"
