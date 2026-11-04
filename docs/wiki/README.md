# Wiki source (staging)

These files are the **source for the GitHub Wiki** at
<https://github.com/JustChr/BavarianData/wiki>. They live in the repo so they're
reviewable in PRs; the wiki itself is a separate git repository.

Filenames follow GitHub Wiki conventions: the filename (spaces as `-`) **is** the
page title and URL slug. `Home.md` is the landing page; `_Sidebar.md` and
`_Footer.md` are the nav chrome. Internal links use the page slug, e.g.
`[Trips](Feature-Trips)`.

## Publishing

The GitHub Wiki must be **initialized once via the web UI** — GitHub doesn't
provision the wiki git remote (and offers no API for it) until at least one page
exists. Go to <https://github.com/JustChr/BavarianData/wiki>, click **Create the
first page**, save, then run:

```bash
bash scripts/publish-wiki.sh
```

That clones the wiki, syncs every page here (except this repo-facing README),
commits and pushes. Re-run it any time these files change.

> The pages reference screenshots via
> `raw.githubusercontent.com/JustChr/BavarianData/main/screenshots/…`, so those
> image files must be **committed and pushed to `main`** for the images to load.

## Screenshots

Every card view and Configure screen has its picture. One placeholder is left,
`<!-- screenshot: config-flow-authorize -->` on step 3: it needs a live
onboarding, which can't be shot on the production instance without disturbing
its one stream.

## German pages

Every page has a German counterpart in [`de/`](de/), named `DE-<page>.md`. They
publish flat beside the English pages (wiki page names are global, hence the
prefix), and the sidebar lists both languages. German sections carry the English
section ids as `<a id>` anchors, so links use the same `#section` in both.
`tests/test_wiki_links.py` checks links, anchors, the pairing and the sidebar —
change an English page and its German page together.
