---
name: support
description: Work a user's GitHub issue or Discussion end to end — read the whole thread, establish the facts before touching code, triage any diagnostics, draft a reply in Chris's voice that links the wiki, and update the open threads. Use when given an issue or discussion number, when asked "what's open", "answer #N" or "draft a reply", or when a user report needs a response. Never posts without Chris's go.
---

# Work a support thread

Most of this project's users meet it through a thread, and the reply is the
product as much as the code is. The bar: the reporter's real question is answered
with evidence, the answer outlives the thread (a wiki link), and nothing is
posted or promised that Chris hasn't seen.

## 1. Read the whole thread

`#N` may be an issue or a **Discussion**: if `gh issue view N` 404s, it is a
Discussion. `gh` has no discussion command, so use GraphQL, and **ask for the
nested replies**. The top-level comments alone hide answers already given.

```bash
gh issue view <N> --repo JustChr/BavarianData --json title,state,author,body,comments
gh api graphql -f query='query { repository(owner:"JustChr", name:"BavarianData") {
  discussion(number: <N>) { title url author { login } body
    comments(first: 50) { nodes { id author { login } createdAt body
      replies(first: 50) { nodes { author { login } createdAt body } } } } } } }'
```

Before saying anything is open, note **who spoke last and when**. If it was us,
the thread is waiting on the reporter: say so and stop.

## 2. Facts before code

Changing code on a plausible hypothesis is the mistake to avoid. Separate what the
reporter **observed** (a value, a time, a screen) from what they **concluded**.
Then:

- **Diagnostics attached?** Run the `triage` skill on it first. Diagnostics have
  solved reports whose own description pointed the wrong way.
- **The same symptom on the live instance?** Check it with the `live` skill (zero quota).
- **Already decided?** Read `settled-decisions` in memory before proposing a
  change. A feature request that reruns a closed call gets the reason, not a new
  debate.
- **Not enough facts?** The reply asks for them: the exact entity, the time, the
  version, diagnostics. **Never ask for a VIN, positions or a raw capture, not
  even privately.** Give the self-redaction steps instead
  (`tools/anonymize_capture.py`, then search the output for the VIN and the
  street before attaching). Debug logs (`debug_log`) can hold VIN and GPS, so
  ask for them only with those steps.

If it is a bug in our code: it gets a test that fails without the fix
(CLAUDE.md, *Tests*), then the `land` skill, and a changelog line crediting the
reporter by handle. If it is BMW's (a refused field, a silent stream,
`access_denied`): say so plainly, with the evidence.

## 3. The answer is a wiki link

If the answer isn't already in `docs/wiki/`, the wiki has a gap. Write the
section (EN + `de/DE-…`, same commit), land it, and link it. Link
`justchr.github.io/BavarianData/…` or the wiki page, whichever already holds the
text **for the released version**. A beta-only feature isn't documented to
stable users yet. If a wiki statement turned out to be wrong, fix it in the same pass.

## 4. Draft the reply

In Chris's register, which differs from the poster's:

- **Verdict first.** "That one isn't broken." as the first line, no preamble.
  Then the evidence, then what to do. Short.
- German threads: plain German with *du*; never mirror the poster's dialect or
  greeting slang. Open with a neutral `Hallo <handle>` and a thank-you.
- No pitch, no emojis, no promise beyond what is verified. Don't promise to
  report back when a stable ships; invite more feedback "if you feel like it".
- When it's relevant: the integration is **read-only** (it can't precondition
  or lock) and allows **one stream per account**.
- Link the wiki, not a numbered announcement thread (`/discussions` instead).

Show the draft to Chris. **Posting, closing, labelling or reacting is outward-facing:
only on Chris's explicit go.** Then:

```bash
gh issue comment <N> --body-file -                     # an issue
gh api graphql -f query='mutation { addDiscussionComment(input: {
  discussionId: "<id>", replyToId: "<top-level comment id>", body: "…" }) { comment { url } } }'
```

## 5. Record it

Update *Open threads* in `current-status.md` (memory): the thread, who spoke
last and when, and what is owed by whom ("owed by them: diagnostics", "owed by
us: tell them it shipped"). Something learned about BMW or a car trait goes into
its topic memory, not the status file.
