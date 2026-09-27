"""``scripts/prove_fix.py`` says PROVEN only when it should, and always restores.

Each case builds a throwaway git repository: a buggy module at the base commit,
the fix and its test in the working tree. The script runs pytest inside it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "prove_fix.py"
_spec = importlib.util.spec_from_file_location("prove_fix", _SCRIPT)
prove_fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prove_fix)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "mod.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")  # puts the root on sys.path
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _write_test(repo: pathlib.Path, body: str) -> str:
    (repo / "tests" / "test_mod.py").write_text(body, encoding="utf-8")
    return "tests/test_mod.py"


def test_a_real_guard_is_proven_and_the_fix_is_restored(repo: pathlib.Path) -> None:
    fixed = "def answer():\n    return 42\n"
    (repo / "pkg" / "mod.py").write_text(fixed, encoding="utf-8")
    test = _write_test(
        repo, "from pkg.mod import answer\n\ndef test_it():\n    assert answer() == 42\n"
    )

    assert prove_fix.main([test, "--repo", str(repo)]) == 0
    assert (repo / "pkg" / "mod.py").read_text(encoding="utf-8") == fixed


def test_a_test_that_passes_either_way_is_not_proven(repo: pathlib.Path) -> None:
    (repo / "pkg" / "mod.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    test = _write_test(repo, "def test_it():\n    assert True\n")

    assert prove_fix.main([test, "--repo", str(repo)]) == 1
    assert "42" in (repo / "pkg" / "mod.py").read_text(encoding="utf-8")


def test_a_fix_in_a_new_file_is_removed_for_the_run_and_put_back(repo: pathlib.Path) -> None:
    new = repo / "pkg" / "guard.py"
    new.write_text("LIMIT = 50\n", encoding="utf-8")
    test = _write_test(
        repo, "from pkg.guard import LIMIT\n\ndef test_it():\n    assert LIMIT == 50\n"
    )

    assert prove_fix.main([test, "--repo", str(repo)]) == 0
    assert new.read_text(encoding="utf-8") == "LIMIT = 50\n"


def test_nothing_to_revert_is_a_usage_error(repo: pathlib.Path) -> None:
    test = _write_test(repo, "def test_it():\n    assert True\n")
    assert prove_fix.main([test, "--repo", str(repo)]) == 2
