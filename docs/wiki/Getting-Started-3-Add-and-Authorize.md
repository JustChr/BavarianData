# 3. Add & authorize the integration

You need the portal prep from [step 1](Getting-Started-1-BMW-Portal-Setup) done
first: a **CarData Client** created and subscribed to **both** CarData API and
CarData Streaming. That applies to **both** setup paths below — the guided path
only saves you copying the client ID by hand, it doesn't skip creating the
client.

Start setup:

1. **Settings → Devices & Services → Add Integration → BavarianData: Connect
   Home Assistant to BMW CarData**.
2. Pick a setup path — **Guided** or **Manual**:

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-chooser.png" alt="First screen: reminder to create a CarData Client subscribed to both services, then a choice between Guided setup (recommended) and 'I'll paste the Client ID myself'" width="520" />
   </p>

## Guided vs Manual — which to pick

Both paths need the same portal client and both end the same way (device
authorization → a live stream). They differ only in **how the client ID gets in**
and **how the stream is switched on**:

| | **Guided** (recommended) | **Manual** |
| --- | --- | --- |
| Client ID | discovered for you | you copy & paste it |
| Switching on the stream | one click — an **Activate BMW data** bookmarklet you run on the portal | pick clusters in Home Assistant, then run a console snippet on the portal's **Data Selection** page |
| Which fields turn on | a sensible **default** set (fine-tune later) | **exactly** the clusters you tick |
| Best when | you just want it working with no copying | you want to choose clusters up front, or the bookmarklet can't run in your browser |

You can always change the streamed clusters afterwards from
**Configure → Choose streamed data** (which uses the same one-click activator) —
see [step 4](Getting-Started-4-Choose-Data).

## Guided path

1. Choose **Guided setup (recommended)**. Home Assistant serves a small
   activation page. **Drag the *Activate BMW data* button to your bookmarks bar**
   (once):

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-bookmarklet.png" alt="The served activation page with a draggable 'Activate BMW data' bookmarklet button and a console fallback" width="600" />
   </p>

2. Open the **BMW or MINI portal**, sign in, go to a vehicle's **stream setup**
   page, and click the **Activate BMW data** bookmark. It finds your client ID,
   checks the vehicle, and turns on the default data fields — **all in your own
   browser**, so no password or session ever leaves it.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-guided.png" alt="Guided activation screen: run the bookmarklet on the portal, then Home Assistant continues automatically or you paste the short result" width="520" />
   </p>

   - On an **https** Home Assistant the activator reports back and setup
     **continues on its own**.
   - On an **http** Home Assistant, press **Copy** in the box the activator shows
     and **paste** the short (non-secret) result into the field.

3. Continue with **device authorization** (below). When it finishes, the default
   clusters are already streaming — fine-tune them any time from
   **Configure → Choose streamed data**.

## Manual path

1. Choose **I'll paste the Client ID myself** and paste the **client ID** you
   copied from the portal.

   <p align="center">
     <img src="https://raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/bavariandata-setup-manual.png" alt="Manual path: a recap of the portal setup and a field to paste the CarData Client ID" width="520" />
   </p>

2. Continue with **device authorization** (below).
3. When authorization succeeds, setup moves straight into the **cluster picker**,
   where you choose clusters and get a ready-made snippet for the portal's Data
   Selection page. Continue with [step 4](Getting-Started-4-Choose-Data).

## Device authorization (both paths)

Home Assistant shows a **link and a code**. Open the link, sign in, and approve
the device on BMW's site. When BMW accepts the approval, the dialog **continues
on its own** — there is nothing to click in Home Assistant. If the code times
out, press **Submit** for a fresh one and try again.

<!-- screenshot: config-flow-authorize (device link + user code) -->

## If onboarding fails with "access denied"

BMW's authorization backend is sometimes flaky and can report *access denied*
even though your login clearly worked and you never saw a consent page. **This
is a known BMW-side quirk, not a fault in the integration**, and it has a
reliable workaround — see
[Troubleshooting → "access denied"](Troubleshooting-and-FAQ#onboarding-fails-with-access-denied).

## Re-authorizing later

If BMW later invalidates the token, run **Configure → Re-authorize with BMW**.
Removing and re-adding the integration with the same client ID also works — the
previous entry is cleaned up automatically.

**Next:** [4. Choose which data to stream →](Getting-Started-4-Choose-Data)
