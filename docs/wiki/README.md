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

## Pending passes

- **Screenshots** — done: cluster picker, Configure menu, charging-costs
  settings, and the charging & battery-health card views (in `screenshots/`).
  Still marked with `<!-- screenshot: … -->` comments and pending:
  - `config-flow-user` / `config-flow-authorize` — need a live onboarding (not
    captured on the production instance to avoid disturbing the single stream).
  - `config-flow-cluster-snippet` — reachable only by submitting the picker,
    which persists/reloads; capture during a real setup.
  - `card-trips` — the live car has no recorded trips yet (empty state); capture
    once trips exist.
  - `Trips` settings screen — captured, but showed a real work-zone name;
    redact before publishing.
- **German** — English prose ships first; German is a fast-follow (see
  [docs/documentation-plan.md](../documentation-plan.md)).
