---
name: triage
description: Read a user's attached diagnostics JSON from a GitHub issue and work out whether the fault is BMW's or ours. Use when an issue has a diagnostics attachment, when a reported value looks wrong, scaled or stuck, or when asked to investigate a bug report.
---

# Triage from diagnostics

The attached file is the primary evidence. Read it **before** theorising — two
issues in two days were solved from diagnostics after the reporter's own
description pointed the wrong way.

## Get it

```bash
gh issue view <n> --repo JustChr/BavarianData --json title,body,comments
```

The attachment URL is in the body. `gh issue view --comments` prints nothing
under this shell; the `--json` form is reliable. Load the JSON with
`io.open(..., encoding="utf-8")` — a plain `open()` dies on cp1252 for the
degree sign and BMW's German text.

## `arrivals` decides whether a row describes BMW or us

Each descriptor row carries `descriptor`, `arrivals`, `last_timestamp`, `unit`.
`arrivals` counts messages received **this session**:

- **`arrivals > 0` → BMW.** The `unit` is genuinely what the stream sent.
- **`arrivals: 0` with a `last_timestamp` → a restored value.** Before v0.9.7
  the `unit` shown was the user's *display* unit written back into our own
  store. **Never quote a unit from a zero-arrival row as evidence about BMW.**

`arrivals_total: 0` with `last_message_at: null` means the whole vehicle block
is restored — the integration had just started.

## Cross-check units against the catalogue, not against expectations

Scan every row's `unit` against `descriptor_metadata.py`. It is cheap and it
finds the anomaly for you — that scan surfaced eleven mismatches (`kpa`,
`degrees`) and turned a vague suspicion into a rule.

## A value wrong by a *power* of a factor is a per-restart ratchet

Not a scaling bug. Prove it in the recorder on the live instance (`/live`):
look for a **sawtooth** — monotonic decay by a fixed factor, reset when a fresh
message lands. One case ran `0.0364 → 0.000606 → 1.01e-05 → …`, six divisions
by the min→h factor, then a jump back on arrival.

Read the reporter's numbers as arithmetic: two metrics off by *different*
powers of the *same* factor means one mechanism applied a different number of
times — which is what restarts do. That names the mechanism before any code is
read.
