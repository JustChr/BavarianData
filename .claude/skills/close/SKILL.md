---
name: close
description: Close a working session cleanly so the next one starts seamlessly — finish or park the work, run the gates if code changed, bring docs and the wiki in line, prune and consolidate memory, and set the close marker. Use when the user says close, wrap up, end the session, "clean up", or before stopping for the day.
---

# Close the session

The goal: a fresh session reads `MEMORY.md` → `current-status.md` and knows
**exactly** where things stand, with nothing left half-done on this machine
that it can't see. Close never releases, never posts to GitHub issues or
discussions, never force-pushes and never deletes work that isn't merged:
those stay the user's call. Report them; don't do them.

## 1. Read the state

```bash
python scripts/close_check.py
```

Read-only. It lists uncommitted files, unpushed commits, branches not merged,
stashes, worktrees and open PRs; what changed since the last close (by area);
code on `main` without a changelog entry; wiki pages that differ from the
published wiki; and memory over budget, out of sync with its index, or naming
repo paths that no longer exist. `!` lines need an answer; the rest is
context. Re-run it at the end: the goal is `CLEAN`, or every `!` left on
purpose and written down in step 6.

## 2. Finish or park the work

For each uncommitted change and each unmerged branch, decide:

- **Done** (the user said so, or it's a finished fix with its test): land it
  with the `land` skill. Merge on green is the standing rule.
- **Not done:** commit it on its own branch (`wip/<slug>` or the existing
  feature branch), push the branch, **no PR, never `main`**. Write the branch,
  what's done and the very next step into `current-status.md` ("In flight").
- **Junk** (scratch output, a capture, diagnostics): delete it. Never commit
  it, because the repo is public. Stage by path; never `git add .`.

A branch already merged into `main` is deleted locally (`git branch -d`). An
open PR you opened this session is either landed or listed in the status file
with what it waits for. A stash labelled "drawer" is parked on purpose and has
a memory; anything else gets a branch or is dropped, after asking.

## 3. Gates, if code changed

If step 1 says code changed since the last close, run `bash scripts/check.sh`
as its own command and read its last line. Don't close on RED: fix it, or
park the failing work on a branch (step 2) and say so.

## 4. Docs in lockstep

Walk the session's changes (`git log --oneline <last close>..HEAD`) and the
support threads it handled, not the whole repo:

- every user-visible change has its line under `## [Unreleased]` in
  `CHANGELOG.md`;
- new or changed screens, card views, services, options, events or derived
  entities have their wiki row **and** the German counterpart, and the
  coverage matrix in `docs/documentation-plan.md` moved with them;
- `CLAUDE.md` still tells the truth about layout, tooling, constraints and
  conventions this session touched (a new skill, script, hook or test harness
  belongs in it);
- README stays the shop window: only change it if the quick start, the
  screenshots or the requirements changed.

For a large or unfamiliar diff, the `docs-lockstep` agent reports what went
stale; for a small one, check by hand.

The list above catches what is **missing**. Two more checks catch what is
**wrong**:

- **Changed behavior falsifies old text.** For every behavior this session
  changed or removed, grep `docs/wiki/` (both languages) for the entity,
  option, service or attribute name, and read each hit: does it still hold?
  A new row next to a now-false paragraph is still a stale wiki.
- **Support answers belong in the wiki.** For every issue, discussion or
  diagnostics read this session: if the answer had to come from the code or
  from memory rather than a wiki link, the wiki has a gap. Write the page
  (EN + DE) so the next reply can be a link. If a wiki statement turned out
  wrong while answering, fix it here too. (#62: nothing said which of five
  range sensors is the cluster figure, and the "unavailable" section described
  behavior the integration never had.)

## 5. Wiki

For each page step 1 lists as different from the published wiki:

- *publish now* (docs-only changes, or pages of an already released version):
  `bash scripts/publish-wiki.sh`. Any screenshot a page links must already be
  on `main`.
- *with the release*: leave it. Feature docs for unreleased code go out with
  `ship`, so users of the released version never read about features they
  don't have. Note it in "Next up" if a release is pending.

## 6. Memory

Memory is what the next session starts from. Its rules (see the system
prompt): one fact per file, nothing the repo or git already records, update
rather than duplicate, delete what turned out wrong. At close:

**`current-status.md` is a status, not a log.** Rewrite it, don't append:

- *Release state*: latest stable, newest pre-release, what's on `main` but
  unreleased (by PR number), and whether the wiki is published.
- *In flight*: parked branches with their next step, PRs waiting and on whom.
- *Next up*: the prioritized list, short.
- *Open threads*: outward-facing conversations, and what's owed to whom.

Remove anything finished: it lives in git and `CHANGELOG.md` now. Keep the
file under its budget (100 lines). The last line is the marker:

```
Last close: <short sha of HEAD on main> (YYYY-MM-DD)
```

**Open threads are refreshed from GitHub, never copied forward.** Memory goes stale
between sessions: someone else, or an earlier session, may have closed an issue,
merged a PR or answered a thread. For every issue, PR and discussion listed under
*Open threads* and *In flight*, and every one touched this session, read its live
state and last comment before rewriting the file:

```bash
gh issue view <n> --json state,closedAt,comments --jq '{state, closedAt, last: .comments[-1] | {a: .author.login, t: .createdAt, b: .body[0:300]}}'
gh pr view <n> --json state,mergedAt
```

Then write what is true now: closed threads leave the list (with the date, if it
matters), a new reply from the user moves the thread to "owed by us", and a
release that fixes a thread makes "tell them it shipped" an owed item. Never post
from close; list it for the user in the report.

**Topic memories.** Anything learned this session that a future session
needs and the repo can't hold (why a decision went the way it did, a live
measurement, a trap) goes into the memory it belongs to. A decision the user
made goes into `settled-decisions.md`. Fix every memory `close_check` lists as
naming a gone path: update the path, or drop the sentence if it's now history.

**Consolidate when a budget breaks** (index over 60 lines or 6 KB, a file
over 120 lines, or two memories covering the same ground): merge them, cut
history the code and git already tell, keep the *why*. Rewrite `MEMORY.md`
so it holds exactly one line per memory file, with a hook specific enough to
decide relevance. Convert relative dates to absolute ones.

## 7. Report

End with a short report for the user:

- what landed and what's parked (branch names);
- anything only the user can do: a release to cut, a reply to post, a test in
  the real car or HA, a decision;
- whether the next session has a clear first step, and what it is.

Keep it short; the details are in `current-status.md`.
