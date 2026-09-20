"""Every user-facing surface is documented, in both languages, and in the matrix.

``test_wiki_links.py`` already proves the manual is internally consistent: links
land, anchors exist, and every English page has a German counterpart. What it
cannot see is whether the manual still describes *the code* -- a service added
to ``services.yaml`` breaks no link by going undocumented, and a card view
dropped from the card breaks none by staying documented.

This closes that side. CLAUDE.md's rule is that a feature is not done until its
docs ship in the same change, and ``docs/documentation-plan.md`` calls its
coverage matrix "the definition of documented everything" -- so both are checked
here rather than left to whoever remembers.

Deliberately not covered:

* **Option keys.** There is no machine-readable source for them: they are
  string literals inside the options flow, not constants, so any check would be
  a regex that quietly matches nothing. A test that cannot fail is worse than
  no test.
* **Events.** Same reason -- they are built as ``f"{DOMAIN}_..."`` rather than
  written out, so the names do not exist as literals to compare against.

Both become checkable the day they gain a constant; until then this file says
so out loud instead of pretending.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "custom_components" / "bavariandata"
_WIKI = _ROOT / "docs" / "wiki"

PLAN = (_ROOT / "docs" / "documentation-plan.md").read_text(encoding="utf-8")


def _page(name: str) -> str:
    return (_WIKI / name).read_text(encoding="utf-8")


def _de_page(name: str) -> str:
    return (_WIKI / "de" / f"DE-{name}").read_text(encoding="utf-8")


with io.open(_PKG / "services.yaml", encoding="utf-8") as handle:
    SERVICES = sorted(yaml.safe_load(handle) or {})

# The card's own list of addressable views, read from its constants so a new
# view cannot be added without this test noticing.
_VIEW_CONST = re.compile(r'^const (?:[A-Z_]+_VIEW|OVERVIEW) = "([a-z]+)";', re.MULTILINE)
CARD_VIEWS = sorted(
    set(_VIEW_CONST.findall((_PKG / "www" / "bavariandata-card.js").read_text(encoding="utf-8")))
)


def test_the_extraction_itself_found_something():
    """Guards the guards: a broken regex here would silently pass everything."""

    assert len(SERVICES) >= 15, SERVICES
    assert len(CARD_VIEWS) >= 5, CARD_VIEWS


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_is_in_the_english_reference(service: str) -> None:
    assert service in _page("Services-Reference.md"), (
        f"{service} ships in services.yaml but Services-Reference.md never mentions it"
    )


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_is_in_the_german_reference(service: str) -> None:
    assert service in _de_page("Services-Reference.md"), f"{service} is documented in English only"


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_is_in_the_coverage_matrix(service: str) -> None:
    assert service in PLAN, (
        f"{service} is missing from the matrix in docs/documentation-plan.md, "
        "which that file calls the definition of 'documented everything'"
    )


def test_no_service_is_documented_that_no_longer_exists() -> None:
    """A removed service leaves a row behind, and nothing else would catch it."""

    documented = set(re.findall(r"`bavariandata\.([a-z_]+)`", _page("Services-Reference.md")))
    stale = sorted(documented - set(SERVICES))
    assert not stale, f"Services-Reference.md documents services that are gone: {stale}"


# --------------------------------------------------------------------------
# Card views
# --------------------------------------------------------------------------


@pytest.mark.parametrize("view", CARD_VIEWS)
def test_every_card_view_is_documented_in_both_languages(view: str) -> None:
    for label, text in (
        ("The-Dashboard-Card.md", _page("The-Dashboard-Card.md")),
        ("de/DE-The-Dashboard-Card.md", _de_page("The-Dashboard-Card.md")),
    ):
        assert view in text, f"card view '{view}' is not described in {label}"


@pytest.mark.parametrize("view", CARD_VIEWS)
def test_every_card_view_is_in_the_coverage_matrix(view: str) -> None:
    assert view in PLAN, f"card view '{view}' is missing from the coverage matrix"
