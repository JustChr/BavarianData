# 3. Add & authorize the integration

You need the portal prep from [step 1](Getting-Started-1-BMW-Portal-Setup) done
first: a **CarData Client** created and subscribed to **both** CarData API and
CarData Streaming. That applies to **both** setup paths below — the guided path
only saves you copying the client ID by hand, it doesn't skip creating the
client.

## Steps

1. **Settings → Devices & Services → Add Integration → BavarianData: Connect
   Home Assistant to BMW CarData**.
2. Pick a setup path:
   - **Guided (recommended)** — Home Assistant serves a small page with a
     one-click **Activate BMW data** bookmarklet. Run it on the BMW/MINI portal's
     stream-setup page and it finds your client ID, checks the vehicle, and turns
     on the stream **in your own browser** — nothing to copy. On an https Home
     Assistant it reports back and continues automatically; on http it shows a
     **Copy** button and you paste the short result back.
   - **Manual** — paste the **client ID** yourself, the classic path.

   <!-- screenshot: config-flow-user (guided/manual chooser) -->

3. Home Assistant shows a **link and a code**. Open the link, sign in, and
   approve the device on BMW's site. When BMW accepts the approval, the dialog
   **continues on its own** — there is nothing to click in Home Assistant. If
   the code times out, press **Submit** for a fresh one and try again.

   <!-- screenshot: config-flow-authorize (device link + user code) -->

4. **Choose which data to stream.** The moment authorization succeeds, setup
   moves straight into the cluster picker — no separate trip to Configure.
   Continue with [step 4](Getting-Started-4-Choose-Data). Until you finish it,
   no descriptors are selected in the portal, so no MQTT data arrives.

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
