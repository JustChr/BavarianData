# Clean install — BavarianData residual artifacts

This note lists every file, registry entry, and flag the integration (domain
`bavariandata`) can leave behind. Use it to fully clean a Home Assistant instance
before testing a fresh install.

## Config entry data

Runtime state is stored on the config entry and persists until the entry is
removed:

- Credentials/tokens: `client_id`, `access_token`, `refresh_token`, `id_token`,
  `expires_in`, `scope`, `gcid`, `token_type`, `received_at`
- Bootstrap and runtime flags: `bootstrap_complete`, `vin`, `last_telematic_poll`
- HV container info: `hv_container_id`, `hv_descriptor_signature`
- Cached vehicle metadata: `vehicle_metadata`
- Options (only if set via the hidden overrides view): `mqtt_keepalive`,
  `diagnostic_log_interval`, `debug_log`

Removing the integration deletes the config entry, but the devices/entities and
`.storage` files below may remain.

## .storage files

- `bavariandata_<entry_id>_request_log` — rolling API-quota log written via
  `homeassistant.helpers.storage.Store`. Left behind unless deleted manually.
- The cached vehicle image is also persisted via `Store`; it is removed with the
  integration but may linger in `.storage` until HA compacts it.

## Device registry

- One integration-level device: `("bavariandata", <entry_id>)`, named
  "CarData Debug Device".
- One device per VIN: `("bavariandata", <vin>)`, populated from basic vehicle
  data or stream payloads.

Delete both from *Settings → Devices & Services → Devices* for a clean slate.

## Entity registry

Entities are created dynamically from stream/telematics data and remain after
removal unless deleted manually:

- Descriptor sensors (`sensor.<vin>_…`) and binary sensors
  (`binary_sensor.<vin>_…`).
- Diagnostics sensors under the "CarData Debug Device": Stream Connection Status,
  Last Message Received, Last Telematics API Call.
- SOC helpers per VIN (state-of-charge estimate and rate).
- The per-VIN vehicle `image` entity.

Remove these from *Settings → Devices & Services → Entities* as needed.

## Services & notifications

- Services are registered while any entry is loaded (`bavariandata.fetch_*`) and
  disappear automatically once the last entry unloads.
- Reauthentication failures raise a persistent notification with id
  `bavariandata_reauth_<entry_id>`; dismiss it manually if it is still visible.

## Runtime cache

While loaded, runtime data lives in `hass.data["bavariandata"][<entry_id>]`
(stream manager, session, quota manager, coordinator). It clears on unload —
useful to know when debugging.

## Fresh-install checklist

1. Remove the integration from the UI.
2. Delete lingering devices (the debug device and per-VIN devices).
3. Delete lingering entities (descriptor sensors, binary sensors, diagnostics,
   SOC helpers, vehicle image).
4. Delete `bavariandata_<entry_id>_request_log` from `.storage`.
5. Dismiss any remaining reauth notifications.

After these steps, reinstalling behaves like a true first-time setup.
