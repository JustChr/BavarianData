"""``tools/stable_changelog.py`` drafts a stable entry the way ``[0.9.13]`` reads.

The real changelog is the fixture: cut it back to the moment before 0.9.13 was
released (an empty ``[Unreleased]`` over its six betas) and check the draft.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "stable_changelog", _ROOT / "tools" / "stable_changelog.py"
)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

CHANGELOG = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _before_0_9_13() -> str:
    """The changelog as it stood just before ``release.sh --stable`` for 0.9.13."""

    head = CHANGELOG[: CHANGELOG.index("## [Unreleased]")]
    betas = CHANGELOG[CHANGELOG.index("## [0.9.13-beta.6]") :]
    return f"{head}## [Unreleased]\n\n{betas}"


def test_it_promotes_every_beta_since_the_last_stable() -> None:
    version, body, notes = tool.draft(_before_0_9_13())
    assert version == "0.9.13"
    assert body.startswith("Promotes 0.9.13-beta.1 to beta.6.")
    assert "Last stable: 0.9.12" in notes[0]
    assert "Unreleased" not in notes[0], "an empty [Unreleased] was counted as content"


def test_bullets_are_tagged_like_the_real_stable_entry() -> None:
    _, body, _ = tool.draft(_before_0_9_13())
    # The shape the hand-written [0.9.13] entry uses.
    assert "- **Device triggers for automations** (beta.6). Create an automation" in body
    for beta in range(1, 7):
        assert f"(beta.{beta})" in body


def test_headings_come_in_the_house_order() -> None:
    _, body, _ = tool.draft(_before_0_9_13())
    headings = re.findall(r"^### (.+)$", body, flags=re.M)
    known = [h for h in headings if h in tool.ORDER]
    assert known == sorted(known, key=tool.ORDER.index)


def test_every_fixed_bullet_is_listed_for_a_judgment_call() -> None:
    _, body, notes = tool.draft(_before_0_9_13())
    fixed_in_body = body.split("### Fixed", 1)[1].split("\n### ", 1)[0].count("\n- ")
    listed = [n for n in notes if re.match(r"\s+beta\.\d+: ", n)]
    assert fixed_in_body and len(listed) == fixed_in_body


def test_write_replaces_only_the_unreleased_body() -> None:
    text = _before_0_9_13()
    _, body, _ = tool.draft(text)
    written = tool.write(text, body)
    released = text[text.index("## [0.9.13-beta.6]") :]
    assert written.endswith(released), "an already-released section was touched"
    between = written[written.index("## [Unreleased]") : written.index("## [0.9.13-beta.6]")]
    assert "Promotes 0.9.13-beta.1 to beta.6." in between


def test_nothing_to_promote_is_refused() -> None:
    text = CHANGELOG[: CHANGELOG.index("## [Unreleased]")] + (
        "## [Unreleased]\n\n" + CHANGELOG[CHANGELOG.index("## [0.9.13] ") :]
    )
    with pytest.raises(SystemExit):
        tool.draft(text)


def test_an_unreleased_bullet_carries_no_beta_tag() -> None:
    assert tool.tag("- **New thing.** More.", None) == "- **New thing.** More."
    assert tool.tag("- **New thing.** More.", "beta.2") == "- **New thing** (beta.2). More."
