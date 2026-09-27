import type { APIRoute } from "astro";
import { url } from "../lib/site";

export const GET: APIRoute = () =>
  new Response(
    JSON.stringify({
      name: "BavarianData",
      short_name: "BavarianData",
      description: "BMW CarData for Home Assistant",
      start_url: url(""),
      display: "browser",
      background_color: "#f3f6fb",
      theme_color: "#0e1f45",
      icons: [
        { src: url("apple-touch-icon.png"), sizes: "180x180", type: "image/png" },
        { src: url("icon-512.png"), sizes: "512x512", type: "image/png" },
      ],
    }),
    { headers: { "Content-Type": "application/manifest+json" } }
  );
