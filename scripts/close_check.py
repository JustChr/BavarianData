#!/usr/bin/env python3
"""Read-only report of what a session leaves behind, for the ``close`` skill.

Everything here is mechanical and the same every time; the skill
(``.claude/skills/close/SKILL.md``) makes the judgment calls. Nothing is
changed -- not the tree, not the wiki, not memory -- so it is safe to run at
any point, as often as wanted.

    python scripts/close_check.py                 # fetches origin first
    python scripts/close_check.py --no-fetch      # offline
    python scripts/close_check.py --memory DIR    # memory dir, if not found

Sections:

* **git** -- uncommitted or untracked files, commits not on a remote,
  local branches not merged, stashes, extra worktrees, open pull requests;
* **session** -- commits since the ``Last close: <sha>`` marker in memory's
  ``current-status.md``, and which areas they touched (so the skill knows
  whether the gates and which docs need a look);
* **release** -- commits since the last tag, and code shipped there while
  ``## [Unreleased]`` is empty;
* **wiki** -- pages that differ from the published GitHub Wiki, each marked
  *publish now* (docs-only, or already released) or *with the release*;
* **memory** -- index and file budgets, index <-> files, frontmatter, and
  repo paths a memory names that no longer exist.

Exit status: 0 nothing to do; 1 something listed needs attention; 2 error.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI_URL = "https://github.com/JustChr/BavarianData.wiki.git"

# MEMORY.md is loaded into every session, so it is the scarce one; the status
# file is read first every session and must stay a status, not a log.
INDEX_MAX_LINES = 60
INDEX_MAX_BYTES = 6000
STATUS_MAX_LINES = 100
FILE_MAX_LINES = 120
STATUS_FILE = "current-status.md"
MARKER = re.compile(r"Last close:\s*`?([0-9a-f]{7,40})`?")

# What a changed path means for the rest of the close.
AREAS = (
    ("code", ("custom_components/",)),
    ("tests", ("tests/",)),
    ("wiki", ("docs/wiki/",)),
    ("docs", ("docs/", "README.md", "info.md", "CHANGELOG.md", "CLAUDE.md")),
    ("site", ("site/",)),
    ("tooling", (".claude/", "scripts/", "tools/", ".github/")),
)
# Paths whose change means check.sh should run before the session ends.
GATED = ("custom_components/", "tests/", "tools/", "scripts/", "site/scripts/", "pyproject.toml")


@dataclass
class Report:
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def issue(self, text: str) -> None:
        self.issues.append(text)

    def note(self, text: str) -> None:
        self.notes.append(text)


def run(*args: str, cwd: Path = ROOT, check: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip() if result.returncode == 0 else ""


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# --- git -----------------------------------------------------------------------


def check_git(rep: Report) -> None:
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    rep.note(f"branch {branch}")
    for entry in lines(run("git", "status", "--porcelain")):
        rep.issue(f"uncommitted: {entry}")
    if run("git", "rev-parse", "--abbrev-ref", "@{u}"):
        ahead = lines(run("git", "rev-list", "@{u}..HEAD"))
        behind = lines(run("git", "rev-list", "HEAD..@{u}"))
        if ahead:
            rep.issue(f"{branch}: {len(ahead)} commit(s) not pushed")
        if behind:
            rep.issue(f"{branch}: {len(behind)} commit(s) behind its remote (pull)")
    elif branch != "HEAD":
        rep.issue(f"{branch}: no upstream (never pushed)")
    for name in lines(run("git", "branch", "--format=%(refname:short)")):
        if name == "main":
            continue
        unmerged = lines(run("git", "rev-list", f"origin/main..{name}"))
        if not unmerged:
            rep.issue(f"branch {name}: merged into main, delete it")
            continue
        remote = run("git", "rev-parse", "--verify", "--quiet", f"origin/{name}")
        unpushed = lines(run("git", "rev-list", f"origin/{name}..{name}")) if remote else unmerged
        state = f"{len(unpushed)} unpushed" if unpushed else "pushed"
        rep.issue(f"branch {name}: {len(unmerged)} commit(s) not on main, {state}")
    for stash in lines(run("git", "stash", "list")):
        # A stash labelled "drawer" is parked on purpose and has a memory.
        (rep.note if "drawer" in stash.lower() else rep.issue)(f"stash: {stash}")
    worktrees = lines(run("git", "worktree", "list"))
    for extra in worktrees[1:]:
        rep.issue(f"worktree: {extra}")
    if shutil.which("gh"):
        prs = lines(
            run(
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName",
                "--template",
                "{{range .}}#{{.number}} {{.headRefName}}: {{.title}}\n{{end}}",
            )
        )
        for pr in prs:
            rep.issue(f"open PR {pr}")


# --- session -------------------------------------------------------------------


def classify(paths: list[str]) -> dict[str, list[str]]:
    touched: dict[str, list[str]] = {}
    for path in paths:
        for area, prefixes in AREAS:
            if path.startswith(prefixes):
                touched.setdefault(area, []).append(path)
                break
        else:
            touched.setdefault("other", []).append(path)
    return touched


def check_session(rep: Report, memory: Path | None) -> None:
    status = memory / STATUS_FILE if memory else None
    marker = None
    if status and status.exists():
        found = MARKER.search(status.read_text(encoding="utf-8"))
        marker = found.group(1) if found else None
    if marker is None:
        rep.issue(
            f"no 'Last close: <sha>' marker in {STATUS_FILE}; set it at the end of this close"
        )
        return
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{marker}^{{commit}}"], cwd=ROOT, capture_output=True
    )
    if exists.returncode:
        rep.issue(f"close marker {marker} is not a commit here")
        return
    commits = lines(run("git", "log", "--oneline", f"{marker}..HEAD"))
    paths = lines(run("git", "diff", "--name-only", f"{marker}..HEAD"))
    paths += [entry[3:] for entry in lines(run("git", "status", "--porcelain"))]
    rep.note(f"since last close ({marker}): {len(commits)} commit(s)")
    touched = classify(sorted(set(paths)))
    for area, files in sorted(touched.items()):
        rep.note(f"  {area}: {len(files)} file(s)")
    if any(p.startswith(GATED) for p in paths):
        rep.note("  -> code changed: run `bash scripts/check.sh`")
    if "code" in touched and not ({"wiki", "docs"} & touched.keys()):
        rep.issue("code changed this session but no docs did; confirm nothing user-visible moved")


# --- release -------------------------------------------------------------------


def unreleased_is_empty(changelog: str) -> bool:
    match = re.search(r"^## \[Unreleased\]\s*\n(.*?)(?=^## \[)", changelog, re.S | re.M)
    return match is None or not match.group(1).strip()


def check_release(rep: Report) -> None:
    tag = run("git", "describe", "--tags", "--abbrev=0")
    if not tag:
        return
    commits = lines(run("git", "log", "--oneline", f"{tag}..origin/main"))
    code = lines(run("git", "log", "--oneline", f"{tag}..origin/main", "--", "custom_components/"))
    rep.note(
        f"last release {tag}; main is {len(commits)} commit(s) ahead, {len(code)} touching code"
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if code and unreleased_is_empty(changelog):
        rep.issue("code on main since the last release but `## [Unreleased]` is empty")


# --- wiki ----------------------------------------------------------------------


def wiki_pages() -> dict[str, Path]:
    """Published page name -> source file, as ``publish-wiki.sh`` maps them."""

    src = ROOT / "docs" / "wiki"
    pages = {}
    for path in [*src.glob("*.md"), *(src / "de").glob("*.md")]:
        if path.name != "README.md":
            pages[path.name] = path
    return pages


def docs_only(commits: list[str]) -> bool:
    """Whether none of these commits changed what the integration does."""

    for sha in commits:
        changed = lines(run("git", "show", "--name-only", "--format=", sha))
        if any(p.startswith("custom_components/") for p in changed):
            return False
    return True


def check_wiki(rep: Report) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "wiki"
        if subprocess.run(
            ["git", "clone", "--depth", "1", "-q", WIKI_URL, str(clone)], capture_output=True
        ).returncode:
            rep.issue("could not clone the wiki to compare")
            return
        tag = run("git", "describe", "--tags", "--abbrev=0")
        pages = wiki_pages()
        for name, path in sorted(pages.items()):
            published = clone / name
            norm = lambda b: b.replace(b"\r\n", b"\n")  # noqa: E731
            if published.exists() and norm(published.read_bytes()) == norm(path.read_bytes()):
                continue
            rel = path.relative_to(ROOT).as_posix()
            since = (
                lines(run("git", "log", "--format=%h", f"{tag}..HEAD", "--", rel)) if tag else []
            )
            when = "publish now" if docs_only(since) else "with the release"
            rep.issue(f"wiki {name}: differs from published ({when})")
        for orphan in sorted(p.name for p in clone.glob("*.md") if p.name not in pages):
            rep.note(f"wiki {orphan}: published but not in docs/wiki (left alone)")


# --- memory --------------------------------------------------------------------

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
INDEX_LINK = re.compile(r"\]\(([^)]+\.md)\)")
# A repo path a memory names: a backticked token with a slash and an extension.
REPO_PATH = re.compile(
    r"`([\w./-]+/[\w.-]+\.(?:py|js|ts|mjs|json|md|sh|yml|yaml|astro|toml))(?::(\d+))?`"
)


def find_memory(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    mangled = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT)).lower()
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return None
    for candidate in projects.iterdir():
        if candidate.name.lower() == mangled and (candidate / "memory").is_dir():
            return candidate / "memory"
    return None


# Where a path named in memory is looked for, and the words that say a path is
# deliberately not in the tree (a stashed prototype, a planned file).
REPO_ROOTS = (ROOT, ROOT / "custom_components" / "bavariandata", ROOT / "site")
NOT_IN_REPO_WORDS = ("stash", "parked", "planned", "proposed", "would ")


def resolve_repo_path(token: str) -> Path | None:
    """Where a path named in memory lives, trying the usual roots."""

    for base in REPO_ROOTS:
        if (base / token).exists():
            return base / token
    return None


def check_memory(rep: Report, memory: Path | None) -> None:
    if memory is None or not memory.is_dir():
        rep.issue("memory directory not found (pass --memory)")
        return
    index = memory / "MEMORY.md"
    text = index.read_text(encoding="utf-8") if index.exists() else ""
    size, count = len(text.encode("utf-8")), len(text.splitlines())
    rep.note(
        f"memory index: {count} lines, {size} bytes (budget {INDEX_MAX_LINES} / {INDEX_MAX_BYTES})"
    )
    if count > INDEX_MAX_LINES or size > INDEX_MAX_BYTES:
        rep.issue("MEMORY.md over budget: shorten hooks or merge memories")
    linked = set(INDEX_LINK.findall(text))
    files = {p.name for p in memory.glob("*.md") if p.name != "MEMORY.md"}
    for missing in sorted(linked - files):
        rep.issue(f"MEMORY.md links {missing}, which does not exist")
    for unlisted in sorted(files - linked):
        rep.issue(f"{unlisted} is not in MEMORY.md")
    for name in sorted(files):
        body = (memory / name).read_text(encoding="utf-8")
        head = FRONTMATTER.match(body)
        if not head or not all(k in head.group(1) for k in ("name:", "description:", "type:")):
            rep.issue(f"{name}: frontmatter lacks name/description/type")
        budget = STATUS_MAX_LINES if name == STATUS_FILE else FILE_MAX_LINES
        if (n := len(body.splitlines())) > budget:
            rep.issue(f"{name}: {n} lines (budget {budget}); consolidate")
        body_lines = body.splitlines()
        for i, text_line in enumerate(body_lines):
            context = " ".join(body_lines[max(0, i - 1) : i + 1]).lower()
            if any(word in context for word in NOT_IN_REPO_WORDS):
                continue
            for token, line in REPO_PATH.findall(text_line):
                check_path(rep, name, token, line)


def check_path(rep: Report, name: str, token: str, line: str) -> None:
    """Report a repo path a memory names that is gone, or a line past its end."""

    if not any((base / token.split("/")[0]).is_dir() for base in REPO_ROOTS):
        return  # another project's path (HA core, the HA frontend), not ours
    found = resolve_repo_path(token)
    if found is None:
        rep.issue(f"{name}: names `{token}`, which no longer exists")
    elif line and found.is_file():
        length = len(found.read_text(encoding="utf-8", errors="replace").splitlines())
        if int(line) > length:
            rep.issue(f"{name}: `{token}:{line}` is past the end of the file")


# --- main ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch and the wiki clone")
    parser.add_argument("--memory", help="the auto-memory directory, if not found")
    args = parser.parse_args()

    memory = find_memory(args.memory)
    sections: list[tuple[str, Report]] = []
    try:
        if not args.no_fetch:
            run("git", "fetch", "-q", "--prune", "origin", check=True)
        for title, fn in (
            ("git", lambda r: check_git(r)),
            ("session", lambda r: check_session(r, memory)),
            ("release", lambda r: check_release(r)),
            ("wiki", None if args.no_fetch else (lambda r: check_wiki(r))),
            ("memory", lambda r: check_memory(r, memory)),
        ):
            if fn is None:
                continue
            rep = Report()
            fn(rep)
            sections.append((title, rep))
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    total = 0
    for title, rep in sections:
        print(f"## {title}")
        for note in rep.notes:
            print(f"   {note}")
        for issue in rep.issues:
            print(f" ! {issue}")
        total += len(rep.issues)
    print(f"\n{'CLEAN' if not total else f'{total} item(s) need attention'}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
