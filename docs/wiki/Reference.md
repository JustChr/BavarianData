# Deep reference

> 🇩🇪 [Deutsch](DE-Reference)

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

## Code quality

Home Assistant grades its own integrations on the
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale)
(Bronze, Silver, Gold, Platinum). It grades only integrations that ship with
Home Assistant, so no custom integration can be awarded a tier. BavarianData
follows the rules anyway and **assesses itself at Platinum**: every rule up to
Gold is met, and so are two of Platinum's three.

The claim is written down rule by rule in
[`quality_scale.yaml`](https://github.com/JustChr/BavarianData/blob/main/custom_components/bavariandata/quality_scale.yaml),
with a reason beside every exemption. Three of the rules are checked on every
change, in CI's *Home Assistant tests* job:

- **Test coverage:** both test suites together cover more than 95 % of every
  module on its own, not just of the total. The job fails if one drops below.
- **Strict typing:** the whole integration passes `mypy --strict` against a real
  Home Assistant.
- **Config flow tests:** setup, reauthentication, reconfiguration and every
  Configure screen run against a real Home Assistant.

Three rules are exempt, and the file says why:

- **Polling** doesn't apply: the car's data arrives on BMW's stream.
- **Discovery** doesn't apply: a BMW account lives in the cloud, with nothing on
  your network to find.
- **Platinum's async library rule:** the stream uses paho-mqtt, the same MQTT
  client Home Assistant's own MQTT integration uses.

## For contributors

The descriptor catalogue, metadata, translations, and the field reference are
all generated from BMW's exports by the pipeline in
[`tools/`](https://github.com/JustChr/BavarianData/tree/main/tools) — read
[tools/README.md](https://github.com/JustChr/BavarianData/blob/main/tools/README.md)
before hand-editing any generated file. To change an entity name, edit
`title_en` in `tools/curated_titles.json` and re-run the pipeline.
