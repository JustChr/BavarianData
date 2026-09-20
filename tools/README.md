# Catalogue generation pipeline

The field clustering, metadata and translations are all generated from BMW's
telematic data catalogue. Run the steps in order after refreshing a source
export; each writes into `custom_components/bavariandata/`.

| Step | Command | Input | Output |
| --- | --- | --- | --- |
| 1. Canonical dataset | `python tools/build_catalogue.py` | `CustomerTelematicsDataCatalogue.html` (German export, provides BMW's sections + German text), `descriptor-list.csv` (English export, provides sub-category + English text + raw enum values) and `curated_titles.json` (our curated English display names) | `catalogue.json` |
| 2. Metadata registry | `python tools/generate_metadata.py` | `catalogue.json` | `descriptor_metadata.py` (device/state class, unit, enum options, entity category, enabled-by-default, streamable) |
| 3. Translations | `python tools/generate_translations.py` | `catalogue.json` + `derived_entities.json` | `translations/en.json`, `translations/de.json` (entity names + enum state labels) |
| 4. Reference doc | `python tools/generate_reference_doc.py` | `catalogue.json` + `descriptor_metadata.py` | `docs/reference/telematics-fields.md` |
| 5. British English | `python tools/generate_en_gb.py` | `translations/en.json` + `spelling_en_gb.json` (our curated US/UK word list) | `translations/en-GB.json` (a delta, not a full language) |

To rename an entity, edit its `title_en` in `tools/curated_titles.json` and
re-run steps 1–5. (`curated_titles.json` is project-authored, not a BMW export.)

## Dialect

`en.json` is **US English**, and `en-GB.json` is generated from it. BMW is why
this needs saying: the descriptor paths in its catalogue are US
(`vehicle.chassis.axle.row1.wheel.left.tire.pressure`) while the `element_en`
titles in the same export say "tyre", so the two spellings arrive together and
`en_name()` falls back to `element_en` for any descriptor without a curated
title. US wins because the entity IDs users type are `tire_*`.

British English is a **delta**: Home Assistant loads `en` and overlays the
requested language on top of it key by key (`helpers/translation.py`), so
`en-GB.json` holds only the ~75 strings that differ rather than 1300 duplicated
ones — duplicates nothing would regenerate. The bundled card's `t()` falls back
the same way, so `TRANSLATIONS["en-GB"]` there is four keys.

`tools/spelling_en_gb.json` is the single list of US/UK pairs, and it is
deliberately a word list rather than suffix rules: `meter` is the device in all
four places it appears in `en.json` (wallbox, house, grid) and spelled the same
either side of the Atlantic, so an `-er` → `-re` rule would render every one of
them as "metre". The file's `_deliberately_absent` block records those
exclusions and `tests/test_translations_dialect.py` checks the map and that
reasoning still agree.

Entities with **no BMW descriptor** — the integration's own derived and
diagnostic sensors, the device tracker, the vehicle image — are named from
`tools/derived_entities.json` (also project-authored) and merged into the same
generated `entity` block, so they stay bilingual instead of carrying a hardcoded
English `_attr_name`. Add a key there whenever you add such an entity, keep its
`_attr_translation_key` identical, and re-run steps 3 and 5; `tests/test_catalogue.py`
fails if a literal translation key has no entry, if a key collides with a
descriptor's, or if a German name is missing.

`keys.py` (shipped in the integration, not a tool) derives the Home Assistant
`translation_key` from a descriptor and is shared by the generators and the
runtime entities so they can never drift.

`units.py` is shared the same way, and for the same reason. **Step 2 stops with
an error if the catalogue uses a unit it does not recognise** — that is
deliberate, not an obstacle to work around. A descriptor's device class and
state class are derived from its unit string by an exact match, so an
unrecognised spelling (`kpa` for `kPa`, say) silently produces a sensor with no
device class, no unit conversion and no long-term statistics, and nothing in the
output looks wrong afterwards. Add the spelling to `UNIT_ALIASES` in
`custom_components/bavariandata/units.py`, then check whether
`device_and_state_class` should classify it.

`tests/test_catalogue.py` checks the outputs stay consistent (every descriptor
has metadata + bilingual translations, enum options have labels, generators are
idempotent). Run `python -m pytest tests/test_catalogue.py`.

## Refreshing from BMW

Download a fresh catalogue export from the BMW CarData portal
(`.../public/cardata-telematic-catalogue`) as HTML, drop it in this folder as
`CustomerTelematicsDataCatalogue.html`, then re-run steps 1–4 and the tests.

The portal page renders it from a direct download URL, which is easier to script.
It is market-scoped — the locale in the path decides the language:

```bash
curl -s https://mybmwweb-utilities.api.bmw/de-at/utilities/bmw/api/cd/catalogue/file \
  -o tools/CustomerTelematicsDataCatalogue.html
```

Checked 2026-07-27: identical to the committed copy apart from indentation
(294 descriptors, 8 sections).

**The catalogue is the stream's contract, not the Swagger.** The Swagger files in
`docs/reference/` describe the REST API only — MQTT has no machine-readable spec.
The catalogue's `technicalDescriptor` is the shared vocabulary between the two:
REST container `technicalDescriptors` and MQTT payload keys are the same strings.

**The `streamable` flag.** The catalogue's last column (`col7`, *Streamingfähig*)
marks which descriptors BMW can actually put on the MQTT stream — 246 of 295 in
the current export. It is rendered as a tick glyph, so the value is in the
**class**, not the cell text:

```html
<td class="col7"><div class="false-tick"></div></td>
```

`clean()` strips tags, so reading the cell text yields `""` for every row. The
class is parsed separately by `parse_streamable()` and flows through
`catalogue.json` → `descriptor_metadata.py` → `descriptors.descriptors_for_sections()`,
which excludes non-streamable descriptors from everything stream-facing: the
cluster picker, the portal snippet, the stream activator and the coverage
self-test. **They still get entities** — several (state of health, charging level,
Condition Based Servicing) arrive over REST via a container instead — so the
filter belongs in the stream derivation, not in entity creation. A descriptor with
no readable tick (e.g. one present only in the English CSV) defaults to
streamable, which degrades to the old behaviour rather than hiding a working field.
