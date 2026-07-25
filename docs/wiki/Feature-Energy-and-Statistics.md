# Energy dashboard & long-term statistics

The recorded history is published into Home Assistant's own **long-term
statistics**, under this integration's `bavariandata:` namespace: charging
energy (kWh), charging cost and driving distance — one series per vehicle.

## Why external statistics

They're written as **external** statistics, so they can cover hours no entity was
alive for. That means charging from **before you installed this**, or from
**while Home Assistant was down**, still lands on the Energy dashboard.

## Adding to the Energy dashboard

**Settings → Dashboards → Energy → Individual devices**, and add the series (or
the **Charged Energy (Total)** entity).

## A mirror, not a second archive

The statistics **mirror the store** rather than being a separate archive: each
rebuild regenerates the whole series from the records still held, so your
[retention setting](Settings-Reference#charging-costs--history) genuinely governs
everything kept.

It happens **automatically** as sessions and trips are recorded. To force a
rebuild:

- **`bavariandata.import_statistics`** — rebuilds from the store and reports the
  row counts. Useful after **restoring a backup**, or to verify the counts.

## Turning it off

Toggle **`statistics_import`** off under **Configure → Charging costs &
history**. This also **deletes the published series** — no stale mirror is left
lingering on the Energy dashboard.
