---
name: ship
description: Cut a BavarianData release — write the changelog, check for stray files, run scripts/release.sh, publish the wiki. Use when asked to release, cut a beta, publish a version, or ship to HACS. Only run when the user explicitly asks for a release.
---

# Ship a release

Pushing to `main` is **not** a release. HACS users only ever see GitHub
releases. Never run this unprompted.

## 1. The changelog is the release, so write it first

`scripts/release.sh` refuses to run on an empty `## [Unreleased]`, then rolls
that section over into the new version and leaves a fresh empty one behind. So
those notes *become* the release body — write them as notes to a user, not as a
summary of commits.

```bash
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
```

### A stable release: consolidate the betas first

```bash
python tools/stable_changelog.py           # draft + the judgment calls, prints only
python tools/stable_changelog.py --write   # put the draft into ## [Unreleased]
```

It gathers every beta section since the last **stable** (not the last beta
series) plus anything unreleased, tags each bullet with its beta
(`**Lead** (beta.3).`) and lists what needs a human: the preamble's summary,
bullets repeated across betas (fold intra-beta churn into the final state), and
every Fixed bullet (keep it only if the bug was in the last stable). Edit the
result; never rewrite an already-released section.

**The draft is raw material, not the entry: rewrite it.** A stable's notes tell a
stable user what changed since the *last stable*, as one sound account. Write
each change once, in its final state. Betas that reverse or refine each other
(added then removed, renamed, a default flipped back, a feature reworked) become
the one bullet for the end result, or nothing if the net change is nothing. A fix
to something only a beta introduced is not a Fixed bullet: the stable user never
had that bug. No "now", "again" or "no longer" measured against a beta, and nothing
that only makes sense to someone who ran the betas. Read the finished entry
top to bottom as a stable user would before releasing.

## 2. Check for strays before the script stages everything

`release.sh` runs `git add .`. Anything untracked ships.

```bash
git status --porcelain     # look hard at every ?? line
```

## 3. Confirm the gates are green

```bash
bash scripts/check.sh
```

It runs CI's exact commands and paths (`.github/workflows/test.yml`) — ruff,
the format check, ESLint, pytest — and ends `GREEN` or `RED`. Its stale-translation
list is advisory; clear it with the `translate` skill before a release. The format check is
separate from the lint and is easy to skip: v0.9.13-beta.3 went out with CI red
over one line wrapped wrong in a new test file.

And the last CI run on `main` itself, since that covers what a local run
can't (real HA, typing, per-module coverage, hassfest):

```bash
gh run list --branch main --limit 4
```

Every workflow on `main`'s HEAD must be `completed success`. Never release over
a red or still-running `main`.

**A stable promotes a beta that was proven live.** If `current-status.md` lists
something to verify on the live instance before the stable (a midnight reset,
a refresh time), check it with the `live` skill first, or ask.

## 4. Release

```bash
bash scripts/release.sh              # beta pre-release (default)
bash scripts/release.sh --stable     # full release
```

It bumps `manifest.json`, commits, tags `vX.Y.Z`, pushes, and creates the GitHub
release with `--notes-file`. Beta numbering rolls to the next patch after
`beta.9`.

## 5. Publish the wiki — with a stable only

**A beta does not publish the wiki.** The wiki is what stable users read, so pages
describing a beta's features wait for the stable that ships them (Chris's rule).
Publish it with `--stable`, or for a beta only when every changed page is
docs-only drift that is already true of the current stable — and then say so.

```bash
bash scripts/publish-wiki.sh     # stable releases
```

The wiki is a separate git repo; `docs/wiki/` is the source of truth. Pages
reference screenshots through `raw.githubusercontent.com/.../main/screenshots/…`,
so **screenshot files must already be committed to `main`** or the images 404.

## 6. A beta: write down how it will be proven

A beta exists to be proven on the live instance before the stable. Right after
releasing one, put its **predictions** into *Next up* in `current-status.md`:
for each change, what should be observable, on which entity or log, **when**
(UTC), and what it costs in quota (aim for zero). For example: "00:00 UTC:
quota sensor jumps 40 → 50; ~10:43 UTC refresh costs 2 requests, CBS fresh".
A prediction you can't check without spending quota or driving the car is a
task for Chris; say so.

Installing it and checking the predictions is the `live` skill (*Proving a
beta*). The stable waits until every prediction holds or Chris waives one.

## Before you call it done

Every new config-flow step, options screen, card view, service, option key,
event or derived entity needs its wiki row **and its German counterpart in the
same commit**, plus a line in `docs/documentation-plan.md`. That matrix is the
definition of "documented everything".
