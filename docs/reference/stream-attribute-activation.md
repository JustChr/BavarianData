# Stream attribute activation — the portal `/streams` endpoint

**Status: implemented (session-injected).** Stream *attribute selection*
("Datenauswahl" / stream setup) has **no CarData API** — see
[`stream-scope-investigation.md`](stream-scope-investigation.md), which closed
the "granular OAuth scopes" avenue as **portal-only**. This document records the
*actual backend request* the portal's stream-setup page makes, reverse-engineered
from a real HAR (`www.bmw.at`, 2026-07-25), which
[`stream_activation.py`](../../custom_components/bavariandata/stream_activation.py)
now replays.

This is **not** a new auth avenue: the endpoint is on the **market portal**
(`www.bmw.at`, `www.bmw.de`, …), authenticated by the **interactive browser
session**, and is unrelated to the CarData API the integration talks to with its
OAuth token (`api-cardata.bmwgroup.com`). It cannot be driven by the integration's
token, and the session cannot be minted or refreshed headlessly (BMW's Akamai
bot-defense cookies). Hence: **session-injected, manual, occasional.**

## The request

```
POST {base_url}/{locale}/utilities/bmw/api/cd/streams/{mapped_vehicle_id}
Accept: application/json
Content-Type: application/json
Origin: {base_url}
Referer: {base_url}/{locale}/mybmw/mapped-vehicle/{mapped_vehicle_id}/cardata/stream-setup
x-ocp-stage: prod
Cookie: <browser session — gcdmToken, mybmwUserLogin, ak_bmsc, bm_sv, …>

{"attributes": ["vehicle.isMoving", "vehicle.electricalSystem.battery.voltage", …]}
```

Returns **`201 Created`** with:

```json
{"data": {"attributes": [ …the full list… ]}, "success": true, "status": …, "message": …}
```

### Contract notes (differences worth flagging)

- **Body is flat** `{"attributes": [...]}`. It is *not* wrapped in
  `{"data": {...}}` — that wrapper appears only in the **response**. (Some
  community write-ups show the wrapped form; the wire request is flat.)
- **Set / replace.** The posted list becomes the entire selection; the response
  echoes it back. Re-posting the same list is therefore idempotent from our side
  (we dedupe + sort first, and skip a no-op via a read).
- **Host + locale are market-specific** (`www.bmw.at` + `de-at`,
  `www.bmw.de` + `de-de`, …) — not a single global host.
- **`mapped_vehicle_id` is a portal hash, not the VIN.** It comes from the
  stream-setup URL. `…/mapping-details` does not even contain the VIN, so there
  is no headless VIN → mapped-id bridge; the id must be read from the portal.
- **Auth is cookies only** — there is **no `Authorization: Bearer`** header. The
  cookie jar includes Akamai bot-defense cookies (`ak_bmsc`, `bm_sv`, `bm_mi`,
  `AKA_A2`) that a headless client cannot generate.
- **Browser-shaped headers are mandatory.** Akamai *stalls* (connection hangs, no
  response — not a 4xx) a request that omits the `Sec-Fetch-Dest/Mode/Site`,
  `Accept-Language` and `Accept-Encoding` headers. Verified live: the minimal
  `Accept`/`Content-Type`/`Origin`/`Referer`/`x-ocp-stage` set times out; adding
  the browser set returns `200`/`201`. `PortalSession.headers()` sends them.
  `Accept-Encoding` is capped at `gzip, deflate` (not the browser's `br, zstd`)
  so aiohttp can always decode the body. A valid captured cookie *does* work from
  a plain aiohttp client once the headers match — no TLS/JA3 spoofing needed.

### Companion read endpoint

```
GET {base_url}/{locale}/utilities/bmw/api/cd/streams/{mapped_vehicle_id}?includeAttributes=true
```

Returns the currently selected `data.attributes` plus MQTT connection facts
(`host`, `port`, `topic`, `username`, `creationTime`). Used for read-before-write
so an unchanged selection skips the POST.

## Guided onboarding (the whole chain in one snippet)

The same route carries the *entire* onboarding, not just `/streams`:

- `GET /utilities/bmw/api/cd/applications` → the registered API client(s):
  `credentials[].apikey` is the **Client ID** the device flow needs (its scopes
  are the familiar `cardata:api:read openid cardata:streaming:read
  authenticate_user`). This is what users otherwise create and copy by hand.
- `GET /mybmw/api/mapped-vehicle/{id}/mapping-details` → `mappingStatus`,
  `subscriberStatus` (PRIMARY/SECONDARY), `isElectricOrHybrid` — mapping/consent
  verification.
- `POST …/cd/streams/{id}` → activate the chosen fields (above).

[`onboarding.py`](../../custom_components/bavariandata/onboarding.py) builds a
single **in-browser** snippet (`build_onboarding_snippet`) that runs these
same-origin on the portal's stream-setup page — so the httpOnly session is used
in place, the browser sets the Akamai-required headers, and **no secret leaves
the browser**. It prints a compact, non-secret result blob (client id · mapped
vehicle · activation count) that `parse_onboarding_result` decodes back in the
config flow (`async_step_guided…`). This is why guided onboarding is robust where
the cookie-into-HA service is throttled: it never handles the session at all.

The **same activator is reused when reconfiguring** the stream selection later —
**Configure → Choose streamed data** (`CardataOptionsFlowHandler.
async_step_action_select_clusters` → `activate_stream_wait`/`_paste`/`_done`).
The helper-page + webhook plumbing lives in the shared `_StreamActivatorFlow`
mixin (`config_flow.py`), so first-time setup and reconfiguration drive one
activator. The activator is **additive** (it unions the wanted fields with the
current selection and never removes one), so reconfiguring only ever widens the
stream; unticking a field to stop it streaming is still a portal Data Selection
action.

## How the integration uses it

- Module: [`stream_activation.py`](../../custom_components/bavariandata/stream_activation.py)
  — HA-free, unit-tested (`tests/test_stream_activation.py`).
  - `PortalSession(base_url, locale, cookie)` carries the captured auth context.
  - `PortalStreamClient.async_set_stream_attributes(mapped_vehicle_id, attributes)`
    dedupes + sorts, reads current, replaces if changed, validates `201` +
    `success`, and logs **requested vs. accepted counts** (never the cookie).
  - `DEFAULT_STREAM_ATTRIBUTES` is derived from the default clusters via
    `descriptors.descriptors_for_sections`, so it can't drift from the catalogue.
- Service: `bavariandata.activate_stream_fields` (see
  [Services-Reference](../wiki/Services-Reference.md#activate_stream_fields)).

## Why not automate the session?

Replicating the portal web login server-side, or driving a headless browser,
would be needed to obtain the cookies — both are out of scope, fragile
(2FA/captcha), and defeated by Akamai bot-defense. The snippet/checkbox flow in
[Getting-Started-4-Choose-Data](../wiki/Getting-Started-4-Choose-Data.md) remains
the no-capture path.
