# API quota

BMW caps the CarData REST API at **50 requests per 24 hours per account**. The
integration tracks and enforces this itself.

## What spends quota

- Every manual **`fetch_*`** service call
  ([Services reference](Services-Reference)).
- On-demand background fetches (basic vehicle data, tyre diagnosis, the vehicle
  image, …).

## What does not

- The **MQTT stream** — the primary data path. All the streamed sensors, the
  charging/trip/health history, statistics and export are **free**.
- The **`get_*`** and `export_history` / `import_statistics` / `set_trip_class`
  services — they read or write the integration's own local store.

Prefer the stream. Never add polling that burns quota; cache what's fetched (the
vehicle image entity is cached across restarts for exactly this reason).

## Watching it

An **API Quota Remaining** diagnostic sensor exposes how many of the 50 requests
are left, with `used`, `limit` and `next_reset` attributes.

If the quota is ever exhausted, a **repair issue** appears under **Settings →
Repairs** telling you when it resets. **Streaming data keeps flowing
throughout** — only REST fetches are paused until the window rolls over.
