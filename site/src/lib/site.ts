// Build-time facts about the project, read from the repository itself so the
// site can never advertise a version, a floor or a date the repo doesn't hold.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { REPO_ROOT, url, docPath, PAGES, sidebar, SITE_URL, REPO } from "./wiki.mjs";

export { url, docPath, PAGES, sidebar, SITE_URL, REPO };
export type Lang = "en" | "de";

const read = (...parts: string[]) => readFileSync(join(REPO_ROOT, ...parts), "utf8");

const changelog = read("CHANGELOG.md");
const stable = /^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})/m.exec(changelog);
/** The newest stable release -- what HACS offers by default. */
export const RELEASE = { version: stable?.[1] ?? "", date: stable?.[2] ?? "" };

/** The Home Assistant floor, from hacs.json. */
export const HA_MIN = JSON.parse(read("hacs.json")).homeassistant.replace(/\.0$/, "");

/** Descriptors in BMW's catalogue. */
export const DESCRIPTOR_COUNT = JSON.parse(read("custom_components", "bavariandata", "catalogue.json")).descriptors
  .length as number;

/** The last commit that touched a file, as an ISO date (the page's dateModified). */
export function lastModified(relativePath: string): string | undefined {
  try {
    const out = execFileSync("git", ["log", "-1", "--format=%cI", "--", relativePath], {
      cwd: REPO_ROOT,
      encoding: "utf8",
    }).trim();
    return out || undefined;
  } catch {
    return undefined;
  }
}

/** The page's path in the other language. */
export function otherLang(lang: Lang): Lang {
  return lang === "en" ? "de" : "en";
}

/** A path prefixed for a language: `docs/x` -> `de/docs/x`. */
export function localized(path: string, lang: Lang): string {
  return lang === "de" ? `de/${path}`.replace(/\/$/, "") : path;
}

export const HACS_REDIRECT =
  "https://my.home-assistant.io/redirect/hacs_repository/?owner=JustChr&repository=BavarianData&category=integration";

export const absolute = (path: string) => `${SITE_URL}${url(path)}`;
