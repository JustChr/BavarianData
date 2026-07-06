"""Tests for the cluster / descriptor / streaming-scope helpers.

Home Assistant-free: they validate that the cluster picker's data derivation is
total, stable and round-trips, so the scope selection can't silently drift from
the catalogue.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _PKG / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # so sibling standalone imports resolve
    spec.loader.exec_module(mod)
    return mod


_META_MOD = _load("descriptor_metadata", "descriptor_metadata.py")
META = _META_MOD.DESCRIPTOR_META
SECTIONS = _META_MOD.SECTIONS
D = _load("descriptors", "descriptors.py")


def test_sections_cover_every_descriptor_section():
    used = {meta["section"] for meta in META.values()}
    assert used <= set(SECTIONS), "descriptor references an unknown section"
    # Every advertised cluster is actually used by at least one descriptor.
    assert set(SECTIONS) == used


def test_default_sections_are_relevant_and_ordered():
    defaults = D.default_sections()
    assert defaults, "expected some relevant clusters"
    # Only clusters that carry an enabled-by-default descriptor qualify.
    relevant = {m["section"] for m in META.values() if m["enabled_default"]}
    assert set(defaults) == relevant
    # Order follows the catalogue's section order.
    assert defaults == [s for s in SECTIONS if s in relevant]


def test_descriptors_for_sections_is_total_and_scoped():
    # Selecting every section (with the diagnostic tail) yields every descriptor.
    all_desc = D.descriptors_for_sections(SECTIONS, include_diagnostic=True)
    assert set(all_desc) == set(META)
    # Default (relevant-only) excludes the diagnostic long tail.
    relevant = D.descriptors_for_sections(SECTIONS)
    assert set(relevant) == {d for d, m in META.items() if m["enabled_default"]}
    assert set(relevant) < set(all_desc)


def test_descriptors_are_partitioned_by_section():
    # A descriptor belongs to exactly one cluster, so per-section sets are disjoint
    # and their union (with diagnostics) is the whole catalogue.
    seen: set[str] = set()
    for slug in SECTIONS:
        got = set(D.descriptors_for_sections([slug], include_diagnostic=True))
        assert not (got & seen), f"{slug} overlaps another section"
        seen |= got
    assert seen == set(META)


def test_build_scope_is_stable_and_round_trips():
    sections = D.default_sections()
    scope = D.build_scope(sections)
    tokens = scope.split(" ")
    # Base scopes come first, in order.
    assert tokens[: len(D.BASE_SCOPES)] == list(D.BASE_SCOPES)
    # Deterministic: same input -> identical string.
    assert scope == D.build_scope(sections)
    # Every granular streaming token maps back to a known descriptor in a chosen
    # cluster (excluding the coarse cardata:streaming:read base scope).
    expected = {
        D.streaming_scope(d) for d in D.descriptors_for_sections(sections)
    }
    stream_tokens = {
        t
        for t in tokens
        if t.startswith(D.STREAMING_SCOPE_PREFIX) and t != "cardata:streaming:read"
    }
    assert stream_tokens == expected


def test_empty_selection_yields_only_base_scopes():
    assert D.build_scope([]) == " ".join(D.BASE_SCOPES)


def test_empty_selection_matches_default_scope():
    # An empty cluster selection must reproduce the coarse DEFAULT_SCOPE exactly,
    # so entries without a selection behave identically to before the picker.
    const = _load("const", "const.py")
    assert D.build_scope([]) == const.DEFAULT_SCOPE


def test_base_scopes_keep_coarse_streaming_scope():
    # BMW's device-code endpoint rejects a streaming request that omits the
    # coarse read scope, so granular selections must keep it.
    assert "cardata:streaming:read" in D.BASE_SCOPES
    assert "cardata:streaming:read" in D.build_scope(["tire"]).split(" ")


def _embedded_wanted(snippet: str) -> list[str]:
    import json as _json

    anchor = "const wanted = new Set("
    idx = snippet.index(anchor) + len(anchor)
    value, _end = _json.JSONDecoder().raw_decode(snippet, idx)
    return value


def test_build_portal_snippet_matches_descriptors_exactly():
    snippet = D.build_portal_snippet(["tire"])
    assert D._IDS_MARKER not in snippet  # placeholder fully substituted
    assert snippet.startswith("(() =>")
    # The portal matches the raw descriptor column, so the embedded set is
    # exactly the selected clusters' descriptors — no fuzzy display names.
    wanted = set(_embedded_wanted(snippet))
    assert wanted == set(D.descriptors_for_sections(["tire"]))


def test_build_portal_snippet_empty_selection_matches_nothing():
    assert _embedded_wanted(D.build_portal_snippet([])) == []
