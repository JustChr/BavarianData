// The bridge between the GitHub-wiki Markdown in docs/wiki/ and this site.
//
// The wiki stays the single source of the manual; nothing here edits it. What
// changes on the way through:
//
// * wiki links (`Feature-Trips#x`, `DE-Feature-Trips`, full wiki URLs) become
//   clean site URLs in the right language;
// * the "> 🇩🇪 Deutsch" line at the top of each page goes -- the site has a real
//   language switch with hreflang instead;
// * the page's H1 moves into frontmatter so the layout can set it;
// * screenshots are served as local WebP with their real dimensions;
// * tables scroll inside themselves on narrow screens, and headings get links.
//
// Plain .mjs on purpose: astro.config.mjs imports it before Vite is running.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { visit, SKIP } from "unist-util-visit";

/** site/ -- found by walking up, because the build bundles this module into
 * dist/ and its own location stops saying anything. */
function findSiteRoot() {
  for (const start of [dirname(fileURLToPath(import.meta.url)), process.cwd()]) {
    let dir = start;
    for (let i = 0; i < 8; i++) {
      if (existsSync(join(dir, "astro.config.mjs"))) return dir;
      dir = dirname(dir);
    }
  }
  throw new Error("cannot find site/ (no astro.config.mjs above this module or the working directory)");
}
export const SITE_ROOT = findSiteRoot();
export const REPO_ROOT = join(SITE_ROOT, "..");
export const WIKI_DIR = join(REPO_ROOT, "docs", "wiki");

export const SITE_URL = (process.env.SITE_URL || "https://justchr.github.io").replace(/\/$/, "");
export const BASE = (process.env.SITE_BASE ?? "/BavarianData").replace(/\/$/, "");
export const REPO = "https://github.com/JustChr/BavarianData";

const PAGES = JSON.parse(readFileSync(join(SITE_ROOT, "src", "data", "pages.json"), "utf8"));
delete PAGES._comment;

/** Site path for a path inside the site, with the base and a trailing slash. */
export function url(path = "") {
  const clean = path.replace(/^\/+/, "");
  if (!clean) return `${BASE}/`;
  const [pathname, hash] = clean.split("#");
  const withSlash = /\.[a-z0-9]+$/i.test(pathname) || pathname.endsWith("/") ? pathname : `${pathname}/`;
  return `${BASE}/${withSlash}${hash !== undefined ? `#${hash}` : ""}`;
}

/** The site path of a wiki page in a language. */
export function docPath(page, lang) {
  const entry = PAGES[page];
  if (!entry) return null;
  const prefix = lang === "de" ? "de/" : "";
  return entry.slug ? `${prefix}docs/${entry.slug}` : `${prefix}docs`;
}

const WIKI_URL = /^https:\/\/github\.com\/JustChr\/BavarianData\/wiki\/?/;
const FIELD_REFERENCE = /^https:\/\/github\.com\/JustChr\/BavarianData\/blob\/main\/docs\/reference\/telematics-fields\.md/;

