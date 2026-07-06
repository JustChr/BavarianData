# Investigation: do granular streaming scopes provision the MQTT stream?

**Status: CLOSED — Outcome B (portal-only).** Requesting granular
`cardata:streaming:vehicle.*` scopes at BMW's device-code endpoint is rejected
with `400 invalid_request`, both when they replace `cardata:streaming:read`
(Run 1) and when they are appended to it (Run 2). BMW does **not** let a client
request per-descriptor streaming entitlements; the stream selection is made in
the portal's Data Selection page and the token's "dynamic scopes" only reflect
it. The follow-up is therefore a **curated portal snippet**, not scopes.

## Why this matters

Entities are created from whatever descriptors actually arrive on the MQTT
stream, and BMW only streams descriptors the user selected. There are two
possible gates for that selection:

1. **Portal "Data Selection"** (`Datenauswahl ändern`) — ticking checkboxes in
   the BMW customer portal. This is documented and known to work, but there is
   **no REST API** for it; it is browser-only.
2. **Granular OAuth scopes** — BMW's streaming guide mentions per-token
   *"dynamic scopes"*, and each descriptor has a matching
   `cardata:streaming:<descriptor>` scope. The integration currently requests
   only the coarse `cardata:streaming:read`.

**If requesting granular scopes at device-registration time actually provisions
the stream** (outcome A), the integration can enable exactly the chosen clusters
with no portal clicking. **If the scopes merely *reflect* the portal selection or
are rejected** (outcome B), we keep steering users to the portal and only use the
cluster selection to drive a curated portal snippet.

## How the code supports the experiment

The cluster picker is the experiment vehicle. **Configure → Choose streamed
data** stores the selected clusters on the entry (`stream_sections`) and
re-authorizes. During that re-auth,
[`scope_for_entry_data()`](../../custom_components/bavariandata/config_flow.py)
turns the selection into a scope string via
[`descriptors.build_scope()`](../../custom_components/bavariandata/descriptors.py):

```
authenticate_user openid cardata:api:read
cardata:streaming:vehicle.<descriptor>   # one per descriptor in the clusters
```

Entries that never touch the picker keep requesting `DEFAULT_SCOPE`, so existing
installs are unaffected while the question is open.

## Procedure

1. **Baseline — decode the current token.** From a working install, take the
   stored `id_token` (or `access_token`) and base64-decode the JWT payload.
   Inspect its `scope` claim. Record whether it contains
   `cardata:streaming:vehicle.*` entries (and whether they mirror your portal
   Data Selection) or just `cardata:streaming:read`.

2. **Pick an isolating cluster.** In the BMW portal, make sure the descriptors of
   one small cluster (e.g. **Tire data**, 12 descriptors) are **NOT** ticked in
   Data Selection. This isolates "scope" from "portal selection."

3. **Request granular scopes.** In Home Assistant: **Configure → Choose streamed
   data**, select *only* that cluster, submit, and approve the re-auth on BMW's
   site. (Enable `debug_log` first — the device-code request logs the requested
   scope and the token response logs the granted `scope`.)

4. **Observe three things:**
   - Did the device-code endpoint **accept** the granular scope, or reject it?
   - What `scope` did BMW **grant back** in the token response? (all requested
     descriptors? a subset? just `cardata:streaming:read`?)
   - Does the **stream deliver** those descriptors within a few minutes / after
     an app-triggered update, even though they were never ticked in the portal?
     (watch for new `sensor.*`/`binary_sensor.*` entities for that cluster.)

## Result (in progress)

### Run 1 — granular scopes *replacing* `cardata:streaming:read` (v0.3.9)

Requested `authenticate_user openid cardata:api:read cardata:streaming:vehicle.<tire…>`
(no coarse `cardata:streaming:read`). BMW's device-code endpoint **rejected it**:

```
400 invalid_request: The request is missing a required parameter, includes an
unsupported parameter value (other than grant type), repeats a parameter, …
```

`invalid_request` (not `invalid_scope`) points at the request shape rather than
the scope values — the leading hypothesis is that BMW requires
`cardata:streaming:read` to be present in any streaming authorization. Fixed in
v0.3.10: the granular scopes are now **appended to** `cardata:streaming:read`
rather than replacing it, and a rejected scope automatically falls back to
`DEFAULT_SCOPE` so authentication can never break.

### Run 2 — granular scopes *in addition to* `cardata:streaming:read` (v0.3.10)

Requested `authenticate_user openid cardata:api:read cardata:streaming:read
cardata:streaming:vehicle.<tire…>`. BMW's device-code endpoint **still rejected
it** with the same `400 invalid_request`, and the flow fell back to
`DEFAULT_SCOPE` (auth recovered). So the coarse read scope was not the issue —
BMW simply does not accept granular streaming scopes here.

| Question | Finding |
| --- | --- |
| Device-code accepts `read` + granular? | **No — 400 invalid_request** |
| **Conclusion** | **B: portal-only** |

## Outcome B — plan

Granular scopes are a dead end. The "Choose streamed data" picker **no longer
requests scopes / re-authorizes**; instead it generates a curated browser-console
snippet that ticks exactly the selected clusters' descriptors on the portal's
Data Selection page.

Resolved portal DOM (from a real page): the Data Selection table's first column is
**"Technischer Beschreiber"** — the raw descriptor. Each checkbox `<label
class="chakra-checkbox">` sits in a cell (`div.css-k008qs`) next to a `<p>` holding
the descriptor, and the label itself has no text. So the snippet reads each
checkbox cell's text and matches it **exactly** against the selected clusters'
descriptor set (no display-name matching needed). Shipped in v0.3.12.

## Next step per outcome

- **Outcome A** — make granular scopes the norm: default the picker into the
  initial config flow, and drop the portal-snippet instructions from the README.
- **Outcome B** — keep the picker, but instead of (or in addition to) scopes,
  generate a curated portal snippet from the selected clusters that ticks exactly
  those descriptors' checkboxes, and surface it in the picker step + README.
