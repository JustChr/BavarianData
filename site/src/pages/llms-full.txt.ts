import type { APIRoute } from "astro";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { WIKI_DIR, rewriteHref, SITE_URL } from "../lib/wiki.mjs";
import { sidebar, absolute, docPath } from "../lib/site";

// The whole English manual as Markdown, in reading order, with links pointing
// at the site. Images are left out: an assistant can't use them.
export const GET: APIRoute = () => {
  const pages = ["Home", ...sidebar("en").flatMap((s) => s.items.map((i) => i.page))];
  const parts = pages.map((page) => {
    const md = readFileSync(join(WIKI_DIR, `${page}.md`), "utf8")
      .replace(/<p align="center">[\s\S]*?<\/p>\s*/g, "")
      .replace(/^> 🇩🇪 .*\n\n?/m, "")
      .replace(/\]\(([^)\s]+)\)/g, (m, href) => {
        const next = rewriteHref(href);
        return next ? `](${SITE_URL}${next})` : m;
      });
    return `<!-- ${absolute(docPath(page, "en"))} -->\n${md.trim()}\n`;
  });
  return new Response(parts.join("\n---\n\n"), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
