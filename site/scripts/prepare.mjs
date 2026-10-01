// Prepares everything the site takes from the rest of the repository, so the
// build never reaches outside site/ at render time for binary assets:
//
// * the shipped card, copied verbatim -- the demo runs the real thing;
// * the Material Design Icons the card and the demo car ask for (only those);
// * the screenshots as WebP, with their dimensions for width/height (no layout
//   shift);
// * favicons and the social preview image, drawn from the brand icon.
//
// Runs before every `astro dev` / `astro build` (see package.json).

import { readFileSync, writeFileSync, mkdirSync, readdirSync, copyFileSync, rmSync } from "node:fs";
import { dirname, join, basename } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import * as mdi from "@mdi/js";

const SITE = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = join(SITE, "..");
const PUBLIC = join(SITE, "public");
const GEN = join(SITE, "src", "generated");
for (const dir of [GEN, join(PUBLIC, "demo"), join(PUBLIC, "screenshots")]) mkdirSync(dir, { recursive: true });

// Astro caches rendered Markdown by file content, not by the plugins that
// rendered it -- a change to src/lib/wiki.mjs would otherwise be invisible.
rmSync(join(SITE, ".astro"), { recursive: true, force: true });

// --- the card ---------------------------------------------------------------
const cardPath = join(ROOT, "custom_components", "bavariandata", "www", "bavariandata-card.js");
const card = readFileSync(cardPath, "utf8");
copyFileSync(cardPath, join(PUBLIC, "demo", "bavariandata-card.js"));
const cardVersion = (card.match(/const CARD_VERSION = "([^"]+)"/) || [])[1];

// --- icons ------------------------------------------------------------------
const demo = readFileSync(join(SITE, "src", "demo", "i5.json"), "utf8");
const names = new Set([...`${card}\n${demo}`.matchAll(/mdi:([a-z0-9-]+)/g)].map((m) => m[1]));
const icons = {};
const missing = [];
for (const name of [...names].sort()) {
  const key = "mdi" + name.split("-").map((p) => p[0].toUpperCase() + p.slice(1)).join("");
  if (mdi[key]) icons[`mdi:${name}`] = mdi[key];
  else missing.push(name);
}
if (missing.length) console.warn(`prepare: no MDI path for ${missing.join(", ")}`);

// --- demo car: data plus only the entity strings it needs, per language --------
const demoData = JSON.parse(demo);
const strings = {};
for (const lang of ["en", "de"]) {
  const tr = JSON.parse(
    readFileSync(join(ROOT, "custom_components", "bavariandata", "translations", `${lang}.json`), "utf8")
  ).entity;
  const names = {};
  const states = {};
  for (const [eid, ent] of Object.entries(demoData.entities)) {
    const block = (tr[eid.split(".")[0]] || {})[ent.translation_key] || {};
    if (block.name) names[eid] = block.name;
    if (block.state) states[eid] = block.state;
  }
  strings[lang] = { names, states };
}
writeFileSync(join(PUBLIC, "demo", "i5.json"), JSON.stringify({ ...demoData, strings }));

// --- screenshots --------------------------------------------------------------
const shots = {};
const shotDir = join(ROOT, "screenshots");
for (const file of readdirSync(shotDir).filter((f) => f.endsWith(".png"))) {
  const name = basename(file, ".png");
  const img = sharp(join(shotDir, file));
  const { width, height } = await img.metadata();
  shots[name] = { width, height };
  await img.webp({ quality: 84 }).toFile(join(PUBLIC, "screenshots", `${name}.webp`));
}
await sharp(join(SITE, "src", "demo", "i5.png")).webp({ quality: 86 }).toFile(join(PUBLIC, "demo", "i5.webp"));

// --- favicons and the social preview -----------------------------------------
const icon = join(ROOT, "custom_components", "bavariandata", "brand", "icon@2x.png");
await sharp(icon).resize(32).png().toFile(join(PUBLIC, "favicon-32.png"));
await sharp(icon).resize(180).png().toFile(join(PUBLIC, "apple-touch-icon.png"));
await sharp(icon).resize(512).png().toFile(join(PUBLIC, "icon-512.png"));
copyFileSync(join(ROOT, "logo.png"), join(PUBLIC, "logo.png"));

const og = (title, line) => `
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0B1A3D"/><stop offset="1" stop-color="#16408F"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <path d="M-20 470 C 260 380, 420 560, 700 470 S 1100 380, 1240 450" fill="none" stroke="#FFFFFF" stroke-opacity=".14" stroke-width="26"/>
  <path d="M-20 520 C 260 430, 420 610, 700 520 S 1100 430, 1240 500" fill="none" stroke="#7CCBF6" stroke-opacity=".55" stroke-width="30"/>
  <text x="96" y="250" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="84" font-weight="700" fill="#FFFFFF">${title}</text>
  <text x="96" y="320" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="36" fill="#C9DAF5">${line}</text>
</svg>`;
const ogIcon = await sharp(icon).resize(120).png().toBuffer();
for (const [file, title, line] of [
  ["og.png", "BavarianData", "Your BMW, live in Home Assistant — via BMW CarData"],
  ["og-de.png", "BavarianData", "Dein BMW, live in Home Assistant — über BMW CarData"],
]) {
  await sharp(Buffer.from(og(title, line)))
    .composite([{ input: ogIcon, left: 96, top: 60 }])
    .png()
    .toFile(join(PUBLIC, file));
}

// --- the changelog ------------------------------------------------------------
// CHANGELOG.md stays the one source. The site shows stable releases only
// (betas are working steps toward them and are folded into the stable's entry),
// newest first, with every release linked to its GitHub page. Relative links
// are pointed at the repository, where those files live.
const REPO_URL = "https://github.com/JustChr/BavarianData";
const log = readFileSync(join(ROOT, "CHANGELOG.md"), "utf8").replace(/\r\n/g, "\n");
const releases = [...log.matchAll(/^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\n([\s\S]*?)(?=^## \[|(?![\s\S]))/gm)].map(
  ([, version, date, body]) => ({ version, date, body: body.trim() })
);
const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const longDate = (iso) => {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${months[m - 1]} ${y}`;
};
const fixLinks = (md) =>
  md.replace(/\]\((?!https?:|#|mailto:)([^)\s]+)\)/g, (_, path) => `](${REPO_URL}/blob/main/${path.replace(/^\.?\//, "")})`);
writeFileSync(
  join(GEN, "changelog.md"),
  [
    "# What's new",
    "",
    ...releases.flatMap((r) => [
      `<h2 id="v${r.version}">${r.version} <small>${longDate(r.date)}</small></h2>`,
      "",
      `[Release on GitHub](${REPO_URL}/releases/tag/v${r.version})`,
      "",
      fixLinks(r.body),
      "",
    ]),
  ].join("\n")
);
writeFileSync(join(GEN, "releases.json"), JSON.stringify(releases.map(({ version, date }) => ({ version, date }))) + "\n");

writeFileSync(
  join(GEN, "assets.json"),
  JSON.stringify({ cardVersion, icons, screenshots: shots }, null, 1) + "\n"
);
console.log(`prepare: ${releases.length} releases in the changelog, card ${cardVersion}, ${Object.keys(icons).length} icons, ${Object.keys(shots).length} screenshots`);
