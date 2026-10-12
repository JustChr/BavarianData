# Energy dashboard & long-term statistics

> 🇩🇪 [Deutsch](DE-Feature-Energy-and-Statistics)

The recorded history is published into Home Assistant's own **long-term
statistics**, under this integration's `bavariandata:` namespace: charging
energy (kWh), charging energy at home (kWh), charging cost and driving
distance — one series of each per vehicle.

## Why external statistics

They're written as **external** statistics, so they can cover hours no entity was
alive for. That means charging from **before you installed this**, or from
**while Home Assistant was down**, still lands on the Energy dashboard.

## Adding to the Energy dashboard

**Settings → Dashboards → Energy → Individual devices**, and add the
**charging energy at home** series (`bavariandata:charging_energy_home_<vin>`).

Use that one, not the plain **charging energy** series or the **Charged Energy
(Total)** entity. Home Assistant treats an individual device as part of what your
house used. Those two count every charge, including a fast charge on the
highway, which would then show up as household use and can push your devices
above the house's own total. The home series uses the same rule as the
[energy mix](Feature-Charging-History-and-Cost#where-the-energy-came-from): a
car that reports no position counts as at home.

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
