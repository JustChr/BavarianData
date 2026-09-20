# CLAUDE.md

BavarianData — a Home Assistant (HA) custom integration (HACS) that connects HA
directly to **BMW CarData**: a live MQTT stream plus a REST API, using the
user's personal BMW client ID. Domain: `bavariandata`. Repo:
`JustChr/BavarianData`. License: MIT. Read-only — CarData cannot command the car.

## Layout

- `custom_components/bavariandata/` — the integration (everything shipped to users).
  - `api.py` — REST client (auth headers, quota accounting).
  - `device_flow.py` — OAuth 2.0 device-authorization flow (no HA imports; unit-testable).
  - `stream.py` — MQTT streaming client (paho-mqtt).
  - `coordinator.py` — central state: token refresh, stream lifecycle, quota, charging-session tracking + `bavariandata_charging_*` events, derived charged-energy sensors.
  - `config_flow.py` — setup wizard: client ID → device auth → cluster picker → in-browser bookmarklet activator (shared by guided, manual and Configure via `_StreamActivatorFlow`; BMW has **no API** for Data Selection, it's portal-only).
  - `sensor.py` / `binary_sensor.py` / `image.py` / `device_tracker.py` / `entity.py` — entity platforms. One device per VIN.
  - `descriptors.py`, `keys.py`, `units.py` — descriptor → entity mapping; `keys.py` derives the HA `translation_key` and is shared by runtime **and** generators so they can't drift.
  - `www/bavariandata-card.js` — bundled Lovelace card (vanilla JS, registered automatically by `__init__.py`; no build step). Groups entities via their `cluster`/`category` attributes, not names.
- `tools/` — catalogue generation pipeline (see below).
- `tests/` — pytest, **no Home Assistant required** (see below).
- `docs/reference/` — BMW API notes + generated field reference.
- `blueprints/automation/bavariandata/` — shipped automation blueprints.

## Generated files — never hand-edit

These are outputs of the `tools/` pipeline (inputs: BMW's catalogue exports +
the project-authored `tools/curated_titles.json` and
`tools/derived_entities.json`):

- `custom_components/bavariandata/catalogue.json`
- `custom_components/bavariandata/descriptor_metadata.py`
- `custom_components/bavariandata/translations/en.json` and `de.json` —
  **only the `entity` block**; `config`/`options` sections are hand-maintained
  and preserved by the generator
- `docs/reference/telematics-fields.md`

Entities without a BMW descriptor (derived/diagnostic sensors, device tracker,
vehicle image) are named from `tools/derived_entities.json` — never a hardcoded
`_attr_name`, or German installs silently fall back to English.

To change an entity name, edit `title_en` in `tools/curated_titles.json`, then
re-run steps 1–4 from `tools/README.md` (`build_catalogue.py`,
`generate_metadata.py`, `generate_translations.py`, `generate_reference_doc.py`)
and run `python -m pytest tests/test_catalogue.py` (checks consistency and
generator idempotence).

## Tests

```
python -m pytest tests/
```

Deps: `requirements_test.txt` — light on purpose (aiohttp, pytest, PyYAML,
plus a pinned `ruff`); nothing there pulls in Home Assistant. `tests/conftest.py`
loads integration modules in isolation via a synthetic package so nothing
imports Home Assistant — keep new test targets HA-import-free, or they won't be
testable here. There is no HA test harness in this repo; config-flow/entity
behavior is verified against a live HA instance manually.

Some tests run the shipped card under Node and skip themselves when it is
absent; CI pins Node 24 so that coverage cannot silently disappear.

`tests/test_card_snapshots.py` pins a golden render of **every card view for
every drivetrain**, so a layout change shows up as a readable diff instead of
having to be spotted by eye in Home Assistant. When a change to the card is
intended, approve it with `python -m pytest tests/test_card_snapshots.py
--snapshot-update` and **read the resulting diff** — that review is the point.
The harness freezes the clock and forces English; without that the snapshots
would rot daily.

## User documentation

User-facing docs live in three tiers — keep them **in lockstep with the code**:

- `README.md` — Tier-1 shop window only (overview, screenshots, requirements,
  the 4-step quick start, links into the Wiki). Keep it lean; detail belongs in
  the Wiki, not here.
- `docs/wiki/*.md` — the full manual and the **source of truth** for the GitHub
  Wiki (a separate git repo). Publish with `bash scripts/publish-wiki.sh` after
  changing any page. Pages reference screenshots via
  `raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/…`, so
  screenshot files must be committed to `main` for the images to load.
- `docs/reference/*` — generated deep reference; never hand-edit generated files.

**A feature isn't done until its docs are updated in the same change.** Any new
config-flow step, Configure/options screen, card view, service (`services.yaml`),
option key, event, or derived entity needs its Wiki page/row — and, where it's a
visible screen, a screenshot (capture against the live HA instance; force English
first). Update the coverage matrix in [`docs/documentation-plan.md`](docs/documentation-plan.md)
too — that matrix is the definition of "documented everything," and reviewing it
is how you catch a gap. English is the source language; every page has a German
counterpart in `docs/wiki/de/DE-<page>.md` that changes **in the same commit**
(`tests/test_wiki_links.py` checks links, anchors and the EN↔DE pairing;
`tests/test_docs_lockstep.py` checks that every service in `services.yaml`
and every card view is described in both languages **and** present in the
coverage matrix).

## Claude Code tooling (`.claude/`)

Machinery for the rituals above, so they survive a fresh session instead of
living only in prose.

- **Hooks** (`.claude/hooks/`, wired in `.claude/settings.json`):
  `guard_generated.py` refuses writes to the pipeline's output and names the
  input to edit instead; `lint_touched.py` runs ruff on a `.py` and
  `node --check` on the card right after it is written; `commit_guard.py` asks
  before a commit that changes an English wiki page without its German
  counterpart, or that ships code while `## [Unreleased]` is empty.
- **Skills** (`.claude/skills/`): `regen` (the four generators, in order),
  `ship` (release), `triage` (read a user's diagnostics), `live` (zero-quota
  reads against the live instance), `shoot` (Playwright screenshots).
- **Agent** (`.claude/agents/docs-lockstep.md`): reads a diff and reports which
  wiki pages, German counterparts, matrix rows and screenshots went stale.

Host names, addresses and credentials stay in local memory, never in these
files — this repo is public.

## Releases

Pushing to `main` is **not** a release — HACS users get updates only from
GitHub releases. Use `scripts/release.sh` (bash): bumps `manifest.json` version,
commits (`git add .` — so check for stray files first), tags `vX.Y.Z`, pushes,
and creates the release with `--notes-file`. Default is a beta pre-release
(`-beta.N`); pass `--stable` for a full release. Only release when the user
asks.

**The release notes are the `## [Unreleased]` section of `CHANGELOG.md`** — the
script refuses to run if it is empty, then rolls it over to the new version and
leaves a fresh empty one behind. So write the changelog *before* releasing; it
is the release, not a summary of it. Publish the wiki afterwards
(`bash scripts/publish-wiki.sh`) when any `docs/wiki/` page changed.

Version lives in `custom_components/bavariandata/manifest.json`. `hacs.json`
sets `zip_release: true` / `bavariandata.zip` (the release workflow expects the
zip asset).

## Constraints & hard-won quirks

- **REST quota: 50 requests / 24 h per account.** Enforced in `api.py`/
  `coordinator.py`; every manual service call spends one. Never add polling
  that burns quota — prefer the stream, cache what's fetched (the vehicle
  image entity is cached across restarts for exactly this reason).
