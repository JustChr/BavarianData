# Catalogue generation pipeline

The field clustering, metadata and translations are all generated from BMW's
telematic data catalogue. Run the steps in order after refreshing a source
export; each writes into `custom_components/bavariandata/`.

| Step | Command | Input | Output |
| --- | --- | --- | --- |
| 1. Canonical dataset | `python tools/build_catalogue.py` | `CustomerTelematicsDataCatalogue.html` (German export, provides BMW's sections + German text), `descriptor-list.csv` (English export, provides sub-category + English text + raw enum values) and `curated_titles.json` (our curated English display names) | `catalogue.json` |
| 2. Metadata registry | `python tools/generate_metadata.py` | `catalogue.json` | `descriptor_metadata.py` (device/state class, unit, enum options, entity category, enabled-by-default) |
| 3. Translations | `python tools/generate_translations.py` | `catalogue.json` | `translations/en.json`, `translations/de.json` (entity names + enum state labels) |
| 4. Reference doc | `python tools/generate_reference_doc.py` | `catalogue.json` + `descriptor_metadata.py` | `docs/reference/telematics-fields.md` |

To rename an entity, edit its `title_en` in `tools/curated_titles.json` and
re-run steps 1–4. (`curated_titles.json` is project-authored, not a BMW export.)

`keys.py` (shipped in the integration, not a tool) derives the Home Assistant
`translation_key` from a descriptor and is shared by the generators and the
runtime entities so they can never drift.

`tests/test_catalogue.py` checks the outputs stay consistent (every descriptor
has metadata + bilingual translations, enum options have labels, generators are
idempotent). Run `python -m pytest tests/test_catalogue.py`.

## Refreshing from BMW

Download a fresh catalogue export from the BMW CarData portal
(`.../public/cardata-telematic-catalogue`) as HTML, drop it in this folder as
`CustomerTelematicsDataCatalogue.html`, then re-run steps 1–4 and the tests.
