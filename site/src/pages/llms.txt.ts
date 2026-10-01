import type { APIRoute } from "astro";
import { absolute, docPath, sidebar, PAGES, RELEASE, HA_MIN, REPO } from "../lib/site";

// https://llmstxt.org -- a plain summary for assistants, with the pages that
// answer the questions people actually ask them.
export const GET: APIRoute = () => {
  const lines = [
    "# BavarianData",
    "",
    `> BavarianData is a free, open-source (MIT) Home Assistant integration for BMW CarData, BMW's official customer data service. It connects Home Assistant directly to a BMW or MINI: a live MQTT stream plus a REST API (50 requests per 24 hours), using the owner's personal CarData client ID. It is read-only (CarData cannot command the car), installs from the HACS default store, and needs Home Assistant ${HA_MIN} or newer.${RELEASE.version ? ` Latest stable release: ${RELEASE.version} (${RELEASE.date}).` : ""}`,
    "",
    "It replaces the BMW Connected Drive integration (bimmer_connected), which stopped working when BMW blocked third-party access on 29 September 2025. On top of the raw data it records a charging history with cost and energy source, a trip journal, learned battery health, a measured real range, Energy dashboard statistics, CSV/HTML export, and an evcc/openWB bridge over MQTT. Entity names ship in ten languages.",
    "",
    `Source: ${REPO}`,
    `Full manual as one file: ${absolute("llms-full.txt")}`,
    "",
  ];
  for (const section of sidebar("en")) {
    lines.push(`## ${section.title}`, "");
    for (const item of section.items) {
      const meta = PAGES[item.page]?.en;
      if (meta) lines.push(`- [${item.label}](${absolute(docPath(item.page, "en"))}): ${meta.description}`);
    }
    lines.push("");
  }
  lines.push("## Reference", "", `- [Every BMW CarData descriptor](${absolute("data")}): names, units, value ranges, streamed or REST-only`, "");
  lines.push("## Releases", "", `- [What's new](${absolute("changelog")}): every stable release, newest first, with what was added, changed and fixed`, "");
  lines.push("## Optional", "", `- [German manual](${absolute("de/docs")})`, "");
  return new Response(lines.join("\n"), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
