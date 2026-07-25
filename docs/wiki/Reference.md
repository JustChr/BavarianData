# Deep reference

Technical reference material, kept in the repository and (for the field
catalogue) generated from BMW's own exports so it can't drift.

## Descriptor / field catalogue

- **[telematics-fields.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/telematics-fields.md)**
  — every descriptor BMW can stream, grouped by cluster, with the HA entity it
  maps to. This is the authoritative field-per-cluster breakdown referenced from
  [step 4](Getting-Started-4-Choose-Data).

## BMW API notes

- **[bmw-cardata-api-guide.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-api-guide.md)**
  — how the REST API is used.
- **[bmw-cardata-streaming-guide.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-streaming-guide.md)**
  — the MQTT stream.
- **[bmw-cardata-api-reference.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/bmw-cardata-api-reference.md)**
  — endpoint reference.
- Swagger: [customer-api](https://github.com/JustChr/BavarianData/blob/main/docs/reference/customer-api.swagger.json)
  · [device-flow](https://github.com/JustChr/BavarianData/blob/main/docs/reference/device-flow.swagger.json).

## Design investigations

- **[stream-scope-investigation.md](https://github.com/JustChr/BavarianData/blob/main/docs/reference/stream-scope-investigation.md)**
  — why per-descriptor streaming scopes don't work and Data Selection is
  portal-only.

## For contributors

The descriptor catalogue, metadata, translations, and the field reference are
all generated from BMW's exports by the pipeline in
[`tools/`](https://github.com/JustChr/BavarianData/tree/main/tools) — read
[tools/README.md](https://github.com/JustChr/BavarianData/blob/main/tools/README.md)
before hand-editing any generated file. To change an entity name, edit
`title_en` in `tools/curated_titles.json` and re-run the pipeline.
