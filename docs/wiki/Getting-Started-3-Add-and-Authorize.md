# 3. Add & authorize the integration

With the client ID from [step 1](Getting-Started-1-BMW-Portal-Setup) ready:

## Steps

1. **Settings → Devices & Services → Add Integration → BavarianData: Connect
   Home Assistant to BMW CarData**.
2. The first screen recaps the portal setup and asks for your **client ID**.
   Paste it in.

   <!-- screenshot: config-flow-user (client ID entry) -->

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
