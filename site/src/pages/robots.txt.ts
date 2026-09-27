import type { APIRoute } from "astro";
import { absolute } from "../lib/site";

// Everything is public; AI crawlers are welcome too -- being found by
// assistants is part of why this site exists.
export const GET: APIRoute = () =>
  new Response(`User-agent: *\nAllow: /\n\nSitemap: ${absolute("sitemap-index.xml")}\n`, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
