# 2. Install via HACS

> 🇩🇪 [Deutsch](DE-Getting-Started-2-Install)

BavarianData is in the **HACS default store**, so it installs like any other
integration — no custom repository needed.

## Requirements

- Home Assistant **2026.3** or newer.
- HACS installed.

## Steps

1. Open **HACS** and search for **BavarianData**.
2. Open **BavarianData: Connect Home Assistant to BMW CarData** and install it.
3. **Restart Home Assistant.**

> HACS users receive updates only from **GitHub releases**, not from every push
> to `main`. Track the releases page for new versions.

## If it doesn't appear in the search

Newly added integrations take a while to reach every HACS instance. Refresh the
HACS data (**⋮ → Reload data** or restart Home Assistant) and search again. If
you still don't see it, install it as a custom repository instead — it's the
same integration and the same updates:

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/JustChr/BavarianData` with category **Integration**.
3. Install it, then restart Home Assistant.

## The missing icon in HACS

HACS's own list shows no logo next to BavarianData. That is a HACS-side
limitation with self-served brand assets, not a broken install — the icon shows
correctly everywhere else in Home Assistant, and there is nothing to fix on your
side.

**Next:** [3. Add & authorize the integration →](Getting-Started-3-Add-and-Authorize)
