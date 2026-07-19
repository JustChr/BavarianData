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
  - `config_flow.py` — setup wizard: client ID → device auth → cluster picker (generates a browser-console snippet; BMW has **no API** for Data Selection, it's portal-only).
  - `sensor.py` / `binary_sensor.py` / `image.py` / `device_tracker.py` / `entity.py` — entity platforms. One device per VIN.
  - `descriptors.py`, `keys.py`, `units.py` — descriptor → entity mapping; `keys.py` derives the HA `translation_key` and is shared by runtime **and** generators so they can't drift.
  - `www/bmw-cardata-card.js` — bundled Lovelace card (vanilla JS, registered automatically by `__init__.py`; no build step). Groups entities via their `cluster`/`category` attributes, not names.
- `tools/` — catalogue generation pipeline (see below).
- `tests/` — pytest, **no Home Assistant required** (see below).
- `docs/reference/` — BMW API notes + generated field reference.
- `blueprints/automation/bavariandata/` — shipped automation blueprints.

## Generated files — never hand-edit

These are outputs of the `tools/` pipeline (inputs: BMW's catalogue exports +
project-authored `tools/curated_titles.json`):

- `custom_components/bavariandata/catalogue.json`
- `custom_components/bavariandata/descriptor_metadata.py`
- `custom_components/bavariandata/translations/en.json` and `de.json` —
  **only the `entity` block**; `config`/`options` sections are hand-maintained
  and preserved by the generator
- `docs/reference/telematics-fields.md`

To change an entity name, edit `title_en` in `tools/curated_titles.json`, then
re-run steps 1–4 from `tools/README.md` (`build_catalogue.py`,
`generate_metadata.py`, `generate_translations.py`, `generate_reference_doc.py`)
and run `python -m pytest tests/test_catalogue.py` (checks consistency and
generator idempotence).

## Tests

```
python -m pytest tests/
```

Deps: `requirements_test.txt` (aiohttp + pytest only). `tests/conftest.py`
loads integration modules in isolation via a synthetic package so nothing
imports Home Assistant — keep new test targets HA-import-free, or they won't be
testable here. There is no HA test harness in this repo; config-flow/entity
behavior is verified against a live HA instance manually.

## Releases

Pushing to `main` is **not** a release — HACS users get updates only from
GitHub releases. Use `scripts/release.sh` (bash): bumps
`manifest.json` version, commits, tags `vX.Y.Z`, pushes, runs
`gh release create --generate-notes`. Default is a beta pre-release
(`-beta.N`); pass `--stable` for a full release. Only release when the user
asks.

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
- Minimum supported HA is **2024.6** (`hacs.json`) — don't use newer-only HA
  APIs without bumping it deliberately.
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
- Keep the card dependency-free vanilla JS; there is no bundler.
