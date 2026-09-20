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

import ast
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


# hassfest's sibling rule to the URL one, and an easier trap to fall into: a
# translation string may contain no HTML. Anything shaped like a tag counts, so
# the natural way to write a placeholder -- <prefix>/<VIN>/soc -- fails the whole
# integration's validation. Use a notation that does not look like markup
# (PREFIX/VIN/soc). Caught here, after it had already gone out in a release.
_HTML_LIKE = re.compile(r"<\s*/?\s*[A-Za-z][^<>]*>")


@pytest.mark.parametrize("lang,doc", [("en", EN), ("de", DE)])
def test_translations_contain_no_html(lang: str, doc: dict) -> None:
    """hassfest rejects anything tag-shaped inside translation strings."""

    found: list[str] = []

    def walk(node, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, str):
            for match in _HTML_LIKE.findall(node):
                found.append(f"{path}: {match}")

    walk(doc)
    assert not found, (
        f"{lang}.json contains HTML-like text, which hassfest rejects: "
        f"{found[:5]}. Write placeholders without angle brackets."
    )


# --------------------------------------------------------------------------
# Identifiers that escape the diagnostics redaction
# --------------------------------------------------------------------------
#
# ``diagnostics.py`` builds a deliberately safe payload and runs
# ``async_redact_data`` over it, but that only covers *our* payload. Home
# Assistant's own diagnostics wrapper appends the registered repair issues to
# the download, and a repair id is chosen by us -- so a VIN embedded in one
# bypasses the redaction entirely and lands in the file we ask users to attach
# to public issues. That is how a real VIN reached issue #6.


def _coverage_module():
    from .conftest import load_module

    return load_module("coverage")


SAMPLE_VIN = "WBA00000000000000"
SAMPLE_ENTRY = "01ABCDEF0123456789ABCDEFGH"


def test_the_repair_id_does_not_carry_the_vin() -> None:
    issue_id = _coverage_module().coverage_issue_id(SAMPLE_ENTRY, SAMPLE_VIN)
    assert SAMPLE_VIN not in issue_id, (
        f"{issue_id!r} embeds the VIN. Repair ids are appended to the "
        "diagnostics download by Home Assistant, outside our redaction."
    )
    assert VIN_RE.search(issue_id) is None


def test_the_repair_id_is_stable_and_per_vehicle() -> None:
    """It has to survive a restart to clear an existing repair, and not collide."""

    coverage = _coverage_module()
    again = coverage.coverage_issue_id(SAMPLE_ENTRY, SAMPLE_VIN)
    assert again == coverage.coverage_issue_id(SAMPLE_ENTRY, SAMPLE_VIN)
    other = coverage.coverage_issue_id(SAMPLE_ENTRY, "WBA00000000000001")
    assert again != other
    assert again != coverage.coverage_issue_id("01ZZZZZZZZZZZZZZZZZZZZZZZZ", SAMPLE_VIN)


def test_the_leaking_id_is_still_derivable_so_it_can_be_deleted() -> None:
    """An install that already raised one must have it cleared, not orphaned."""

    coverage = _coverage_module()
    legacy = coverage.legacy_coverage_issue_id(SAMPLE_ENTRY, SAMPLE_VIN)
    assert legacy.endswith(SAMPLE_VIN)
    assert legacy != coverage.coverage_issue_id(SAMPLE_ENTRY, SAMPLE_VIN)


# --- service handlers must not close over the entry that registered them ----
#
# The services are registered process-wide, exactly once, by whichever config
# entry sets up first. Every handler therefore has ``async_setup_entry``'s own
# ``entry``/``runtime_data`` in scope -- and using one silently serves the wrong
# account (a second car filed under the first account's entry), then fails
# outright once that first entry is removed while another stays loaded. The
# handler must work only from what ``_resolve_target`` hands it.
#
# Checked on the source rather than at runtime because there is no Home
# Assistant in this harness, and because the failure is invisible until someone
# owns two accounts: the reviewable artefact is the name lookup itself.

_SETUP_SCOPE_NAMES = frozenset(
    {
        "entry",
        "runtime_data",
        "coordinator",
        "history_store",
        "coverage_store",
        "tyre_store",
        "quota",
        "session",
        "client_id",
        "gcid",
    }
)


def _service_registration_block(tree: "ast.AST") -> "ast.If":
    """The ``if not domain_data.get("_service_registered"):`` body."""

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        source = ast.dump(node.test)
        if "_service_registered" in source:
            return node
    raise AssertionError("service registration block not found in __init__.py")


def _bound_names(node: "ast.AST") -> set[str]:
    """Names a function binds itself (args, assignments, loops, with, except)."""

    bound: set[str] = set()
    args = getattr(node, "args", None)
    if args is not None:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            bound.add(arg.arg)
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                bound.add(extra.arg)
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            bound.add(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(child.name)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
        elif isinstance(child, ast.alias):
            bound.add((child.asname or child.name).split(".")[0])
    return bound


def test_service_handlers_never_read_the_registering_entrys_scope() -> None:
    tree = ast.parse((_PKG / "__init__.py").read_text(encoding="utf-8"))
    block = _service_registration_block(tree)

    leaks: list[str] = []
    for handler in block.body:
        if not isinstance(handler, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = _bound_names(handler)
        for child in ast.walk(handler):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            if child.id in _SETUP_SCOPE_NAMES and child.id not in bound:
                leaks.append(f"{handler.name} reads '{child.id}' (line {child.lineno})")

    assert not leaks, (
        "service handlers must use only what _resolve_target returns, "
        "never async_setup_entry's own scope: " + "; ".join(leaks)
    )
