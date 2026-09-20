---
name: regen
description: Regenerate the descriptor catalogue, metadata, translations and field reference from tools/. Use whenever an entity is renamed, a descriptor is added, tools/curated_titles.json or tools/derived_entities.json changes, a fresh BMW catalogue export is dropped in, or a test complains that a generated file is stale.
---

# Regenerate the catalogue pipeline

Five generators, run **in order** — each one reads the previous one's output, so
a partial run leaves the tree self-consistent but wrong.

```bash
python tools/build_catalogue.py        # -> catalogue.json
python tools/generate_metadata.py      # -> descriptor_metadata.py
python tools/generate_translations.py  # -> translations/{en,de}.json (entity block only)
python tools/generate_reference_doc.py # -> docs/reference/telematics-fields.md
python tools/generate_en_gb.py         # -> translations/en-GB.json (delta over en.json)
python -m pytest tests/test_catalogue.py tests/test_translations_dialect.py
```

The tests are not optional: they are what proves the generators are idempotent
and that every descriptor has metadata and **both** languages.

Step 5 must run after **any** change to `en.json`, including a hand-edit to the
flow strings — it re-derives the British delta from it. `en-GB.json` is not a
translation anybody maintains: Home Assistant loads `en` and overlays the
requested language on top of it key by key, so the file holds only the ~75
strings that differ, and `tools/spelling_en_gb.json` is the word list it is
derived from.

## Edit the input, never the output

A hook refuses writes to the four generated files. What to edit instead:

| Want to change | Edit |
| --- | --- |
| An entity's English name | `title_en` in `tools/curated_titles.json` |
| A derived/diagnostic sensor, the device tracker, the vehicle image | `tools/derived_entities.json` |
| The flow strings (`config` / `options`) | `translations/en.json` **and** `de.json` by hand — these are not generated (then re-run step 5) |
| A US/UK spelling pair | `tools/spelling_en_gb.json` — never `en-GB.json`, which is fully generated |

Entities with no BMW descriptor **must** have a `derived_entities.json` entry
with a `_attr_translation_key` identical to the code's. A hardcoded
`_attr_name` makes German installs silently fall back to English.

## When step 2 stops with an unknown unit

That error is deliberate. A unit spelling BMW uses that `units.py` does not know
produces a sensor with no device class, no conversion and no long-term
statistics, and nothing downstream looks wrong. Add the spelling to
`UNIT_ALIASES` in `custom_components/bavariandata/units.py`, then decide whether
`device_and_state_class` should classify it. BMW spells units differently on the
stream than in the catalogue (`kpa`, `degrees`) — that is the usual cause.

## Refreshing from BMW

```bash
curl -s https://mybmwweb-utilities.api.bmw/de-at/utilities/bmw/api/cd/catalogue/file \
  -o tools/CustomerTelematicsDataCatalogue.html
```

Market-scoped: the locale in the path picks the language. Then run all five
steps and the tests. The catalogue — not the Swagger — is the stream's contract.
