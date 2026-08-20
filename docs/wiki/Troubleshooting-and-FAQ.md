# Troubleshooting & FAQ

## Onboarding fails with "access denied"

<a id="onboarding-fails-with-access-denied"></a>

BMW's device-authorization backend can return `access_denied` ("The user has
declined authorization") **even though you never saw a consent page and the
login clearly worked**. This is flakiness on BMW's side, not the integration —
BMW support has confirmed (in response to a ticket) that the device-code flow is
handled by an internal partner system with "sync problems." There is nothing the
integration can do to fix it.

> **Update, 2026-07-28 — BMW says this is fixed.** BMW notified several projects
> that the partner-system sync problem has been resolved, and confirmed it by
> email to users who had raised tickets. Most affected people got through
> immediately afterwards. **Affected clients are not repaired in place**: BMW's
> instruction is to **delete the CarData client in the portal, create a new one**
> (tick both subscriptions), and run the device flow again with the new client
> ID. If you have been stuck for weeks, do that first — before working through
> the ritual below.

If it still fails, two things outside the flow itself have repeatedly turned out
to be the cause:

- **MyBMW app privacy settings.** Several people only got through after setting
  the profile's privacy/data-sharing settings to **all** in the MyBMW app and
  then **starting the car once** so the change is applied. You can turn them back
  down afterwards.
- **Ad blockers.** AdGuard, Pi-hole and friends can break the portal's approval
  page. Disable them for the attempt.

Beyond that it can take a few attempts. This sequence has worked reliably
(multiple times) for others:

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

## Download diagnostics

<a id="download-diagnostics"></a>

The fastest way to turn "it doesn't work" into an answer. Go to **Settings →
Devices & Services → BavarianData → ⋮ → Download diagnostics** and attach the
file to your issue.

It costs **no API quota** — everything in it is state the integration already
holds — and it is built to be safe to post in public: your **VIN, GCID, client
ID, tokens, MQTT topic and GPS coordinates are redacted automatically**.

What's left is exactly what distinguishes the common failures from each other:

| In the file | Answers |
| --- | --- |
| Quota state (used / remaining / next reset) | Whether you've spent your 50 requests / 24 h. |
| Selected clusters + descriptor coverage per car | Whether Data Selection actually saved, and whether the car produces what you picked. |
| Per-descriptor arrival counts and last-seen times | Whether the stream is delivering, and what. |
| Connection history with MQTT `rc` codes | Authorization failures, and another client stealing the single stream. |
| Bootstrap state and your options | Setup that never finished; a setting that isn't what you thought. |

## Debug logging

Off by default. Turn it on in **Configure → Debug logging** and reload. It's
verbose and can include vehicle data such as **GPS and the full VIN**, so leave
it off unless you're chasing a problem. (Separate from Home Assistant's generic
per-integration log level.)

Ordinary log lines — the ones written without debug enabled — show the VIN
masked to its last four characters (`***1234`), which is still enough to tell
two cars apart when you read the log.

Prefer the [diagnostics download](#download-diagnostics) when reporting a
problem: it carries more of what's needed and less of what isn't.

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

## No dashboard appears after setup

<a id="no-dashboard-appears"></a>

Expected: nothing new turns up in the sidebar. BavarianData creates **entities
and devices**, not a dashboard. The bundled card registers itself as a dashboard
*resource*, which makes it available — you still place it yourself: open any
dashboard, **✏️ Edit → ➕ Add card**, pick **BavarianData Card**. See
[The dashboard card](The-Dashboard-Card).

If **BavarianData Card** isn't in the card picker at all, that *is* a fault —
carry on to the next two sections.

## The card doesn't show up after an update

**Hard-refresh the browser** — the bundled card is cached aggressively.

## "Custom element doesn't exist: bavariandata-card" in the mobile app

<a id="custom-element-doesnt-exist-app"></a>

Symptom: the card renders fine in a desktop browser, but the Home Assistant
**companion app** (iOS or Android) shows a red *Configuration error* box reading
`Custom element doesn't exist: bavariandata-card`.

The card is registered once, for the whole instance — so if a browser sees it,
the integration side is fine and something on the phone is failing to load the
card file. Don't start clearing caches; two quick tests narrow it down first.

**Test 1 — open the same dashboard in the phone's own browser** (Safari on iOS,
Chrome on Android), not the app. Same rendering engine, different cache and
different connection settings, so this splits the problem in half:

- **Works in the browser, broken in the app** → it's the app's own frontend
  cache. Restarting Home Assistant or the phone does *not* clear it; you have to
  do it in the app:
  - iOS: **Settings → Companion App → Debugging → Reset frontend cache**, then
    force-quit the app (swipe it away) and reopen it.
  - Android: **Settings → Companion app → Troubleshooting → Clear cache**, then
    force-stop and reopen the app.
