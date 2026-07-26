# Troubleshooting & FAQ

## Onboarding fails with "access denied"

<a id="onboarding-fails-with-access-denied"></a>

BMW's device-authorization backend can return `access_denied` ("The user has
declined authorization") **even though you never saw a consent page and the
login clearly worked**. This is flakiness on BMW's side, not the integration —
BMW support has confirmed (in response to a ticket) that the device-code flow is
handled by an internal partner system with "sync problems." There is nothing the
integration can do to fix it.

It can take a few attempts. This sequence has worked reliably (multiple times)
for others:

1. Open a **fresh incognito/private browser window**.
2. Go to the My BMW / CarData portal **manually** — do **not** use the
   pre-filled complete link, and strip any `?user_code=…` from the URL.
3. Sign in, open **Authenticate device** ("Gerät authentifizieren"), then
   **type the user code by hand** (make sure no "incorrect code" banner appears)
   and approve. You'll typically land on a "Login successful" / "continue in the
   car" screen — **you don't need to do anything in the car**; that screen is
   just confirmation. Go straight back to Home Assistant and press **Submit**.
4. If the code timed out meanwhile, press **Submit** in Home Assistant for a
   fresh code and repeat.
5. Still failing after several tries? **Delete the client** in the BMW portal,
   create a new one (tick **both** subscriptions), and redo auth with the new
   client ID.

If none of that helps, **stop retrying and wait**. This is genuinely a lottery
on BMW's side. Many users only got through after leaving it alone for several
hours to a couple of days (sometimes it starts working overnight with no further
action); rapid retries don't seem to speed it up. There's no single trick that
works for everyone. For the hardest cases the only lever left is BMW support:
**bmwcardata-b2c-support@bmwgroup.com** (include your reference ID from the
error, if BMW showed one).

## No data arriving

<a id="no-data-arriving"></a>

1. Make sure you completed [step 4](Getting-Started-4-Choose-Data) — descriptors
   must be ticked **and saved** in the portal's Data Selection.
2. Trigger a lock/unlock in the MyBMW app to prompt the car to send an update.
3. Run **`bavariandata.get_coverage_report`**
   ([Services](Services-Reference#get_coverage_report)) — it tells you exactly
   which expected descriptors haven't arrived, so you can tell a selection
   problem from a car-doesn't-send-it problem.

If nothing streams for **48 hours**, a repair issue appears under **Settings →
Repairs** pointing back here.

## Stream authorization failing (MQTT rc=5)

<a id="stream-authorization-failing"></a>

If the stream is rejected as **unauthorized** (`MQTT rc=5`) and stays that way,
a repair issue appears under **Settings → Repairs**. The integration retries and
re-authorizes on its own first; the repair only appears once that hasn't
recovered it for a while. Two usual causes:

1. **Stale tokens.** Run **Configure → Refresh tokens now**. If it keeps
   failing, **Configure → Re-authorize with BMW** (the refresh token expires
   roughly every two weeks — see
   [access denied](#onboarding-fails-with-access-denied) if the re-auth itself
   is rejected).
2. **Another client holds the stream.** BMW allows only
   [one stream per account](#only-one-stream-per-account) — disconnect any other
   CarData client, then reload the integration.

## Only one stream per account

<a id="only-one-stream-per-account"></a>

BMW allows a **single concurrent streaming client** per account (GCID), so no
other tool can be connected at the same time. If another CarData client is
running, disconnect it.

## The car can't be controlled

**Read-only.** CarData cannot send commands, so this integration can't lock,
precondition, or otherwise control the car. Automations act on external devices
in response to the car's data — see [Events & blueprints](Feature-Automations).

## Debug logging

Off by default. Turn it on in **Configure → Debug logging** and reload. It's
verbose and can include vehicle data such as **GPS and VIN**, so leave it off
unless you're chasing a problem. (Separate from Home Assistant's generic
per-integration log level.)

## Capturing a drive for trip detection

<a id="capturing-a-drive-for-trip-detection"></a>

If trips are being missed, split or mis-measured, a capture of one drive gives
us the raw data to improve detection. Turn on **Trip-capture diagnostics** under
**Configure → Trips** (independent of Debug logging), take a normal drive that
reproduces the problem, then **turn it back off**.

For the richest capture, first **select as many data fields as you can** in the
BMW portal's Data Selection ([Choose data](Getting-Started-4-Choose-Data)) —
especially the navigation/GPS, speed and drivetrain fields. The capture can only
show what the car actually streams, and part of what we're looking for is which
extra signals (GPS fix quality and satellite count, heading, speed, HV-system
and door/lock state) your car sends while driving.

It produces two things:

- **Log lines** tagged for easy filtering — grep the Home Assistant log for
  `[trip.`:
  - `[trip.gps]` — every GPS fix with its step, inter-fix gap, stream latency,
    odometer, SoC, the close-timer countdown, **and GPS fix state / satellite
    count / heading** (so a "no movement" run can be told apart from a lost fix).
  - `[trip.timer]` — when the stationary-close timer arms and fires.
  - `[trip.door]` — driver-door start/end refinements (when the car streams the
    door).
  - `[trip.seg]` — each BMW trip-segment batch in full, with timestamps.
  - `[trip.watch]` — a curated set of candidate "is it driving?" signals (speed,
    HV-system state, ignition, driver door/lock, active navigation) whenever the
    car streams them.
  - `[trip.raw]` — every other descriptor each message carried.
  - `[trip.post]` — a one-line summary per closed trip (distances side by side,
    fix counts, largest GPS gap).
- **A capture file** `bavariandata_trip_capture.ndjson` in your Home Assistant
  **config folder** — one JSON record per message, so a drive can be replayed
  offline while testing detector changes.

Both contain **GPS coordinates and your VIN**. Share them only with the
maintainers (e.g. attached to a GitHub issue you're comfortable making), and
delete the capture file when you're done. It stops growing at ~25 MB.

## The card doesn't show up after an update

**Hard-refresh the browser** — the bundled card is cached aggressively.

## Quota exhausted

<a id="quota-exhausted"></a>

If you've spent all [50 REST requests](Feature-API-Quota) in 24 h, a repair
issue appears under **Settings → Repairs** telling you when it resets.
**Streaming keeps flowing throughout** — only `fetch_*` calls pause.

## Where to get help

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).
