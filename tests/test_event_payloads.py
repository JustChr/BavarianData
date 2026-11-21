"""Every event carries what ``event_payloads.py`` declares, and the wiki says so.

The events are a public contract: automations, Node-RED and the blueprints
read their fields by name. Three things are held to the declared types here:

- every event the coordinator fires: the harness checks each one after every
  step of every scenario, fuzzer and replay (``harness.event_shape_error``),
  and this proves that check catches a stray field;
- every event name in ``const.py``, which must have a declared type;
- the Events table of Feature-Automations, in English and German: every
  declared field is named on the page, and every field a row lists is declared.

The harness is imported inside the test, not at the top: the refactoring gate
runs every module that imports it at the top against the base's code too, and
the base may predate these types.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from .conftest import load_module

payloads = load_module("event_payloads")
const = load_module("const")
triggers = load_module("vehicle_triggers")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PAGES = {
    "en": _ROOT / "docs" / "wiki" / "Feature-Automations.md",
    "de": _ROOT / "docs" / "wiki" / "de" / "DE-Feature-Automations.md",
}
# Named in the Events section's intro, for every event.
_EVERY_EVENT = {"vin", "entry_id", "vehicle"}
_SITUATIONS = {
    triggers.SITUATION_PARKED_UNLOCKED,
    triggers.SITUATION_PLUGGED_NOT_CHARGING,
    triggers.SITUATION_PARKED_WINDOW_OPEN,
}


def test_every_event_name_has_a_declared_payload():
    fired = {
        value
        for name, value in vars(const).items()
        if name.startswith("EVENT_") and isinstance(value, str)
    }
    assert fired == set(payloads.EVENT_PAYLOADS)


def test_the_shape_check_names_what_is_wrong():
    good = {
        "vin": "V",
        "entry_id": "E",
        "vehicle": "i5",
        "status": "CHARGINGACTIVE",
        "soc": 50.0,
        "target_soc": 80,
    }
    assert payloads.shape_error(const.EVENT_CHARGING_STARTED, good) is None
    assert payloads.shape_error(const.EVENT_CHARGING_STARTED, {**good, "odd": 1}) == (
        "bavariandata_charging_started: missing [], undeclared ['odd']"
    )
    without = {key: value for key, value in good.items() if key != "soc"}
    assert "missing ['soc']" in payloads.shape_error(const.EVENT_CHARGING_STARTED, without)
    assert "not a declared event" in payloads.shape_error("bavariandata_nope", good)
    # The session's figures are optional on a stop; whether it completed is not.
    stop = {**good, "complete": True}
    assert payloads.shape_error(const.EVENT_CHARGING_STOPPED, stop) is None
    assert payloads.shape_error(const.EVENT_CHARGING_STOPPED, good) is not None


def test_the_harness_fails_on_an_event_with_a_stray_field(monkeypatch):
    """Plant the bug the per-step check exists for, and watch it fail."""

    from . import harness
    from .test_coordinator_robustness import _fuzz_day

    assert harness.event_shape_error is not None
    real = harness.coordinator_mod.CardataCoordinator._fire

    def leaky(self, event_type, payload):
        real(self, event_type, {**payload, "latitude_raw": 48.1})

    monkeypatch.setattr(harness.coordinator_mod.CardataCoordinator, "_fire", leaky)
    with pytest.raises(AssertionError, match=r"undeclared \['latitude_raw'\]"):
        _fuzz_day(0).close()


def _rows(text: str) -> dict[str, str]:
    """``{event: its Data cell}`` from the Events table."""

    rows = {}
    for line in text.splitlines():
        match = re.match(r"^\| `(bavariandata_\w+)` \|.*\| (.*) \|$", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


@pytest.mark.parametrize("language", sorted(_PAGES))
def test_the_wiki_names_every_declared_field(language):
    text = _PAGES[language].read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", text))
    rows = _rows(text)
    assert set(rows) == set(payloads.EVENT_PAYLOADS), "the Events table lists every event"
    for event in payloads.EVENT_PAYLOADS:
        required, optional = payloads.fields(event)
        undocumented = sorted((required | optional) - named)
        assert not undocumented, f"{language}: {event} fields not on the page: {undocumented}"


@pytest.mark.parametrize("language", sorted(_PAGES))
def test_every_field_the_wiki_lists_is_declared(language):
    rows = _rows(_PAGES[language].read_text(encoding="utf-8"))
    short_names = {event.removeprefix("bavariandata_") for event in payloads.EVENT_PAYLOADS}
    for event, cell in rows.items():
        required, optional = payloads.fields(event)
        listed = set(re.findall(r"`([a-z_]+)`", cell)) - short_names - _SITUATIONS
        listed -= {"true", "false"}
        stray = sorted(listed - required - optional - _EVERY_EVENT)
        assert not stray, f"{language}: {event} lists fields it does not carry: {stray}"
