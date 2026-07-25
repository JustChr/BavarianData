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

1. Make sure you completed [step 4](Getting-Started-4-Choose-Data) — descriptors
   must be ticked **and saved** in the portal's Data Selection.
2. Trigger a lock/unlock in the MyBMW app to prompt the car to send an update.
3. Run **`bavariandata.get_coverage_report`**
   ([Services](Services-Reference#get_coverage_report)) — it tells you exactly
   which expected descriptors haven't arrived, so you can tell a selection
   problem from a car-doesn't-send-it problem.

## Only one stream per account

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

## The card doesn't show up after an update

**Hard-refresh the browser** — the bundled card is cached aggressively.

## Quota exhausted

If you've spent all [50 REST requests](Feature-API-Quota) in 24 h, a repair
issue appears under **Settings → Repairs** telling you when it resets.
**Streaming keeps flowing throughout** — only `fetch_*` calls pause.

## Where to get help

- Bugs in the integration → [Issues](https://github.com/JustChr/BavarianData/issues).
- BMW-side registration trouble, setup help, or general questions →
  [Discussions](https://github.com/JustChr/BavarianData/discussions).
