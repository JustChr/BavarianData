# Clean install — BavarianData residual artifacts

This note lists every file, registry entry, database row, and flag the integration
(domain `bavariandata`) can leave behind. Use it to fully clean a Home Assistant
instance before testing a fresh install.

> **Read this first.** Removing the integration through the UI already cleans up
> most of it — `async_remove_entry` deletes the history store, the long-term
> statistics and the coverage store. The list below matters when you want a
> *genuinely* clean slate (a fresh-install test), or when a removal was partial or
> failed. **The one item that is not a `.storage` file is the long-term statistics
> rows in the recorder database** — leftovers there would silently distort a
> "fresh" install's energy history.

## Config entry data

Runtime state is stored on the config entry and persists until the entry is
removed:

- Credentials/tokens: `client_id`, `access_token`, `refresh_token`, `id_token`,
  `expires_in`, `scope`, `gcid`, `token_type`, `received_at`
- Bootstrap and runtime flags: `bootstrap_complete`, `vin`, `last_telematic_poll`
- HV container info: `hv_container_id`, `hv_descriptor_signature`
- Cached vehicle metadata: `vehicle_metadata`
- Selected stream clusters: `stream_sections`

## Options

All options live on the config entry and go away with it. Listed so you can tell
whether a "fresh" install is really fresh:

| Group | Keys |
| --- | --- |
| Diagnostics | `debug_log`, `trip_debug` |
| Hidden overrides | `mqtt_keepalive`, `diagnostic_log_interval` |
| Charging costs | `price_mode`, `price_fixed`, `price_entity`, `price_currency`, `grid_energy_entity`, `charging_loss_percent` |
| History | `history_retain_months`, `statistics_import` |
| Trips | `trip_work_zone`, `trip_geocode`, `trip_track` |

## .storage files

| File | Contents | Removed with the integration? |
| --- | --- | --- |
| `bavariandata_<entry_id>_history` | The whole history layer: charging sessions and trips (incl. any recorded route polylines) | Yes — `async_remove_entry` clears it |
| `bavariandata_<entry_id>_coverage` | Descriptor-coverage self-test results | Yes |
| `bavariandata_<entry_id>_request_log` | Rolling REST API quota log | **No — delete manually** |
| `bavariandata_vehicle_images` | Cached vehicle renders, base64 (shared across entries) | **No — delete manually** |

A cleared `Store` may linger as an empty file until HA compacts `.storage`; that is
harmless.

## Long-term statistics (recorder database)

The statistics backfill publishes **external statistics** outside the entity
registry, so they are **not** removed by deleting entities or devices:

- Ids follow `bavariandata:<suffix>_<vin>` (lowercased VIN), with up to three
  series per VIN: `charging_energy`, `driving_distance`, `charging_cost`.

`async_remove_entry` deletes these. If the integration was removed some other way
(directory deleted, entry force-removed, HA crashed mid-removal), clear them under
**Developer tools → Statistics**, which lists orphaned statistic ids and offers to
delete them. Stale rows here are the easiest way to invalidate a fresh-install
test, because they reappear in the Energy dashboard with no integration installed.

## Config-directory files

- `bavariandata_trip_capture.ndjson` — raw MQTT trip capture, written only while
  the `trip_debug` option is on (capped at 25 MB). **Never removed automatically —
  delete manually.** It contains raw GPS coordinates, so delete it once you have
  finished analysing a drive.

## Device registry

- One integration-level device: `("bavariandata", <entry_id>)`, named
  "CarData Debug Device".
- One device per VIN: `("bavariandata", <vin>)`, populated from basic vehicle data
  or stream payloads.

Delete both from *Settings → Devices & Services → Devices* for a clean slate.

## Entity registry

Entities are created dynamically from stream/telematics data and remain after
removal unless deleted manually.

- **Descriptor entities:** `sensor.<vin>_…` and `binary_sensor.<vin>_…`, generated
  from the BMW catalogue.
- **Diagnostics** (under "CarData Debug Device"): Stream Connection Status, Last
  Message Received, Last Telematics API Call, API Quota Remaining.
- **Derived sensors** (per VIN — these have no BMW descriptor behind them):
  - State Of Charge (Predicted on Integration side), New Extrapolation Testing
    sensor, Predicted charge speed
  - Charged Energy (Total), Charged Energy (Session)
  - Charging Cost (This Month), Charging Cost (Last Session), Charging Energy
    (This Month), Charging Cost per 100 km
  - Battery Health
  - Driving Distance (This Month)
- **Device tracker:** the per-VIN `device_tracker` Location entity.
- **Image:** the per-VIN vehicle `image` entity.

Note that the cost, battery-health and trip sensors only exist if the matching
feature was configured (a tariff, enough charges, trips recorded), so a shorter
list is not necessarily an incomplete cleanup.

Remove these from *Settings → Devices & Services → Entities* as needed.

## Services, webhooks & notifications

- Services are registered while any entry is loaded and disappear automatically
  once the last entry unloads: the `bavariandata.fetch_*` calls plus
  `get_charging_sessions`, `get_trips`, `get_driving_summary`, `set_trip_class`,
  `export_history`, `import_statistics`, `get_coverage_report` and
  `activate_stream_fields`.
- Guided setup registers a temporary webhook and an unauthenticated helper view
  (`/bavariandata/onboarding`). Both are torn down when the flow finishes or is
  aborted; nothing persists.
- Reauthentication failures raise a persistent notification with id
  `bavariandata_reauth_<entry_id>`; dismiss it manually if it is still visible.

## Runtime cache

While loaded, runtime data lives in `hass.data["bavariandata"][<entry_id>]`
(stream manager, session, quota manager, coordinator). It clears on unload —
useful to know when debugging.

## Fresh-install checklist

1. Remove the integration from the UI (this clears the history store, the
   statistics and the coverage store).
2. Delete lingering devices: the debug device and the per-VIN devices.
3. Delete lingering entities: descriptor sensors, binary sensors, diagnostics, the
   derived sensors listed above, the device tracker and the vehicle image.
4. Delete from `.storage`: `bavariandata_<entry_id>_request_log` and
   `bavariandata_vehicle_images`.
5. Check **Developer tools → Statistics** for orphaned `bavariandata:…` statistic
   ids and delete any that remain.
6. Delete `bavariandata_trip_capture.ndjson` from the config directory if trip
   debugging was ever enabled.
7. Dismiss any remaining reauth notifications.
8. Restart Home Assistant.

After these steps, reinstalling behaves like a true first-time setup.
