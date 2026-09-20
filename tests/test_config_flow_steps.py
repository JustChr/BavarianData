"""Every screen the setup and options flows can show has its text.

``config_flow.py`` imports Home Assistant, so nothing here can run it -- a flow
is only ever exercised on a live instance, by hand. A step id with no entry in
``translations/`` does not fail loudly there either: Home Assistant shows the
raw key and an empty dialog. So this reads the flows with ``ast`` and checks the
shape instead: each ``step_id`` / ``progress_action`` a class passes to Home
Assistant exists in the translation section that class belongs to, in English
and German.

The shared activator mixin shows its screens from both flows, so its steps must
exist in both sections -- which is exactly what manual first-time setup needed
when it moved off the console snippet onto the activator.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"
_FLOW = _PKG / "config_flow.py"

# Which translation sections a class's screens are looked up in.
_SECTIONS = {
    "_StreamActivatorFlow": ("config", "options"),
    "CardataConfigFlow": ("config",),
    "CardataOptionsFlowHandler": ("options",),
}


def _called(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _shown(kind: str) -> dict[str, set[str]]:
    """``{class name: {literal kwarg values}}`` for ``step_id`` or ``progress_action``.

    A progress screen's text lives under ``progress``, keyed by its action, so
    its ``step_id`` needs no ``step`` entry and is not collected.
    """

    tree = ast.parse(_FLOW.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name not in _SECTIONS:
            continue
        values = found.setdefault(cls.name, set())
        for node in ast.walk(cls):
            if not isinstance(node, ast.Call):
                continue
            if kind == "step_id" and _called(node) == "async_show_progress":
                continue
            for keyword in node.keywords:
                if keyword.arg == kind and isinstance(keyword.value, ast.Constant):
                    values.add(keyword.value.value)
    assert set(found) == set(_SECTIONS), "a flow class was renamed; update _SECTIONS"
    return found


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_step_shown_has_translated_text(language: str) -> None:
    translations = json.loads(
        (_PKG / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    missing = []
    for cls, steps in _shown("step_id").items():
        for section in _SECTIONS[cls]:
            known = translations[section]["step"]
            missing += [f"{section}.step.{s} ({cls})" for s in sorted(steps) if s not in known]
    for cls, actions in _shown("progress_action").items():
        for section in _SECTIONS[cls]:
            known = translations[section].get("progress", {})
            missing += [
                f"{section}.progress.{a} ({cls})" for a in sorted(actions) if a not in known
            ]
    assert not missing, f"{language}.json lacks: {missing}"


@pytest.mark.parametrize("language", ["en", "de"])
def test_no_translation_is_left_for_a_step_that_no_longer_exists(language: str) -> None:
    translations = json.loads(
        (_PKG / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    shown = _shown("step_id")
    for section in ("config", "options"):
        owners = [cls for cls, sections in _SECTIONS.items() if section in sections]
        steps = set().union(*(shown[cls] for cls in owners))
        stale = sorted(set(translations[section]["step"]) - steps)
        assert not stale, f"{language}.json {section}.step has no screen for: {stale}"


def test_manual_setup_runs_the_activator_not_a_console_snippet() -> None:
    source = _FLOW.read_text(encoding="utf-8")
    assert "build_portal_snippet" not in source
    assert "cluster_snippet" not in source
