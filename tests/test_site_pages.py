"""The website renders the wiki; these keep the two from drifting apart.

The site (``site/``) takes every page from ``docs/wiki`` and adds only what the
wiki cannot hold: a clean URL, a search title and a meta description per
language, in ``site/src/data/pages.json``. A wiki page without an entry would
break the site build; an entry without a page would advertise a dead URL.

Descriptor pages are indexable only where ``site/src/data/descriptor-notes.json``
says something BMW's catalogue doesn't; those notes link into the manual and to
each other, and a dead link there would only show up in a site build.

It also carries a scrubbed capture of a real car for the live card demo. This
repository is public, so the capture is checked here too: no VIN but the demo
one, and no place that isn't on the fictional list.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from tests.test_wiki_links import PAGES as _WIKI_FILES, _anchors

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WIKI = _ROOT / "docs" / "wiki"
_PAGES = json.loads((_ROOT / "site" / "src" / "data" / "pages.json").read_text(encoding="utf-8"))
_PAGES.pop("_comment", None)
_NOTES = json.loads(
    (_ROOT / "site" / "src" / "data" / "descriptor-notes.json").read_text(encoding="utf-8")
)
_NOTES.pop("_comment", None)
_CATALOGUE = {
    d["descriptor"]
    for d in json.loads(
        (_ROOT / "custom_components" / "bavariandata" / "catalogue.json").read_text(
            encoding="utf-8"
        )
    )["descriptors"]
}
_NOTE_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_DEMO = _ROOT / "site" / "src" / "demo" / "i5.json"
_CAPTURE = (_ROOT / "site" / "scripts" / "capture_demo.py").read_text(encoding="utf-8")

_WIKI_PAGES = sorted(
    p.stem for p in _WIKI.glob("*.md") if p.name != "README.md" and not p.name.startswith("_")
)


@pytest.mark.parametrize("page", _WIKI_PAGES)
def test_every_wiki_page_has_a_site_entry(page: str) -> None:
    assert page in _PAGES, f"add docs/wiki/{page}.md to site/src/data/pages.json"


def test_every_site_entry_has_both_wiki_pages() -> None:
    for page in _PAGES:
        assert (_WIKI / f"{page}.md").exists(), f"pages.json lists {page}, which is not a wiki page"
        assert (_WIKI / "de" / f"DE-{page}.md").exists(), f"{page} has no German page"


def test_slugs_are_unique_and_url_safe() -> None:
    slugs = [entry["slug"] for entry in _PAGES.values()]
    assert len(slugs) == len(set(slugs))
    for slug in slugs:
        assert re.fullmatch(r"[a-z0-9-]*", slug), slug


@pytest.mark.parametrize("page", sorted(_PAGES))
@pytest.mark.parametrize("lang", ["en", "de"])
def test_each_page_has_a_search_title_and_description(page: str, lang: str) -> None:
    meta = _PAGES[page][lang]
    assert 15 <= len(meta["title"]) <= 95, meta["title"]
    # Google shows roughly 150-160 characters; much shorter wastes the slot.
    assert 90 <= len(meta["description"]) <= 200, (len(meta["description"]), meta["description"])


def test_descriptor_notes_name_real_descriptors_and_pair_their_languages() -> None:
    featured = {k: v for k, v in _NOTES.items() if k.startswith("vehicle.")}
    shared = {k: v for k, v in _NOTES.items() if not k.startswith("vehicle.")}
    assert len(featured) >= 20, "the featured set is what keeps descriptor pages indexable"
    assert set(featured) <= _CATALOGUE, set(featured) - _CATALOGUE
    for key, value in featured.items():
        if isinstance(value, str):
            assert value in shared, f"{key} points at {value}, which is no shared block"
    used = {v for v in featured.values() if isinstance(v, str)}
    assert set(shared) == used, f"unused shared blocks: {set(shared) - used}"
    for key, note in [
        *((k, v) for k, v in featured.items() if isinstance(v, dict)),
        *shared.items(),
    ]:
        assert set(note) == {"en", "de"}, key
        assert note["en"] and len(note["en"]) == len(note["de"]), (
            f"{key}: EN and DE paragraphs differ"
        )


def test_descriptor_note_links_resolve() -> None:
    notes = [v for v in _NOTES.values() if isinstance(v, dict)]
    for note in notes:
        for lang, paragraphs in note.items():
            for paragraph in paragraphs:
                for label, target in _NOTE_LINK.findall(paragraph):
                    if target.startswith("https://"):
                        assert label, target
                    elif target.startswith("data:"):
                        assert target[5:] in _CATALOGUE, target
                    else:
                        page, _, anchor = target.partition("#")
                        assert label, f"a manual link needs its own text: {target}"
                        assert page in _PAGES, f"{target}: no such manual page"
                        if anchor:
                            stem = f"DE-{page}" if lang == "de" else page
                            assert anchor in _anchors(_WIKI_FILES[stem]), f"{stem} has no #{anchor}"


def test_the_demo_capture_carries_only_the_demo_vin() -> None:
    text = _DEMO.read_text(encoding="utf-8")
    vins = set(re.findall(r"\bW[A-Z0-9]{16}\b", text))
    assert vins <= {"WBADEMO0000000001"}, vins


def test_the_demo_capture_names_only_fictional_places() -> None:
    allowed = set(re.findall(r'^\s+\("([^"]+)", (?:"Work"|None), \(', _CAPTURE, re.M)) | {"Home"}
    assert len(allowed) > 5, "the fictional place list in capture_demo.py was not found"
    demo = json.loads(_DEMO.read_text(encoding="utf-8"))
    for trip in demo["services"]["get_trips"]["trips"]:
        for end in ("start_place", "end_place"):
            place = trip[end] or {}
            assert place.get("label") in allowed, place
            assert place.get("address") in allowed | {None}, place
    summary = demo["services"]["get_driving_summary"]["summary"]
    for dest in summary.get("top_destinations", []):
        assert dest["label"] in allowed, dest
    for sess in demo["services"]["get_charging_sessions"]["sessions"]:
        place = sess["location"]
        assert place.get("zone") in {"Home", "Work", None}, place
        address = place.get("address")
        assert address is None or address.removeprefix("HPC · ") in allowed, place
