import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";

// The manual is the wiki itself: docs/wiki/*.md (English) and
// docs/wiki/de/DE-*.md (German). Entry ids are the wiki page names.
const wiki = defineCollection({
  loader: glob({
    base: "../docs/wiki",
    pattern: ["*.md", "de/DE-*.md", "!README.md", "!_*.md"],
    generateId: ({ entry }) => entry.replace(/^de\//, "").replace(/\.md$/, ""),
  }),
});

// The changelog: CHANGELOG.md's stable releases, written by scripts/prepare.mjs.
const changelog = defineCollection({
  loader: glob({ base: "./src/generated", pattern: "changelog.md" }),
});

export const collections = { wiki, changelog };