/** Where a link in a wiki page should point on the site, or null to keep it. */
export function rewriteHref(href) {
  if (FIELD_REFERENCE.test(href)) return url("data");
  let target = href;
  if (WIKI_URL.test(target)) target = target.replace(WIKI_URL, "") || "Home";
  if (/^[a-z]+:|^\/|^#|^\./i.test(target)) return null;
  const [name, hash] = target.split("#");
  const lang = name.startsWith("DE-") ? "de" : "en";
  const page = lang === "de" ? name.slice(3) : name;
  const path = docPath(page, lang);
  if (!path) return null;
  return url(hash ? `${path}#${hash}` : path);
}

function textOf(node) {
  if (node.type === "text" || node.type === "inlineCode") return node.value;
  return (node.children || []).map(textOf).join("");
}

/** Remark: links, the language line, the H1 and the wiki's own logo. */
export function remarkWiki() {
  return (tree, file) => {
    let heading = null;
    visit(tree, (node, index, parent) => {
      if (node.type === "heading" && node.depth === 1 && !heading && parent) {
        heading = textOf(node);
        parent.children.splice(index, 1);
        return [SKIP, index];
      }
      if (node.type === "blockquote" && parent) {
        const text = textOf(node).trim();
        let links = 0;
        visit(node, "link", () => void links++);
        if (links === 1 && /^(🇩🇪|🇬🇧)/u.test(text) && text.length < 40) {
          parent.children.splice(index, 1);
          return [SKIP, index];
        }
      }
      if (node.type === "html" && /logo\.png/.test(node.value) && parent) {
        parent.children.splice(index, 1);
        return [SKIP, index];
      }
      if (node.type === "link") {
        const next = rewriteHref(node.url);
        if (next) node.url = next;
      }
    });
    file.data.astro ??= {};
    file.data.astro.frontmatter ??= {};
    file.data.astro.frontmatter.heading = heading;
  };
}

const SCREENSHOT = /^https:\/\/raw\.githubusercontent\.com\/JustChr\/BavarianData\/main\/screenshots\/([\w.-]+)\.png$/;

/** Rehype: screenshots, figures, scrolling tables, heading anchors. */
export function rehypeWiki() {
  const assets = JSON.parse(readFileSync(join(SITE_ROOT, "src", "generated", "assets.json"), "utf8"));
  return (tree) => {
    visit(tree, "element", (node, index, parent) => {
      const p = node.properties || {};
      if (node.tagName === "img") {
        const match = SCREENSHOT.exec(String(p.src || ""));
        if (match && assets.screenshots[match[1]]) {
          const { width, height } = assets.screenshots[match[1]];
          const shown = Number(p.width) || width;
          p.src = url(`screenshots/${match[1]}.webp`);
          p.width = shown;
          p.height = Math.round((shown * height) / width);
          p.dataZoom = url(`screenshots/${match[1]}.webp`);
        }
        p.loading = "lazy";
        p.decoding = "async";
      }
      if (node.tagName === "p" && p.align === "center") {
        delete p.align;
        node.tagName = "figure";
        p.className = ["shot"];
      }
      if (node.tagName === "table" && parent && !(parent.properties?.className || []).includes("table-scroll")) {
        parent.children[index] = {
          type: "element",
          tagName: "div",
          properties: { className: ["table-scroll"], tabIndex: 0 },
          children: [node],
        };
        return SKIP;
      }
      if (/^h[2-4]$/.test(node.tagName) && p.id) {
        node.children.push({
          type: "element",
          tagName: "a",
          properties: { className: ["anchor"], href: `#${p.id}`, ariaLabel: "Link to this section", "data-pagefind-ignore": "all" },
          children: [{ type: "text", value: "#" }],
        });
      }
      if (node.tagName === "a" && /^https?:/.test(String(p.href || "")) && !String(p.href).startsWith(SITE_URL)) {
        p.rel = ["noopener"];
      }
    });
  };
}

/** The wiki sidebar as navigation: sections of {page, label}, per language. */
export function sidebar(lang) {
  const text = readFileSync(join(WIKI_DIR, "_Sidebar.md"), "utf8");
  const [en, de] = text.split(/^---$/m);
  const source = lang === "de" ? de : en;
  const sections = [];
  let current = null;
  for (const line of source.split(/\r?\n/)) {
    const head = /^\*\*(.+)\*\*$/.exec(line.trim());
    if (head) {
      current = { title: head[1], items: [] };
      sections.push(current);
      continue;
    }
    const item = /^- \[(.+?)\]\(([^)]+)\)/.exec(line.trim());
    if (item && current) {
      const name = item[2].replace(/^DE-/, "");
      current.items.push({ page: name, label: item[1] });
    }
  }
  return sections;
}

export { PAGES };
