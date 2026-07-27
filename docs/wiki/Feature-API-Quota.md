# API quota

BMW caps the CarData REST API at **50 requests per 24 hours per account**. The
integration tracks and enforces this itself.

## What spends quota

- Every manual **`fetch_*`** service call
  ([Services reference](Services-Reference)).
- The **daily refresh**: **2 requests every 24 h**, covering everything BMW
  cannot stream (see below).
- One-off fetches at setup (basic vehicle data, the vehicle image), which are
  not repeated because that data does not change.

## The daily refresh

BMW cannot stream 49 of its ~295 telematic fields — service demands, Condition
Based Servicing, check-control messages, tread wear, lifetime-consumption
counters, door-lock status. The REST API is their only route into Home
Assistant, so the integration fetches them once a day:

| Request | Covers |
| --- | --- |
| The telematics **container** | All 41 non-streamable fields the container endpoint can serve — **one request for the lot** |
| **Tyre diagnosis** | Tread wear and remaining mileage per wheel — its own endpoint, so its own request |

**2 of your 50 per day**, leaving 48 for manual fetches. Daily is deliberate:
these values move on the scale of days, not minutes.

## What does not

- The **MQTT stream** — the primary data path. All the streamed sensors, the
  charging/trip/health history, statistics and export are **free**.
- The **`get_*`** and `export_history` / `import_statistics` / `set_trip_class`
  services — they read or write the integration's own local store.

Prefer the stream. Poll only what the stream genuinely cannot carry, and cache
what's fetched (the vehicle image entity is cached across restarts for exactly
this reason).

## Watching it

An **API Quota Remaining** diagnostic sensor exposes how many of the 50 requests
are left, with `used`, `limit` and `next_reset` attributes.

If the quota is ever exhausted, a **repair issue** appears under **Settings →
Repairs** telling you when it resets. **Streaming data keeps flowing
throughout** — only REST fetches are paused until the window rolls over.
