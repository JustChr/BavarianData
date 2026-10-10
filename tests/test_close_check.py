"""The close check's memory and changelog rules (``scripts/close_check.py``).

Only the parts that judge files are pinned here; the git, wiki and gh calls are
plumbing around them, run by the ``close`` skill against the real repo.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "close_check.py"
_spec = importlib.util.spec_from_file_location("close_check", _PATH)
cc = importlib.util.module_from_spec(_spec)
sys.modules["close_check"] = cc  # dataclasses look their module up
_spec.loader.exec_module(cc)


def _memory(tmp_path, files: dict[str, str], index: str | None = None) -> pathlib.Path:
    mem = tmp_path / "memory"
    mem.mkdir()
    for name, body in files.items():
        (mem / name).write_text(body, encoding="utf-8")
    if index is None:
        index = "\n".join(f"- [{n}]({n}) — hook" for n in files)
    (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    return mem


def _note(body: str = "", name: str = "x") -> str:
    return f"---\nname: {name}\ndescription: d\nmetadata:\n  type: project\n---\n{body}\n"


def _issues(mem) -> list[str]:
    rep = cc.Report()
    cc.check_memory(rep, mem)
    return rep.issues


def test_a_tidy_memory_is_clean(tmp_path):
    mem = _memory(tmp_path, {"a.md": _note("See `scripts/close_check.py`.")})
    assert _issues(mem) == []


def test_index_and_files_must_agree(tmp_path):
    mem = _memory(tmp_path, {"a.md": _note(), "b.md": _note()}, index="- [A](a.md)\n- [C](c.md)\n")
    issues = _issues(mem)
    assert any("c.md, which does not exist" in i for i in issues)
    assert any("b.md is not in MEMORY.md" in i for i in issues)


def test_frontmatter_is_required(tmp_path):
    mem = _memory(tmp_path, {"a.md": "no frontmatter\n"})
    assert any("frontmatter" in i for i in _issues(mem))


def test_budgets(tmp_path):
    long = _note("\n".join(["line"] * (cc.STATUS_MAX_LINES + 1)))
    mem = _memory(tmp_path, {cc.STATUS_FILE: long})
    assert any("consolidate" in i for i in _issues(mem))


def test_a_repo_path_that_is_gone_is_named(tmp_path):
    mem = _memory(tmp_path, {"a.md": _note("Fixed in `history/no_such_module.py`.")})
    assert any("no_such_module.py" in i for i in _issues(mem))


def test_a_line_past_the_end_is_named(tmp_path):
    mem = _memory(tmp_path, {"a.md": _note("See `scripts/close_check.py:99999`.")})
    assert any("past the end" in i for i in _issues(mem))


def test_paths_outside_this_repo_and_parked_files_are_not_checked(tmp_path):
    body = (
        "HA core runs `homeassistant/runner.py`.\n"
        "The prototype lives in a git stash:\n"
        "`history/forecast.py` and `tools/backtest_forecast.py`.\n"
    )
    mem = _memory(tmp_path, {"a.md": _note(body)})
    assert _issues(mem) == []


def test_unreleased_section():
    assert cc.unreleased_is_empty("## [Unreleased]\n\n## [1.0.0]\n- x\n")
    assert not cc.unreleased_is_empty("## [Unreleased]\n### Fixed\n- y\n\n## [1.0.0]\n")


def test_classify_sorts_changed_paths_into_areas():
    touched = cc.classify(
        ["custom_components/bavariandata/sensor.py", "docs/wiki/Home.md", "site/x.ts", "README.md"]
    )
    assert set(touched) == {"code", "wiki", "site", "docs"}
