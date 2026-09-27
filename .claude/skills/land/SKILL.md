---
name: land
description: Land a finished change on main — check, branch, commit, push, open a PR, wait for CI, rebase-merge, pull. Use when asked to commit, merge, "land it", push a change to main, or open a PR for work in this repo. Not a release — pushing to main ships nothing to HACS users; use the ship skill for that.
---

# Land a change on `main`

The maintainer's rule: **merge to `main` once CI is green, without asking.
Release only when asked** (that is `ship`, not this).

## 1. Is the change complete?

- `git status --short` — every `??` must be something you meant to add.
- A user-visible change has its line under `## [Unreleased]` in `CHANGELOG.md`,
  written for users, crediting reporters by handle.
- Docs moved with it: wiki page **and** its `docs/wiki/de/DE-…` counterpart, the
  matrix row in `docs/documentation-plan.md`. The `docs-lockstep` agent reports
  what went stale if unsure.
- Changed English text? Run the `translate` skill first — `python tools/i18n_gaps.py`
  must list nothing.

## 2. Every gate, locally

```bash
bash scripts/check.sh
```

It runs CI's exact commands and paths and ends `GREEN` or `RED — …`. Do not
continue on red. One known flake:
`test_stream_reconnect.py::test_a_refused_login_followed_by_a_renewed_token_leaves_one_stream`
can fail under load — rerun that file alone before investigating.

## 3. Branch, commit, push

On `main`, branch first: `git checkout -b <feat|fix|docs|chore>/<slug>`.

Stage **by path**, never `git add .` — the repo is public, and a stray capture or
diagnostics file is a leak. Commit message: an imperative subject in the repo's
style (`git log --oneline -10`), a body saying *why*, ending with the attribution
line from the system reminder. Write multi-line messages through a quoted
heredoc (`git commit -F - <<'EOF'`).

```bash
git push -u origin <branch>
```

## 4. Pull request

```bash
gh pr create --base main --title "<subject>" --body-file - <<'EOF'
<what changed and why, for a reviewer; anything unverified said plainly>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

## 5. Wait for CI

```bash
gh pr checks <N> --watch --interval 20
```

Hassfest and the HACS action run **only** here — a local green does not cover
them (translation files and the manifest are what they reject). On red:
`gh run view <run-id> --log-failed`, fix, push to the same branch, watch again.

## 6. Merge and tidy

```bash
gh pr merge <N> --rebase          # rebase-merge; no --delete-branch
git checkout main && git pull
git branch -d <branch>
```

If the merge is refused by a permission check, **stop and hand the PR link to
the user** — never work around the refusal (no direct push to `main`, no other
merge path).

## 7. Afterwards

- If `current-status.md` in memory tracks this work, mark it merged (and
  unreleased).
- Say in one line that it is on `main` but **not released**, and whether the
  wiki needs publishing with the next release (`docs/wiki/` changed).
