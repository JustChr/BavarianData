"""Guards on the shipped service surface and on what ships alongside it.

Two classes of regression, both Home Assistant-free:

* **Privacy.** Real VINs and config entry ids have been pasted into shipped
  files before (log dumps kept as comments, a real VIN used as every
  ``example:`` in ``services.yaml``). Those files are rendered to every user and
  read by anyone reviewing the repo, so a VIN-shaped literal is a leak. Only the
  documented synthetic placeholder is allowed.
* **Translation coverage.** ``services.yaml`` carries schemas only; every
  user-facing name and description lives in ``translations/{en,de}.json``. If a
  new action lands without both languages, German installs silently fall back to
  the raw action key -- the same failure mode the entity pipeline exists to
  prevent.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "custom_components" / "bavariandata"

SERVICES = yaml.safe_load((_PKG / "services.yaml").read_text(encoding="utf-8"))
EN = json.loads((_PKG / "translations" / "en.json").read_text(encoding="utf-8"))
DE = json.loads((_PKG / "translations" / "de.json").read_text(encoding="utf-8"))

# The one VIN allowed to appear anywhere in the repo.
PLACEHOLDER_VIN = "WBAEXAMPLE0000000"
# BMW VINs start with the WB* world manufacturer identifier and run 17 chars,
# excluding I/O/Q. Narrow on purpose: a generic 17-char matcher would trip over
# descriptor names and hashes.
VIN_RE = re.compile(r"\bWB[A-Z0-9][A-HJ-NPR-Z0-9]{14}\b")
# Config entry ids are ULIDs (Crockford base32, no I/L/O/U) and always start 01.
ULID_RE = re.compile(r"\b01[0-9ABCDEFGHJKMNPQRSTVWXYZ]{24}\b")

TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".js", ".md"}


def _shipped_files() -> list[pathlib.Path]:
    return [
        p
        for p in _PKG.rglob("*")
        if p.is_file()
        and p.suffix in TEXT_SUFFIXES
        and "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize("path", _shipped_files(), ids=lambda p: p.name)
def test_no_real_vehicle_identifiers_are_shipped(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    vins = {v for v in VIN_RE.findall(text) if v != PLACEHOLDER_VIN}
    assert not vins, (
        f"{path.relative_to(_ROOT)} contains VIN-shaped literal(s) {sorted(vins)}. "
        f"Use the placeholder {PLACEHOLDER_VIN}."
    )
    assert not ULID_RE.findall(text), (
        f"{path.relative_to(_ROOT)} contains a config-entry-id-shaped literal; "
        "entry ids are per-install and must never be hardcoded as examples."
    )


@pytest.mark.parametrize("lang,doc", [("en", EN), ("de", DE)])
def test_every_action_is_translated(lang: str, doc: dict) -> None:
    translated = doc.get("services", {})
    assert set(translated) == set(SERVICES), (
        f"{lang}.json services block is out of sync with services.yaml: "
        f"missing {sorted(set(SERVICES) - set(translated))}, "
        f"extra {sorted(set(translated) - set(SERVICES))}"
    )
    for action, spec in SERVICES.items():
        entry = translated[action]
        assert entry.get("name"), f"{lang}.{action} has no name"
        assert entry.get("description"), f"{lang}.{action} has no description"
        schema_fields = set((spec or {}).get("fields") or {})
        text_fields = set(entry.get("fields") or {})
        assert schema_fields == text_fields, (
            f"{lang}.{action} fields differ from the schema: "
            f"missing {sorted(schema_fields - text_fields)}, "
            f"extra {sorted(text_fields - schema_fields)}"
        )


def test_services_yaml_carries_no_user_facing_text() -> None:
    """Names/descriptions belong in translations, or the two sources drift."""

    for action, spec in SERVICES.items():
        assert "name" not in (spec or {}), f"{action}: move name to translations"
        assert "description" not in (spec or {}), (
            f"{action}: move description to translations"
        )
        for field, fspec in ((spec or {}).get("fields") or {}).items():
            assert "description" not in (fspec or {}), (
                f"{action}.{field}: move description to translations"
            )


@pytest.mark.parametrize("lang,doc", [("en", EN), ("de", DE)])
def test_translations_contain_no_urls(lang: str, doc: dict) -> None:
    """hassfest rejects URLs inside translation strings."""

    urls = re.findall(r"https?://[^\s\"]+", json.dumps(doc, ensure_ascii=False))
    assert not urls, f"{lang}.json contains URL(s): {urls[:5]}"
