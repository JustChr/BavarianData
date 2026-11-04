# 2. Install from the Marketplace

> 🇩🇪 [Deutsch](DE-Getting-Started-2-Install)

BavarianData is in Home Assistant's **Marketplace**, the built-in store for
community integrations. It installs like any other integration; no custom
repository needed.

> **Home Assistant older than 2026.11?** The Marketplace arrived with 2026.11;
> before that, the same store was the separate **HACS** add-on. See
> [Before Home Assistant 2026.11: HACS](#before-home-assistant-202611-hacs).

## Requirements

- Home Assistant **2026.3** or newer.

## Steps

The quickest way is this button. It opens BavarianData directly in the
Marketplace on your own Home Assistant (or in HACS, on older versions):

[![Open your Home Assistant instance and open BavarianData in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JustChr&repository=BavarianData&category=integration)

Or do it by hand:

1. Open **Settings → Marketplace** and search for **BavarianData**.
2. Open **BavarianData: Connect Home Assistant to BMW CarData** and select
   **Install**. The first time, the Marketplace asks you to confirm that
   community items aren't reviewed by the Home Assistant project.
3. **Restart Home Assistant.**

Updates come from **GitHub releases**, not from every change to the code. Home
Assistant offers each one as an update, like its own; the release notes show in
the update dialog.

To try beta versions, open BavarianData in the Marketplace and choose
**⋮ → Include pre-releases**.

## If it doesn't appear in the search

The Marketplace refreshes its catalog every few hours. Open BavarianData's page
and choose **⋮ → Refresh information**, or restart Home Assistant, then search
again. If you still don't see it, add it from its GitHub link instead: it's the
same integration and the same updates.

1. In the Marketplace, open the **Add from link** tab.
2. Enter `https://github.com/JustChr/BavarianData` with type **Integration**.
3. Install it, then restart Home Assistant.

## Before Home Assistant 2026.11: HACS

On Home Assistant 2026.3 to 2026.10, install through **HACS** instead. The
button above opens HACS there, or by hand:

1. Open **HACS** and search for **BavarianData**.
2. Open it, install it, and restart Home Assistant.

If it doesn't show up, add `https://github.com/JustChr/BavarianData` under
**⋮ → Custom repositories** with category **Integration**. HACS's own list
shows no logo next to BavarianData; that is a HACS limitation, not a broken
install.

When you update Home Assistant to 2026.11 or newer, the Marketplace takes over
from HACS by itself: BavarianData stays installed, with its update entity and
settings, and there is nothing to reinstall.

**Next:** [3. Add & authorize the integration →](Getting-Started-3-Add-and-Authorize)
