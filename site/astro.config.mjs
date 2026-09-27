// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import rehypeRaw from "rehype-raw";
import { rehypeHeadingIds, unified } from "@astrojs/markdown-remark";
import { remarkWiki, rehypeWiki, SITE_URL, BASE } from "./src/lib/wiki.mjs";

// SITE_URL / SITE_BASE switch the site to a custom domain without code changes
// (e.g. SITE_URL=https://bavariandata.example SITE_BASE=).
export default defineConfig({
  site: SITE_URL,
  base: BASE || "/",
  trailingSlash: "always",
  build: { format: "directory" },
  integrations: [
    sitemap({
      i18n: { defaultLocale: "en", locales: { en: "en", de: "de" } },
      filter: (page) => !page.includes("/404"),
    }),
  ],
  markdown: {
    // The wiki needs remark/rehype plugins, which run on the unified processor
    // (Astro 7's default, Satteri, doesn't take them).
    processor: unified({
      gfm: true,
      remarkPlugins: [remarkWiki],
      // rehype-raw first: the wiki's inline HTML (screenshots) must be real
      // elements before rehypeWiki rewrites it.
      rehypePlugins: [rehypeRaw, rehypeHeadingIds, rehypeWiki],
    }),
    shikiConfig: { themes: { light: "github-light", dark: "github-dark" } },
  },
  vite: { server: { fs: { allow: [".."] } } },
});
