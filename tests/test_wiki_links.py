"""Every link inside the wiki lands on a page and a section that exist.

The manual is plain Markdown published to a GitHub wiki, so nothing renders it
before users do: a renamed heading silently breaks every ``Page#section`` link to
it, and the German pages multiply the number of such links. This checks them the
way GitHub resolves them -- page names are the file stems, anchors are heading
slugs or explicit ``<a id>`` tags -- and that the two languages stay paired.

German pages carry the *English* section ids as explicit anchors, so a link
reads the same in both languages and German headings (umlauts and all) can be
reworded without breaking anything.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_WIKI = pathlib.Path(__file__).resolve().parents[1] / "docs" / "wiki"
_CHROME = {"README.md", "_Sidebar.md", "_Footer.md"}

_LINK = re.compile(r"\]\(([^)\s]+)\)")
_ANCHOR_TAG = re.compile(r'<a id="([^"]+)"')
_FENCE = re.compile(r"^\s*```")


def _pages() -> dict[str, pathlib.Path]:
    files = [*_WIKI.glob("*.md"), *(_WIKI / "de").glob("*.md")]
    return {f.stem: f for f in files if f.name != "README.md"}


def _prose(path: pathlib.Path) -> list[str]:
    """The file's lines outside fenced code blocks."""

    lines, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lower-cased, punctuation dropped, spaces to hyphens."""

    text = heading.strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: pathlib.Path) -> set[str]:
    found = set()
    for line in _prose(path):
        if line.startswith("#"):
            found.add(_slug(line.lstrip("#")))
        found.update(_ANCHOR_TAG.findall(line))
    return found


PAGES = _pages()


@pytest.mark.parametrize("name", sorted(PAGES))
def test_internal_links_resolve(name: str) -> None:
    broken = []
    for line in _prose(PAGES[name]):
        for target in _LINK.findall(line):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            page, _, anchor = target.partition("#")
            if "/" in page or "." in page:
                continue  # a repository file, not a wiki page
            page = page or name
            if page not in PAGES:
                broken.append(f"{target} (no page {page})")
            elif anchor and anchor not in _anchors(PAGES[page]):
                broken.append(f"{target} (no section #{anchor} on {page})")
    assert not broken, f"{name}: {broken}"


def test_every_page_has_a_german_counterpart_and_back() -> None:
    english = {stem for stem, path in PAGES.items() if path.parent == _WIKI and path.name not in _CHROME}
    german = {stem for stem, path in PAGES.items() if path.parent.name == "de"}
    assert {f"DE-{stem}" for stem in english} == german
    for stem in english:
        assert f"](DE-{stem})" in PAGES[stem].read_text(encoding="utf-8"), stem
        assert f"]({stem})" in PAGES[f"DE-{stem}"].read_text(encoding="utf-8"), stem


def test_the_sidebar_lists_every_page_in_both_languages() -> None:
    sidebar = PAGES["_Sidebar"].read_text(encoding="utf-8")
    for stem, path in PAGES.items():
        if path.name in _CHROME:
            continue
        assert f"]({stem})" in sidebar, stem
