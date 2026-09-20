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

## 2. Check for strays before the script stages everything

`release.sh` runs `git add .`. Anything untracked ships.

```bash
git status --porcelain     # look hard at every ?? line
```

## 3. Confirm the gates are green

```bash
python -m pytest tests/ -q
python -m ruff check custom_components/bavariandata tests
```

## 4. Release

```bash
bash scripts/release.sh              # beta pre-release (default)
bash scripts/release.sh --stable     # full release
```

It bumps `manifest.json`, commits, tags `vX.Y.Z`, pushes, and creates the GitHub
release with `--notes-file`. Beta numbering rolls to the next patch after
`beta.9`.

## 5. Publish the wiki if any page changed

```bash
bash scripts/publish-wiki.sh
```

The wiki is a separate git repo; `docs/wiki/` is the source of truth. Pages
reference screenshots through `raw.githubusercontent.com/.../main/screenshots/…`,
so **screenshot files must already be committed to `main`** or the images 404.

## Before you call it done

Every new config-flow step, options screen, card view, service, option key,
event or derived entity needs its wiki row **and its German counterpart in the
same commit**, plus a line in `docs/documentation-plan.md`. That matrix is the
definition of "documented everything".
