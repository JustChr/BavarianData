# Coming from bimmer_connected (MyBMW / ConnectedDrive)

> 🇩🇪 [Deutsch](DE-Coming-from-bimmer_connected)

If your BMW or MINI used to appear in Home Assistant through the **BMW Connected
Drive** integration and has shown nothing but `unavailable` since autumn 2025,
this page is for you. It explains what changed, what replaces it, and how to move
your dashboards and automations across.

## What happened

The Connected Drive integration was built on the `bimmer_connected` library,
which spoke to the servers behind the MyBMW app. On **29 September 2025** BMW
blocked third parties from making those requests. The library's own README now
describes it as deprecated and non-functional, and no setting or update on your
side brings it back.

## What replaces it: BMW CarData

**BMW CarData** is BMW's own, official way for a customer to read their car's
data. You create a personal **client ID** in the BMW (or MINI) portal, authorize
it once, and from then on the car's data reaches you two ways:

- a **live MQTT stream**, which pushes each value within seconds of the car
  sending it — charge level, doors, windows, tire pressures, position, trip
  data;
- a **REST API** for on-demand snapshots, limited to 50 requests per 24 hours.

BavarianData is a Home Assistant integration that talks to both directly. There
is no server in between: Home Assistant holds the tokens and is the only client.
It is installed from the **HACS default store**.

## What changes for you

| | Connected Drive (bimmer_connected) | BavarianData (BMW CarData) |
| --- | --- | --- |
| Status | Blocked by BMW since 29 Sep 2025 | Uses BMW's official customer data service |
| How you sign in | MyBMW username and password | A personal client ID, authorized once in the browser — no password is stored in Home Assistant |
| How data arrives | Polled every few minutes | Pushed live over MQTT |
| Remote commands (lock, climate, flash lights, start charging) | Yes | **No** — CarData is read-only |
| Where it works | Wherever the MyBMW app worked | Where BMW offers the CarData portal ([more](Getting-Started-1-BMW-Portal-Setup)) |
| Entity names | Its own | New ones — automations must be re-pointed |

The one real loss is **remote commands**. CarData has no command interface at
all, so no CarData-based integration can lock the car or start the climate. Use
the MyBMW app for those.

What you gain on top of the raw data: a [charging history with
cost](Feature-Charging-History-and-Cost), a [trip
journal](Feature-Trips), learned [battery health](Feature-Battery-Health), a
[real range](Feature-Efficiency-and-Range) measured from your own driving,
[Energy dashboard statistics](Feature-Energy-and-Statistics),
[CSV/HTML export](Feature-Export), and an [evcc / wallbox
bridge](Feature-evcc-and-Wallbox-Bridge). All of it is computed from the
stream, so none of it spends API quota.

## Moving across

1. **Remove the old integration.** Under **Settings → Devices & Services**,
   delete the *BMW Connected Drive* entry. It no longer receives data, and
   removing it frees the device and entity names.
2. **Set up BavarianData** by following the four setup steps, starting with
   [BMW portal setup](Getting-Started-1-BMW-Portal-Setup). Most of the work is
   in the BMW portal.
3. **Add the dashboard card.** See [The dashboard card](The-Dashboard-Card).
4. **Re-point your automations.** Search your automations, scripts and
   dashboards for the old entity ids and swap in the new ones. The table below
   gives the usual equivalents; [Entities & devices](Feature-Entities-and-Devices)
   lists everything.

### Common equivalents

| You used (Connected Drive) | Use now (BavarianData) |
| --- | --- |
| Remaining battery percent | **Battery HV State Of Charge** |
| Remaining range (electric) | **Range EV Remaining range** — or **Real Range**, measured from your own driving |
| Mileage | **Vehicle mileage** |
| Charging status | **Charging EV Charging state** |
| Charging target | **Battery EV Target state of charge** |
| Plug / connection status | **Charging Port plug state** |
| Door lock state | **Doors overall state** — it follows every lock and unlock; see [which lock entity to use](Feature-Entities-and-Devices#which-lock-entity-to-use) |
| Doors, windows, lids | One binary sensor per door, window, hood and tailgate |
| Check control messages | **Service Check control messages** |
| Condition based services | **Service Condition based services** |
| Device tracker | **Location** (`device_tracker`) |

## Questions people ask

**Is this the same as the old integration under a new name?** No. It is a
separate integration, built from scratch on a different BMW service, so your old
entity ids do not carry over.

**Does it work for MINI?** Yes. MINI cars use the same CarData service through
the MINI portal.

**Does it work outside Europe?** It works wherever BMW offers the CarData portal
for your account. Not every market has it yet, but the client ID is
account-wide — [step 1](Getting-Started-1-BMW-Portal-Setup) has the details.

**Can I run it next to another CarData integration?** BMW allows **one stream
per account** at a time, so two stream clients on the same account take turns
dropping each other. Pick one.
