# API quota

> 🇩🇪 [Deutsch](DE-Feature-API-Quota)

BMW caps the CarData REST API at **50 requests per 24 hours per account**. The
integration tracks and enforces this itself.

## What spends quota

- Every manual **`fetch_*`** service call
  ([Services reference](Services-Reference)).
- The **daily refresh**: **3 requests every 24 h per vehicle**, covering
  everything BMW cannot stream (see below).
- One-off fetches at setup (basic vehicle data, the vehicle image), which are
  not repeated because that data does not change.
- Setting up the telematics container, and replacing it (**Reset telemetry
  container**, or after an update that changes what it holds): one request each
  to list, create and delete one, so a reset costs about three. BMW counts these
  too.

## The daily refresh

BMW cannot stream 49 of its ~295 telematic fields — service demands, Condition
Based Servicing, check-control messages, tread wear, lifetime-consumption
counters, door-lock status. The REST API is their only route into Home
Assistant, so the integration fetches them once a day:

| Request | Covers |
| --- | --- |
| The telematics **container** | 40 of the 41 non-streamable fields the container endpoint can serve — **one request for the lot** |
| The **service items** container | Condition Based Service alone. Since 16 Oct 2026 BMW refuses any container that holds it, and with it the whole fetch, so it has a container of its own — one request |
| **Tire diagnosis** | Tread wear and remaining mileage per wheel — its own endpoint, so its own request |

All three are per vehicle, so the refresh costs **3 requests per car**: 3 a
day for one car, 6 for two, 9 for three — out of 50. Daily is deliberate: these
values move on the scale of days, not minutes.

## What does not

- The **MQTT stream** — the primary data path. All the streamed sensors, the
  charging/trip/health history, statistics and export are **free**.
- The **`get_*`** and `export_history` / `import_statistics` / `set_trip_class`
  services — they read or write the integration's own local store.

Prefer the stream. Poll only what the stream genuinely cannot carry, and cache
what's fetched — the vehicle image and the tire diagnosis are both kept across
restarts for exactly this reason, so a reboot never costs a request to get back
what was already fetched.

## Watching it

An **API Quota Remaining** diagnostic sensor exposes how many of the 50 requests
are left, with `used`, `limit` and `next_reset` attributes.

If the quota is ever exhausted, a **repair issue** appears under **Settings →
Repairs** telling you when it resets. **Streaming data keeps flowing
throughout** — only REST fetches are paused until the window rolls over.
