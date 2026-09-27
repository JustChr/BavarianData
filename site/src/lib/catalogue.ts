// BMW's descriptor catalogue, as the integration ships it (catalogue.json is
// generated from BMW's own exports by tools/), shaped for the data reference.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { REPO_ROOT } from "./wiki.mjs";
import type { Lang } from "./site";

export type Descriptor = {
  descriptor: string;
  section: string;
  section_label: string;
  category: string;
  title_en: string;
  name_de: string;
  names_i18n: Record<string, string>;
  description_en: string;
  description_de: string;
  data_type: string;
  value_range_en: string;
  value_range_de: string;
  unit: string;
  streamable: boolean;
};

export const DESCRIPTORS: Descriptor[] = JSON.parse(
  readFileSync(join(REPO_ROOT, "custom_components", "bavariandata", "catalogue.json"), "utf8")
).descriptors.sort((a: Descriptor, b: Descriptor) => a.descriptor.localeCompare(b.descriptor));

export const SECTION_ORDER = ["electric", "status", "usage", "tire", "events", "basic", "metadata", "contract", "other"];

export const SECTION_LABELS: Record<Lang, Record<string, string>> = {
  en: {
    electric: "Electric vehicle",
    status: "Vehicle status",
    usage: "Usage-based data",
    tire: "Tire data",
    events: "Vehicle events",
    basic: "Vehicle basic data",
    metadata: "Metadata",
    contract: "ConnectedDrive contract",
    other: "Other",
  },
  de: {
    electric: "Elektrofahrzeug",
    status: "Fahrzeugstatus",
    usage: "Nutzungsbasierte Daten",
    tire: "Reifendaten",
    events: "Fahrzeugereignisse",
    basic: "Fahrzeugbasisdaten",
    metadata: "Metadaten",
    contract: "ConnectedDrive-Vertrag",
    other: "Sonstiges",
  },
};

export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  de: "Deutsch",
  fr: "Français",
  it: "Italiano",
  es: "Español",
  nl: "Nederlands",
  pl: "Polski",
  pt: "Português",
  cs: "Čeština",
  sv: "Svenska",
};

export const nameOf = (d: Descriptor, lang: Lang) => (lang === "de" ? d.name_de || d.title_en : d.title_en);
export const descriptionOf = (d: Descriptor, lang: Lang) =>
  (lang === "de" ? d.description_de || d.description_en : d.description_en || d.description_de || "").trim();

/** A search-result-sized summary: the first sentence or ~155 characters. */
export function summary(text: string, max = 155) {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max);
  const stop = cut.lastIndexOf(". ");
  return stop > 80 ? cut.slice(0, stop + 1) : `${cut.slice(0, cut.lastIndexOf(" "))}…`;
}
