#!/usr/bin/env python3
"""Fetch BMW's telematics catalogue in every language we ship, and say what changed.

BMW publishes the catalogue per market; the locale in the URL picks the language.
The German export is the pipeline's main input (sections, types, units, the
streamable tick); the others only name the fields (``catalogue_i18n/``). This
downloads all of them to a temporary folder and compares them *by descriptor*
with the committed copies, so a reformatted page reads as "no change":

* descriptors BMW **added** or **removed**,
* a changed data type, unit, section, value range or **streamable** flag
  (German export),
* a renamed field, in any language.

    python tools/fetch_catalogues.py           # report only; touches nothing
    python tools/fetch_catalogues.py --write   # also replace the committed copies

After ``--write``, run the ``regen`` skill (steps 1-5) and the tests: a new unit
spelling stops step 2 on purpose. The English ``descriptor-list.csv`` (enum
value ranges, sub-categories) is a separate portal download and is not fetched.

Exit status: 0 nothing changed, 1 changes found, 2 a download failed.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

TOOLS = Path(__file__).resolve().parent
URL = "https://mybmwweb-utilities.api.bmw/{locale}/utilities/bmw/api/cd/catalogue/file"

# target file (relative to tools/) -> market locale. German is de-at, as the
# pipeline has always used; the rest match translations/<lang>.json.
EXPORTS: dict[str, str] = {
    "CustomerTelematicsDataCatalogue.html": "de-at",
    "catalogue_i18n/fr.html": "fr-fr",
    "catalogue_i18n/it.html": "it-it",
    "catalogue_i18n/es.html": "es-es",
    "catalogue_i18n/nl.html": "nl-nl",
    "catalogue_i18n/pl.html": "pl-pl",
    "catalogue_i18n/pt.html": "pt-pt",
    "catalogue_i18n/cs.html": "cs-cz",
    "catalogue_i18n/sv.html": "sv-se",
}
MAIN = "CustomerTelematicsDataCatalogue.html"
# Fields of the German export whose change alters what the integration builds.
# ``value_range_de`` matters because enum options fall back to it, and it is
# where BMW corrected charging.status's copy-pasted list in 2026-09.
TRACKED = ("data_type", "unit", "section", "streamable", "value_range_de")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_catalogue", TOOLS / "build_catalogue.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def download(locale: str, dest: Path) -> None:
    request = urllib.request.Request(
        URL.format(locale=locale),
        headers={"User-Agent": "BavarianData catalogue refresh (github.com/JustChr/BavarianData)"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    # A blocked request or an unknown locale comes back small and without the
    # catalogue's section headings; never let that replace a real export.
    if len(data) < 50_000 or b"<h3>" not in data:
        raise ValueError(f"{locale}: {len(data)} bytes, not a catalogue export")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def compare(old: dict[str, dict], new: dict[str, dict], *, main: bool) -> list[str]:
    lines: list[str] = []
    for descriptor in sorted(set(new) - set(old)):
        lines.append(f"  + {descriptor}  ({new[descriptor]['name_de']})")
    for descriptor in sorted(set(old) - set(new)):
        lines.append(f"  - {descriptor}  ({old[descriptor]['name_de']})")
    for descriptor in sorted(set(old) & set(new)):
        before, after = old[descriptor], new[descriptor]
        if before["name_de"] != after["name_de"]:
            lines.append(f"  ~ {descriptor}: name {before['name_de']!r} -> {after['name_de']!r}")
        if main:
            for field in TRACKED:
                if before.get(field) != after.get(field):
                    lines.append(
                        f"  ~ {descriptor}: {field} {before.get(field)!r} -> {after.get(field)!r}"
                    )
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="replace the committed exports")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    builder = _load_builder()
    staging = Path(tempfile.mkdtemp(prefix="bmw_catalogue_"))
    changed_files: list[str] = []
    failed = False
    try:
        for rel, locale in EXPORTS.items():
            fresh = staging / rel
            try:
                download(locale, fresh)
            except (OSError, ValueError) as err:
                print(f"{rel}: download failed -- {err}")
                failed = True
                continue
            committed = TOOLS / rel
            old = builder.parse_html(committed) if committed.exists() else {}
            diff = compare(old, builder.parse_html(fresh), main=rel == MAIN)
            if diff:
                changed_files.append(rel)
                print(f"{rel} ({locale}): {len(diff)} change(s)")
                print("\n".join(diff))
            else:
                print(f"{rel} ({locale}): unchanged")

        if args.write and changed_files and not failed:
            for rel in changed_files:
                shutil.copyfile(staging / rel, TOOLS / rel)
            print(f"\nWrote {len(changed_files)} file(s). Next: the regen skill, then the tests.")
        elif args.write and failed:
            print(
                "\nNot writing anything: a download failed, and a partial refresh would mix versions."
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if failed:
        return 2
    if changed_files and not args.write:
        print("\nRe-run with --write to take these, then the regen skill.")
    return 1 if changed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
