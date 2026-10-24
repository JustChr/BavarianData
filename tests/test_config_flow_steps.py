"""Every screen the setup and options flows can show has its text.

The flows import Home Assistant, so nothing here can run them (``tests_ha/``
does, on Linux). A step id with no entry in ``translations/`` does not fail
loudly there either: Home Assistant shows the raw key and an empty dialog. So
this reads the flow modules with ``ast`` and checks the
shape instead: each ``step_id`` / ``progress_action`` a class passes to Home
Assistant exists in the translation section that class belongs to, in every
full language.

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
# Every module a flow's screens are written in.
_FLOWS = tuple(
    _PKG / name
    for name in ("config_flow.py", "flow_activator.py", "options_flow.py", "options_cars.py")
)
# Every full language (a regional delta like en-GB carries no flow text of its
# own and falls back to en key by key).
_LANGUAGES = sorted(p.stem for p in (_PKG / "translations").glob("*.json") if "-" not in p.stem)

# Which translation sections a class's screens are looked up in.
_SECTIONS = {
    "StreamActivatorFlow": ("config", "options"),
    "CardataConfigFlow": ("config",),
    "CardataOptionsFlowHandler": ("options",),
    "PerCarScreens": ("options",),
}


def _classes() -> list[ast.ClassDef]:
    return [
        node
        for path in _FLOWS
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
    ]


def _called(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _shown(kind: str) -> dict[str, set[str]]:
    """``{class name: {literal kwarg values}}`` for ``step_id`` or ``progress_action``.

    A progress screen's text lives under ``progress``, keyed by its action, so
    its ``step_id`` needs no ``step`` entry and is not collected.
    """

    found: dict[str, set[str]] = {}
    for cls in _classes():
        if cls.name not in _SECTIONS:
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


@pytest.mark.parametrize("language", _LANGUAGES)
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


@pytest.mark.parametrize("language", _LANGUAGES)
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
    source = "".join(path.read_text(encoding="utf-8") for path in _FLOWS)
    assert "build_portal_snippet" not in source
    assert "cluster_snippet" not in source


# Flow hooks Home Assistant calls synchronously. Written as ``async def`` they
# only build a coroutine nobody awaits: the body never runs, and the only trace
# is a RuntimeWarning in the log (#142's log showed one for ``async_remove``).
_SYNC_FLOW_HOOKS = ("async_remove",)


def test_sync_flow_hooks_are_not_coroutines() -> None:
    coroutines = sorted(
        f"{cls.name}.{node.name}"
        for cls in _classes()
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _SYNC_FLOW_HOOKS
    )
    assert not coroutines, f"Home Assistant never awaits these: {coroutines}"
