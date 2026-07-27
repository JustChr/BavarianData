# BMW CarData reference material

These are **BMW-provided source artifacts**, kept for reference and used to
generate parts of the integration (e.g. descriptor titles and streaming scopes).
They are not authored by this project and are reproduced here verbatim.

[`telematics-fields.md`](telematics-fields.md) is the **generated, human-readable
field reference** — every descriptor grouped by BMW's own sections, with unit,
default-enabled state and description. It is produced from the catalogue by
[`tools/generate_reference_doc.py`](../../tools/generate_reference_doc.py).

| File | What it is | BMW version |
| --- | --- | --- |
| `bmw-cardata-api-guide.md` | BMW's Integration Guide, complete (registration + REST + streaming). | 1.5 (09/01/2026) |
| `bmw-cardata-api-reference.md` | Sections 1–3 of the same guide: registration and the REST API. | 1.5 (09/01/2026) |
| `bmw-cardata-streaming-guide.md` | Section 4 of the same guide: the MQTT stream. | 1.5 (09/01/2026) |
| `customer-api.swagger.json` | OpenAPI/Swagger spec for the CarData customer API. | 1.0.0 |
| `device-flow.swagger.json` | OpenAPI/Swagger spec for the OAuth device-flow endpoints. | 1.6 |

Authoritative, up-to-date documentation lives at
<https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction>.

## Refreshing these files

Both pages are JavaScript apps, so fetching the page URL yields an empty shell.
The underlying artifacts are plain files (last checked 2026-07-27):

```bash
base=https://bmw-cardata.bmwgroup.com/customer
curl -s "$base/api/public/content/download-content-file" -o guide.html      # Integration Guide
curl -s "$base/public/assets/swagger/swagger-customer-api-v1.json" -o customer-api.swagger.json
curl -s "$base/public/assets/swagger/swagger-device-code-flow.json" -o device-flow.swagger.json
```

The guide comes back as HTML-in-markdown; the version and date are in the
`Versioning` block at the top — check that against the table above to see whether
anything moved.
