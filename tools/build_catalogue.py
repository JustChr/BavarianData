#!/usr/bin/env python3
"""Build the canonical BMW CarData descriptor catalogue.

Two BMW source exports are joined on the technical descriptor:

* ``CustomerTelematicsDataCatalogue.html`` — the German portal export. Provides
  BMW's own top-level *sections* (the ``<h3>`` headings) plus German element
  names and descriptions.
* ``descriptor-list.csv`` — the English export. Provides English names and
  descriptions, the finer sub-category, and the raw (untranslated) enum value
  ranges we need for state options.

A third, project-authored input — ``curated_titles.json`` — supplies the curated
English display names shown in Home Assistant (``title_en``). These are our own
naming scheme (e.g. cluster-prefixed "Battery EV …") and take precedence over
BMW's raw ``element_en``/``name_en`` so entities keep stable, friendly names.

The result is written to ``custom_components/bavariandata/catalogue.json`` and is
the single source of truth for the metadata registry and translations.
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_FILE = REPO_ROOT / "tools" / "CustomerTelematicsDataCatalogue.html"
CSV_FILE = REPO_ROOT / "tools" / "descriptor-list.csv"
TITLES_FILE = REPO_ROOT / "tools" / "curated_titles.json"
OUTPUT_FILE = REPO_ROOT / "custom_components" / "bavariandata" / "catalogue.json"

# BMW's German section headings mapped to stable English section keys/labels.
SECTION_MAP = {
    "FAHRZEUGBASISDATEN": ("basic", "Vehicle basic data"),
    "DATEN ZUM FAHRZEUGSTATUS": ("status", "Vehicle status"),
    "NUTZUNGSBASIERTE DATEN EINES FAHRZEUGS": ("usage", "Usage-based data"),
    "DATEN ZU DEN EREIGNISSEN EINES FAHRZEUGS": ("events", "Vehicle events"),
    "DATEN ZU ELEKTRISCHEN FAHRZEUGEN": ("electric", "Electric vehicle"),
    "METADATEN": ("metadata", "Metadata"),
    "REIFENDATEN": ("tire", "Tire data"),
    "INFORMATIONEN ZU DEN CONNECTEDDRIVE VERTRAGSDETAILS": (
        "contract",
        "ConnectedDrive contract",
    ),
}


def clean(text: str) -> str:
    """Unescape HTML entities and strip zero-width / non-breaking whitespace."""

    text = html.unescape(text)
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_html() -> "OrderedDict[str, dict]":
    raw = HTML_FILE.read_text(encoding="utf-8")
    entries: "OrderedDict[str, dict]" = OrderedDict()
    # Split the document into (heading, table-body) chunks in document order.
    parts = re.split(r"<h3>(.*?)</h3>", raw, flags=re.S)
    # parts[0] is the preamble; then alternating heading, body, heading, body...
    for i in range(1, len(parts), 2):
        heading = clean(parts[i])
        body = parts[i + 1]
        section_key, section_label = SECTION_MAP.get(
            heading.upper(), ("other", heading.title())
        )
        for row in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S):
            cols = re.findall(r'<td class="([^"]+)">(.*?)</td>', row, flags=re.S)
            # Cells may carry extra classes (e.g. "col1-emea top-column"); key by
            # the leading colN token so the lookup is stable.
            byclass = {
                classes.split()[0]: clean(value)
                for classes, value in cols
                if classes.split()
            }
            descriptor = byclass.get("col3", "")
            if not descriptor.startswith("vehicle"):
                continue
            entries[descriptor] = {
                "descriptor": descriptor,
                "section": section_key,
                "section_label": section_label,
                "name_de": byclass.get("col1-emea", ""),
                "description_de": byclass.get("col2-emea", ""),
                "data_type": byclass.get("col4", ""),
                "value_range_de": byclass.get("col5", ""),
                "unit": byclass.get("col6-emea", ""),
            }
    return entries


def parse_csv() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with CSV_FILE.open(encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if len(row) < 11:
                continue
            descriptor = clean(row[7])
            if not descriptor.startswith("vehicle"):
                continue
            out[descriptor] = {
                "element_en": clean(row[1]),
                "description_en": clean(row[2]),
                "category": clean(row[3]),
                "name_en": clean(row[4]),
                "data_type": clean(row[8]),
                "value_range_en": clean(row[9]),
                "unit": clean(row[10]),
            }
    return out


def normalize_unit_and_range(raw_unit: str) -> str:
    unit = raw_unit.strip()
    return "" if unit in {"-", ""} else unit


def load_curated_titles() -> dict[str, str]:
    data = json.loads(TITLES_FILE.read_text(encoding="utf-8"))
    return data.get("titles", {})


def main() -> None:
    de = parse_html()
    en = parse_csv()
    titles = load_curated_titles()
    descriptors = list(OrderedDict.fromkeys(list(de) + list(en)))

    catalogue: list[dict] = []
    for descriptor in sorted(descriptors):
        d = de.get(descriptor, {})
        e = en.get(descriptor, {})
        unit = normalize_unit_and_range(d.get("unit") or e.get("unit") or "")
        entry = {
            "descriptor": descriptor,
            "section": d.get("section", "other"),
            "section_label": d.get("section_label", "Other"),
            "category": e.get("category", ""),
            "title_en": titles.get(descriptor, ""),
            "name_en": e.get("name_en", ""),
            "element_en": e.get("element_en", ""),
            "name_de": d.get("name_de", ""),
            "description_en": e.get("description_en", ""),
            "description_de": d.get("description_de", ""),
            "data_type": d.get("data_type") or e.get("data_type") or "",
            "value_range_en": e.get("value_range_en", ""),
            "value_range_de": d.get("value_range_de", ""),
            "unit": unit,
        }
        catalogue.append(entry)

    payload = {
        "_generated_by": "tools/build_catalogue.py",
        "_sections": {k: v[1] for k, v in SECTION_MAP.items()},
        "descriptors": catalogue,
    }
    OUTPUT_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(catalogue)} descriptors to {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    only_de = set(de) - set(en)
    only_en = set(en) - set(de)
    if only_de:
        print(f"  only in DE export: {sorted(only_de)}")
    if only_en:
        print(f"  only in EN export: {sorted(only_en)}")
    stray_titles = set(titles) - set(descriptors)
    if stray_titles:
        print(f"  curated titles with no descriptor: {sorted(stray_titles)}")


if __name__ == "__main__":
    main()
