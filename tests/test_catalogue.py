"""Consistency tests for the generated CarData catalogue and metadata.

These are Home Assistant-free: they validate the static data files and the
generators that produce them, so the field clustering/translation pipeline
cannot silently drift.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _PKG / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CATALOGUE = json.loads((_PKG / "catalogue.json").read_text(encoding="utf-8"))
META = _load("descriptor_metadata", "descriptor_metadata.py").DESCRIPTOR_META
KEYS = _load("keys", "keys.py")
EN = json.loads((_PKG / "translations" / "en.json").read_text(encoding="utf-8"))
DE = json.loads((_PKG / "translations" / "de.json").read_text(encoding="utf-8"))

DESCRIPTORS = [e["descriptor"] for e in CATALOGUE["descriptors"]]


def test_catalogue_has_descriptors():
    assert len(DESCRIPTORS) > 250
    assert len(DESCRIPTORS) == len(set(DESCRIPTORS)), "duplicate descriptors"


def test_every_descriptor_has_metadata():
    assert set(META) == set(DESCRIPTORS)


def test_translation_keys_are_unique_and_slugified():
    keys = [KEYS.translation_key(d) for d in DESCRIPTORS]
    assert len(keys) == len(set(keys)), "translation key collision"
    for key in keys:
        assert key and all(c.islower() or c.isdigit() or c == "_" for c in key)


def test_every_descriptor_is_translated_in_both_languages():
    for descriptor in DESCRIPTORS:
        key = KEYS.translation_key(descriptor)
        for lang in (EN, DE):
            sensor = lang["entity"]["sensor"]
            assert key in sensor and sensor[key]["name"], f"missing {key}"


def test_enum_options_have_english_state_labels():
    for descriptor, meta in META.items():
        options = meta.get("options") or []
        if not options:
            continue
        key = KEYS.translation_key(descriptor)
        states = EN["entity"]["sensor"][key].get("state", {})
        for option in options:
            assert option in states, f"{key} missing state label for {option}"


def test_device_class_units_are_consistent():
    # A pinned device class must carry a unit (except duration which HA infers).
    unit_required = {
        "temperature",
        "voltage",
        "current",
        "power",
        "energy",
        "energy_storage",
        "distance",
        "speed",
        "pressure",
        "battery",
        "volume_storage",
    }
    for meta in META.values():
        if meta["device_class"] in unit_required:
            assert meta["unit"], f"{meta['device_class']} without unit"


def test_diagnostic_fields_are_disabled_by_default():
    for meta in META.values():
        if meta["entity_category"] == "diagnostic":
            assert meta["enabled_default"] is False


def test_generators_are_idempotent(tmp_path):
    # Regenerating from the committed sources must reproduce the committed files.
    build = _load("build_catalogue", str(pathlib.Path(__file__).parents[1] / "tools" / "build_catalogue.py"))
    meta_gen = _load("generate_metadata", str(pathlib.Path(__file__).parents[1] / "tools" / "generate_metadata.py"))

    before_cat = (_PKG / "catalogue.json").read_text(encoding="utf-8")
    before_meta = (_PKG / "descriptor_metadata.py").read_text(encoding="utf-8")
    build.main()
    meta_gen.main()
    assert (_PKG / "catalogue.json").read_text(encoding="utf-8") == before_cat
    assert (_PKG / "descriptor_metadata.py").read_text(encoding="utf-8") == before_meta
