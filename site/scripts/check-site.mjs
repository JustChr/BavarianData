// After `astro build`: fail on anything a crawler would trip over.
//
// * every internal link and image resolves to a built file;
// * every `#anchor` on an internal link exists on the page it points at;
// * every page has one <h1>, a <title>, a meta description and a canonical URL,
//   and no two indexable pages share a title;
// * canonicals and internal links to pages end in a slash -- GitHub Pages
//   answers the slash-less form with a 301, and a canonical that redirects
//   contradicts the sitemap.
//
// The wiki is written for GitHub; this is what proves it survived the trip.

import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const BASE = (process.env.SITE_BASE ?? "/BavarianData").replace(/\/$/, "");

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* walk(path);
    else if (name.endsWith(".html")) yield path;
  }
}

const pages = new Map();
for (const file of walk(DIST)) pages.set(file, readFileSync(file, "utf8"));

const ids = (html) => new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
const idCache = new Map();
const problems = [];
const titles = new Map();

function resolve(target) {
  const clean = decodeURIComponent(target.split("#")[0].split("?")[0]);
  if (!clean.startsWith(`${BASE}/`) && clean !== BASE) return { skip: true };
  const rel = clean.slice(BASE.length).replace(/^\//, "");
  const candidates = [join(DIST, rel), join(DIST, rel, "index.html")];
  const hit = candidates.find((c) => existsSync(c) && statSync(c).isFile());
  return { file: hit };
}

for (const [file, html] of pages) {
  const where = relative(DIST, file);
  if (where.startsWith("pagefind")) continue;
  const h1s = (html.match(/<h1[\s>]/g) || []).length;
  if (h1s !== 1) problems.push(`${where}: ${h1s} <h1> elements`);
  const title = /<title>([^<]*)<\/title>/.exec(html)?.[1];
  if (!title) problems.push(`${where}: no <title>`);
  if (!/<meta name="description" content="[^"]{50,}"/.test(html)) problems.push(`${where}: missing or short meta description`);
  const canonical = /<link rel="canonical" href="([^"]+)"/.exec(html)?.[1];
  if (!canonical) problems.push(`${where}: no canonical`);
  else if (!canonical.endsWith("/")) problems.push(`${where}: canonical without a trailing slash: ${canonical}`);
  if (title && !/name="robots" content="noindex"/.test(html)) {
    if (titles.has(title)) problems.push(`${where}: same <title> as ${titles.get(title)}`);
    titles.set(title, where);
  }

  // Scripts and inline JSON may hold URL-like strings; only check real markup.
  const markup = html.replace(/<script[\s\S]*?<\/script>/g, "");
  for (const m of markup.matchAll(/\s(?:href|src)="([^"]+)"/g)) {
    const target = m[1].replace(/&amp;/g, "&");
    if (/^(https?:|mailto:|data:|#)/.test(target) && !target.startsWith("#")) continue;
    if (target.startsWith("#")) {
      const id = decodeURIComponent(target.slice(1));
      if (id && !ids(html).has(id)) problems.push(`${where}: anchor ${target} is not on this page`);
      continue;
    }
    if (target.includes("/pagefind/")) continue;
    const { skip, file: hit } = resolve(target);
    if (skip) {
      problems.push(`${where}: link outside the site base: ${target}`);
      continue;
    }
    if (!hit) {
      problems.push(`${where}: broken link ${target}`);
      continue;
    }
    const pathPart = target.split("#")[0].split("?")[0];
    if (hit.endsWith("index.html") && !pathPart.endsWith("/") && !pathPart.endsWith(".html")) {
      problems.push(`${where}: link without a trailing slash (a 301 on GitHub Pages): ${target}`);
    }
    const hash = target.split("#")[1];
    if (hash && hit.endsWith(".html")) {
      if (!idCache.has(hit)) idCache.set(hit, ids(pages.get(hit) ?? readFileSync(hit, "utf8")));
      if (!idCache.get(hit).has(decodeURIComponent(hash))) problems.push(`${where}: ${target} -- no such anchor`);
    }
  }
}

// The changelog page is generated from CHANGELOG.md; an empty or stale one
// would otherwise pass every check above.
const changelogFile = join(DIST, "changelog", "index.html");
const stable = /^## \[(\d+\.\d+\.\d+)\] - /m.exec(
  readFileSync(fileURLToPath(new URL("../../CHANGELOG.md", import.meta.url)), "utf8")
);
if (!existsSync(changelogFile)) problems.push("changelog/index.html was not built");
else if (stable && !readFileSync(changelogFile, "utf8").includes(`id="v${stable[1]}"`)) {
  problems.push(`changelog/index.html does not list the newest stable release, ${stable[1]}`);
}

if (problems.length) {
  console.error(`check-site: ${problems.length} problem(s)\n  ${[...new Set(problems)].join("\n  ")}`);
  process.exit(1);
}
console.log(`check-site: ${pages.size} pages, links, anchors and head tags all fine`);