- **BMW's MQTT broker is TLS 1.3-only** — the stream client must not offer
  lower versions.
- **One concurrent stream per account (GCID)** — reconnect logic must not race
  a second connection.
- **BMW device auth is flaky**: it can return `access_denied` even after a
  successful login. This is BMW-side; README's Troubleshooting documents the
  workaround ritual. Don't "fix" it in code beyond clear error messages.
- **hassfest forbids URLs inside translation strings** (`translations/*.json`).
- Descriptor selection ("Data Selection") is portal-only; per-descriptor
  streaming scopes are rejected by BMW — see
  `docs/reference/stream-scope-investigation.md` before revisiting.
- Minimum supported HA is **2026.3** (`hacs.json`; needed for self-served brand
  icons in `brand/`) — don't use newer-only HA
  APIs without bumping it deliberately. That floor sets the **Python floor too**:
  HA 2026.3 requires **Python 3.14.2** (2026.2 was the last release to accept
  3.13), so every install runs 3.14+. CI tests 3.14 only and `pyproject.toml`
  targets `py314`; raising the HA floor means revisiting both.
- The source is **syntactically 3.14-only**. `ruff format` at `target-version =
  "py314"` drops the parentheses from multi-exception `except` clauses (PEP 758),
  so `except TypeError, ValueError:` appears throughout — valid on 3.14, a
  *syntax error* on 3.13, which therefore cannot even import this package. That
  is deliberate and matches the floor. Run the suite with 3.14; if the floor
  ever drops below 2026.3, lower `target-version` and re-run `ruff format`.
- Entities must keep exposing `cluster`/`category` attributes even when
  restored/unavailable — the Lovelace card's cluster views depend on them.
- README image links use absolute `raw.githubusercontent.com` URLs on purpose
  (HACS info screen can't resolve relative paths).

## Conventions

- Python: `from __future__ import annotations`, type hints, module docstrings —
  match the existing style. Logging via module loggers; debug logging is
  opt-in (`debug_log` option) because it can contain VIN/GPS.
- User-facing strings live in `translations/`: the `entity` block is generated
  by the pipeline; the `config`/`options` (flow) sections are hand-edited
  directly in `en.json`/`de.json`.
- English and German are both first-class: entity naming changes must land in
  both languages (the pipeline handles this).
- Keep the card dependency-free vanilla JS; there is no bundler. The root
  `package.json` is **dev-only** — ESLint (`npx eslint
  custom_components/bavariandata/www`, config in `eslint.config.mjs`) — and
  nothing from `node_modules/` reaches a user. `no-unsanitized/property` is off
  there on purpose; `tests/test_card_escaping.py` guards escaping far more
  precisely, and the reasoning is written out in the config.
