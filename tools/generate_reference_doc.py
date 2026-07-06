#!/usr/bin/env python3
"""Generate the human-readable descriptor reference from the catalogue.

Writes ``docs/reference/telematics-fields.md`` — one table per BMW section, so
users can look up what a field means, its unit, and whether it is enabled by
default, without visiting BMW's portal.
"""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "custom_components" / "bavariandata"
CATALOGUE_FILE = PKG / "catalogue.json"
OUTPUT_FILE = REPO_ROOT / "docs" / "reference" / "telematics-fields.md"

_spec = importlib.util.spec_from_file_location("dm", PKG / "descriptor_metadata.py")
_dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dm)
META = _dm.DESCRIPTOR_META

# Display order for BMW's sections.
SECTION_ORDER = [
    ("basic", "Vehicle basic data"),
    ("status", "Vehicle status"),
    ("usage", "Usage-based data"),
    ("events", "Vehicle events"),
    ("electric", "Electric vehicle"),
    ("tire", "Tire data"),
    ("metadata", "Metadata"),
    ("contract", "ConnectedDrive contract"),
    ("other", "Other"),
]


def esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def main() -> None:
    data = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    by_section: dict[str, list[dict]] = defaultdict(list)
    for entry in data["descriptors"]:
        by_section[entry["section"]].append(entry)

    lines = [
        "# BMW CarData telematic fields",
        "",
        "Generated from BMW's telematic data catalogue by "
        "`tools/generate_reference_doc.py` — do not edit by hand.",
        "",
        "Fields are grouped by BMW's own top-level sections. **Default** shows "
        "whether the entity is created enabled; the technical long tail is "
        "created disabled and can be turned on per entity in Home Assistant.",
        "",
    ]

    total = 0
    for section_key, section_label in SECTION_ORDER:
        entries = by_section.get(section_key)
        if not entries:
            continue
        entries.sort(key=lambda e: e["descriptor"])
        lines.append(f"## {section_label} ({len(entries)})")
        lines.append("")
        lines.append("| Name (EN) | Name (DE) | Descriptor | Unit | Default | Description |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for e in entries:
            meta = META.get(e["descriptor"], {})
            unit = meta.get("unit") or "—"
            default = "on" if meta.get("enabled_default", True) else "off"
            name_en = esc(e["name_en"] or e["element_en"] or "")
            name_de = esc(e["name_de"] or "")
            desc = esc(e["description_en"] or e["description_de"] or "")
            if len(desc) > 160:
                desc = desc[:157] + "…"
            lines.append(
                f"| {name_en} | {name_de} | `{e['descriptor']}` | {unit} | "
                f"{default} | {desc} |"
            )
        lines.append("")
        total += len(entries)

    lines.insert(4, f"**{total} fields** across {sum(1 for k, _ in SECTION_ORDER if by_section.get(k))} sections.")
    lines.insert(5, "")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {total} fields to {OUTPUT_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
