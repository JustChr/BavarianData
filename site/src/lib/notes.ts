// What we know about a descriptor beyond BMW's catalogue line, from
// data/descriptor-notes.json (its _comment has the rules). A descriptor with
// notes is "featured": its page carries them and is the only kind of
// descriptor page offered to search engines.

import raw from "../data/descriptor-notes.json";
import { DESCRIPTORS, nameOf } from "./catalogue";
import { url, localized, docPath, type Lang } from "./site";

type Note = Record<Lang, string[]>;

const entries = raw as Record<string, Note | string>;
const known = new Map(DESCRIPTORS.map((d) => [d.descriptor, d]));

/** Descriptor -> its notes; an entry may name a shared block instead. */
export const NOTES = new Map<string, Note>();
for (const [key, value] of Object.entries(entries)) {
  if (!key.startsWith("vehicle.")) continue;
  const note = typeof value === "string" ? entries[value] : value;
  if (!note || typeof note === "string") throw new Error(`descriptor-notes.json: ${key} points at no notes`);
  NOTES.set(key, note);
}

const escape = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function href(target: string, lang: Lang): string {
  if (target.startsWith("https://")) return target;
  if (target.startsWith("data:")) {
    const descriptor = target.slice(5);
    if (!known.has(descriptor)) throw new Error(`descriptor-notes.json links to unknown ${descriptor}`);
    return url(localized(`data/${descriptor}`, lang));
  }
  const [page, hash] = target.split("#");
  const path = docPath(page, lang);
  if (!path) throw new Error(`descriptor-notes.json links to unknown wiki page ${page}`);
  return url(hash ? `${path}#${hash}` : path);
}

/** One paragraph as HTML: `code`, **bold** and [text](target) links. */
export function renderNote(text: string, lang: Lang): string {
  return escape(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (_, label: string, target: string) => {
      const text = label || escape(nameOf(known.get(target.slice(5))!, lang));
      const external = target.startsWith("https://") ? ' rel="noopener"' : "";
      return `<a href="${href(target, lang)}"${external}>${text}</a>`;
    });
}

/** The same paragraph as plain text, for a meta description. */
export function plainNote(text: string, lang: Lang): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (_, label: string, target: string) =>
      label || nameOf(known.get(target.slice(5))!, lang)
    );
}
