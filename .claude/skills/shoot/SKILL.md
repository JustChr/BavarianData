---
name: shoot
description: Capture screenshots of Home Assistant screens or the bundled card with Playwright — config-flow dialogs, options screens, card views, shadow-DOM crops. Use when documentation needs a new or updated screenshot, or when a visual change should be seen rather than described.
---

# Screenshots

Playwright browsers are **already cached** on this machine under
`%LOCALAPPDATA%\ms-playwright\chromium-<build>\chrome-win64\chrome.exe` (pick
the newest build dir). The `playwright` npm module is not installed globally —
only the CLI via `npx playwright`.

Work in the session scratchpad: `npm init -y && npm install playwright-core`,
then `require('playwright-core').chromium.launch({ executablePath: EXE })`.
Use **playwright-core**, not `playwright`, so it never tries to download a
browser. Copy finished PNGs into `screenshots/` at the end.

Host and login are in project memory (`live-ha-instance-access`).

## Quality

`deviceScaleFactor: 3` for crisp output; a narrow `viewport` for card-shaped
shots. Crop one component with element `.screenshot()`, not full-page.

## Force English first

```js
localStorage.setItem("selectedLanguage", '"en"')   // value is JSON — note the nested quotes
```
then reload. Caveat: BMW descriptor entity names stay German (server-side
friendly names), so raw attribute lists render mixed even in English mode.

## Shadow DOM

`querySelector` does not cross shadow boundaries. Walk recursively: if
`el.shadowRoot` exists, recurse; collect by `tagName`; return via
`page.evaluateHandle` → `getProperties()` to get handles you can `.screenshot()`.
Playwright's `getByRole`/`getByText` *do* pierce open shadow DOM.

## A card view no dashboard has

Open a dashboard that **already uses the card** (HA's start page never loads the
Lovelace resource, so waiting for `customElements.get("bavariandata-card")`
there times out). Then in `page.evaluate`: make a fixed-position wrapper,
`document.createElement("bavariandata-card")`, `setConfig({type, cluster})`,
`card.hass = document.querySelector("home-assistant").hass`, append, click
inside `card.shadowRoot` to expand, and screenshot the wrapper. 420 px at
`deviceScaleFactor: 3` matches the existing shots.

## A config-flow dialog

Deep-link `GET /config/integrations/dashboard/add?domain=bavariandata`, click
through the "Do you want to set up X?" confirm, then pick the menu option by
text. To crop just the dialog, walk the shadow DOM for
`tagName==='dialog' || role==='dialog'`, take the smallest sensible
`getBoundingClientRect()`, and use `page.screenshot({clip})` — element
`.screenshot()` **times out**, because the modal backdrop animation never
settles.

The served onboarding page (`/bavariandata/onboarding?token=…`) is plain HTML:
open it in a **second tab** while the flow tab stays alive — closing the flow
aborts it and 404s the token.

## Do not try to capture these

- `authorize` / `authorize_failed` — submitting a client id in
  `async_step_manual` **deletes** the existing entry with that id. Destructive
  on the user's real setup.
- `guided_wait` — https-only progress screen; the instance is http.
- `guided_done`, reconfigure `activate_stream_done` — need a real activation.

Grab those by hand during a real setup, or against an https instance.

## Afterwards

Name them `screenshots/bavariandata-*.png` and **commit them to `main`** — the
wiki loads them through `raw.githubusercontent.com/.../main/screenshots/…`, so
an uncommitted file is a broken image. BMW portal shots need their PII
pixelated (name, VIN, plate, client id); they are © BMW AG and the attribution
line lives in the wiki `_Footer.md`.
