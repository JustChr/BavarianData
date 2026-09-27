// Tell IndexNow search engines (Bing -- which also feeds Copilot and ChatGPT
// search -- Yandex, Seznam, Naver) that the site changed. No account needed:
// ownership is proven by the key file served next to the pages
// (public/<key>.txt). Runs after each deploy, against the *live* sitemap, so it
// only ever announces URLs that actually exist.
//
//   node scripts/indexnow.mjs            # submit
//   node scripts/indexnow.mjs --dry-run  # list what would be submitted

import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SITE_URL = (process.env.SITE_URL || "https://justchr.github.io").replace(/\/$/, "");
const BASE = (process.env.SITE_BASE ?? "/BavarianData").replace(/\/$/, "");
const root = `${SITE_URL}${BASE}`;
const publicDir = fileURLToPath(new URL("../public/", import.meta.url));
const keyFile = readdirSync(publicDir).find((f) => /^[0-9a-f]{32}\.txt$/.test(f));
if (!keyFile) throw new Error("no IndexNow key file (32 hex chars .txt) in site/public");
const key = keyFile.slice(0, -4);

async function locs(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return [...(await res.text()).matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
}

const urls = [];
for (const sitemap of await locs(`${root}/sitemap-index.xml`)) urls.push(...(await locs(sitemap)));

if (process.argv.includes("--dry-run")) {
  console.log(`${urls.length} URLs, key ${keyFile}\n${urls.slice(0, 5).join("\n")}\n…`);
  process.exit(0);
}

const body = JSON.stringify({ host: new URL(SITE_URL).host, key, keyLocation: `${root}/${keyFile}`, urlList: urls });
// The first submission for a key starts an asynchronous check of the key file
// and is answered 403 "SiteVerificationNotCompleted" meanwhile: wait, retry.
for (let attempt = 1; ; attempt++) {
  const res = await fetch("https://api.indexnow.org/indexnow", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body,
  });
  // 200 = accepted, 202 = accepted, key validation pending.
  console.log(`IndexNow: HTTP ${res.status} for ${urls.length} URLs`);
  if (res.status < 300) break;
  const text = await res.text();
  console.log(text);
  if (res.status === 403 && text.includes("SiteVerificationNotCompleted")) {
    if (attempt < 3) {
      await new Promise((r) => setTimeout(r, 60_000));
      continue;
    }
    // Their check of the key file can take hours; the next deploy resubmits.
    console.log("::warning::IndexNow is still verifying the key file; the next deploy will resubmit.");
    break;
  }
  process.exit(1);
}