- **Broken in the browser too** → carry on to test 2.

**Test 2 — can the phone fetch the card file at all?** In the phone's browser,
open your Home Assistant address with this path appended:

```
/bavariandata/bavariandata-card.js
```

- **A wall of JavaScript** → the file is reachable, so the card is failing while
  it loads. Please open an issue with your iOS/Android version and, if you can
  get at them, the browser console errors.
- **404 / "not found"** → your connection can't reach the path. That's a
  **reverse proxy** (Nginx, Traefik, Caddy, Cloudflare Tunnel …) forwarding only
  the well-known paths — `/api/`, `/static/`, `/frontend_latest/` and friends.
  Custom cards live outside those: BavarianData's at `/bavariandata/`, HACS
  cards at `/hacsfiles/`. It has to forward everything. Home Assistant Cloud
  (Nabu Casa) does, and is unaffected.

Worth knowing for both tests: the companion app only uses your **internal** URL
when you've told it which Wi-Fi network is home (**Settings → Companion App →
Connection → Internal URL**, with an SSID set). Without that it uses the
external URL *even on your home Wi-Fi* — which is how a proxy problem hides
until you look at it on the phone. Test whichever URL the app is actually using,
and ideally both.

Cross-check if you have any HACS card installed: if *it* is broken on the phone
too, the cause is the connection or the app, not this integration.

## Every card shows "Configuration error" after a reload

<a id="config-error-after-reload"></a>

Symptom: the cards render correctly when you first open Home Assistant, then
every one of them turns into a *Configuration error* box after pressing F5. A
hard refresh doesn't help; only a fresh browser session does.

This is fixed from **v0.9.2-beta.8** onward — update the integration and restart
Home Assistant. If it persists:

1. Go to **Settings → Dashboards → ⋮ → Resources** and check that
   `/bavariandata/bavariandata-card.js?v=…` is listed, with the `?v=` matching
   your installed version. It is created and kept up to date automatically.
2. If the list is missing or greyed out, your dashboard resources are
   YAML-managed. Add the resource yourself in `configuration.yaml`:

   ```yaml
   lovelace:
     mode: yaml
     resources:
       - url: /bavariandata/bavariandata-card.js
         type: module
   ```

   Since Home Assistant 2026.2 you can instead set `resource_mode: storage` on a
   YAML dashboard to let BavarianData manage the resource for you.

The cause was that the card used to be injected as a frontend module, which
Home Assistant renders into the page as a fire-and-forget `import()`. The
frontend's service worker can serve that page from its cache without it, so the
`bavariandata-card` element was never defined and every placed card fell back to
the error box — see [home-assistant/frontend#18728][fe18728]. Dashboard
resources are delivered with the Lovelace config instead and are loaded before
any dashboard renders.

[fe18728]: https://github.com/home-assistant/frontend/issues/18728

## Quota exhausted

<a id="quota-exhausted"></a>

If you've spent all [50 REST requests](Feature-API-Quota) in 24 h, a repair
issue appears under **Settings → Repairs** telling you when it resets.
**Streaming keeps flowing throughout** — only `fetch_*` calls pause.

## Removing BavarianData completely

<a id="clean-uninstall"></a>

Deleting the integration under **Settings → Devices & Services** does the important
part: it removes your tokens *and* deletes the recorded history — charging
sessions, trips, any recorded routes — plus the long-term statistics it published.
That is deliberate: your driving and charging record should not outlive the
integration.

A few things are **not** removed automatically:

- The **API quota log** and the **cached vehicle image** in `.storage`.
- `bavariandata_trip_capture.ndjson` in your config folder, if you ever enabled
  trip debugging. **It contains GPS coordinates — delete it.**
- Devices and entities occasionally linger in the registries; delete leftovers
  under **Settings → Devices & Services → Devices / Entities**.

**If you are testing a fresh install**, one extra check matters: long-term
statistics live in the recorder database, not the entity registry, so a *partial*
removal (deleted folder, crash mid-removal) can leave `bavariandata:…` series
behind that reappear in your Energy dashboard with nothing installed. Look under
**Developer tools → Statistics** for orphaned ids and delete them.

The complete artifact list and a step-by-step checklist:
[clean-install.md](https://github.com/JustChr/BavarianData/blob/main/docs/clean-install.md).

> **Upgrading from an older `cardata` / `bmw_cardata` install?** There is no
> cross-domain migration — the domain rename is a breaking change. Remove the old
> integration using the checklist above, then set BavarianData up fresh.

## Where to get help

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
  Attach a [diagnostics download](#download-diagnostics) — it is redacted, costs
  no quota, and usually answers the first three questions we'd ask.
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).
