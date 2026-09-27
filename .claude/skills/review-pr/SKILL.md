---
name: review-pr
description: Review an external contributor's pull request to BavarianData by reproducing it — check it out in a worktree, run every gate, prove its test guards its fix, judge it against the project's constraints, then report. Use when asked to review, check or merge someone else's PR (a PR number or link from another author).
---

# Review a contributor's PR

The bar: **"if it looks good and fits our design"** — a real review, done by
reproducing, not by reading the diff alone. Commenting on, approving or merging
someone else's PR is outward-facing: **report to the user first** unless they
already asked for that exact action.

## 1. Look before checking out

```bash
gh pr view <N> --json title,author,headRefName,headRepository,maintainerCanModify,files,body
gh pr checks <N>
```

Fork CI from a first-time contributor shows a zero-job "failure": it is
**awaiting approval**, not red. Say so; approving the run is the user's call.

## 2. Reproduce in a worktree

Under OneDrive `git worktree remove` can fail; `git worktree prune` works.

```bash
git fetch origin pull/<N>/head:pr-<N>
git worktree add "<scratchpad>/pr-<N>" pr-<N>
cd "<scratchpad>/pr-<N>" && npm ci --silent   # ESLint is part of the gates
bash scripts/check.sh
```

Then, from the worktree, for each test the PR adds or changes that guards a fix:

```bash
python scripts/prove_fix.py <tests…> --base origin/main
```

`PROVEN` means the test fails with `main`'s code and passes with the PR's. A
coordinator fix **must** come with such a scenario or replay (CLAUDE.md); a
test that passes either way is a finding, not a formality. Use `--revert` when
the PR also touches unrelated code.

Text changes: `python tools/i18n_gaps.py --since origin/main` — a contributor
editing English rarely carries it into nine languages; that can be done for
them after merging (`translate` skill), but say it is owed.

## 3. Judge it against the design

Read the diff with CLAUDE.md's constraints in hand. The usual misses:

- **REST quota** (50/24 h): no new polling; cache what is fetched.
- **One stream per account**; the MQTT client offers TLS 1.3 only.
- **HA floor 2026.3 / Python 3.14** — no newer-only HA API; `except A, B:` is fine.
- **Generated files** hand-edited (`catalogue.json`, `descriptor_metadata.py`,
  the `entity` blocks, `en-GB.json`, the field reference) — must come from `tools/`.
- A derived entity with a hardcoded `_attr_name` instead of `derived_entities.json`.
- Entities losing their `cluster`/`category` attributes when unavailable.
- **Privacy**: no real VIN, entry id, coordinates or host names — the repo is public
  (`test_services_and_privacy.py` catches VIN/ULID shapes, not everything).
- **Docs in the same change**: wiki page + German counterpart + matrix row, and a
  changelog line under `[Unreleased]` crediting the contributor by handle.

## 4. Report, then act on the answer

To the user: what it does, gates (green/red), `prove_fix` verdict, design
findings ranked, and what you would add after merging (translations, docs,
changelog credit). Recommend merge / changes / close.

Only once the user says so:

- Merge: `gh pr merge <N> --rebase` (no `--delete-branch`).
- Fix-ups on the contributor's branch, if `maintainerCanModify`: push by URL,
  `git push https://github.com/<owner>/<repo>.git pr-<N>:<headRefName>`.
- Review comments: `gh pr review <N> --comment|--request-changes --body-file -`.

## 5. Clean up

```bash
git worktree remove "<scratchpad>/pr-<N>" || git worktree prune
git branch -D pr-<N>
```
