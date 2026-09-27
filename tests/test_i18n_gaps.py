"""``tools/i18n_gaps.py`` -- the translation worklist -- stays trustworthy.

The parity tests already fail on a structural gap; this tool's job is to list them
all at once and to find the stale ones no test can see. So check both halves: it
reads every real source cleanly today, and its comparisons flag what they should
on tables built to have exactly one problem each.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil

import pytest

_TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "i18n_gaps.py"
_spec = importlib.util.spec_from_file_location("i18n_gaps", _TOOL)
gaps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gaps)


def test_the_shipped_tree_has_no_structural_gaps() -> None:
    found = gaps.collect(since=None, stale=False)
    assert found == [], gaps.render(found, None)


def test_it_reads_every_source() -> None:
    # A source that silently reads as empty would make "no gaps" meaningless.
    assert "fr" in gaps.ha_tables(None)
    assert "fr" in gaps.report_tables(None)
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    assert "fr" in gaps.card_tables(None)


def test_structural_problems_are_each_named() -> None:
    tables = {
        "en": {"a": "Hello {name}", "b": "Use `x`", "c": "Gone in fr"},
        "fr": {"a": "Bonjour", "b": "Utilisez x", "z": "Orphan"},
    }
    problems = {(g["key"], g["problem"]) for g in gaps.structural_gaps(tables)}
    assert problems == {
        ("a", "placeholders"),
        ("b", "code spans"),
        ("c", "missing"),
        ("z", "extra"),
    }


def test_stale_means_english_moved_and_the_translation_did_not() -> None:
    then = {
        "en": {"a": "Old", "b": "Old", "c": "Same"},
        "fr": {"a": "Vieux", "b": "Vieux", "c": "Pareil"},
    }
    now = {
        "en": {"a": "New", "b": "New", "c": "Same"},
        "fr": {"a": "Vieux", "b": "Nouveau", "c": "Pareil"},
    }
    stale = gaps.stale_gaps(now, then)
    assert [(g["lang"], g["key"]) for g in stale] == [("fr", "a")]
    assert stale[0]["en_before"] == "Old"
