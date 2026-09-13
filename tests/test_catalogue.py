"""Consistency tests for the generated CarData catalogue and metadata.

These are Home Assistant-free: they validate the static data files and the
generators that produce them, so the field clustering/translation pipeline
cannot silently drift.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "custom_components" / "bavariandata"
_TOOLS = _ROOT / "tools"


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

DERIVED = {
    platform: entries
    for platform, entries in json.loads(
        (_TOOLS / "derived_entities.json").read_text(encoding="utf-8")
    ).items()
    if not platform.startswith("_")
}


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


def test_derived_keys_do_not_collide_with_descriptors():
    # A collision would let a derived entity silently take over a descriptor's
    # name (or vice versa) depending on generation order.
    descriptor_keys = {KEYS.translation_key(d) for d in DESCRIPTORS}
    for platform, entries in DERIVED.items():
        for key in entries:
            assert key not in descriptor_keys, f"{platform}.{key} collides"


def test_derived_entities_are_translated_in_both_languages():
    for platform, entries in DERIVED.items():
        for key, names in entries.items():
            assert names.get("en"), f"{platform}.{key} missing English name"
            assert names.get("de"), f"{platform}.{key} missing German name"
            for lang in (EN, DE):
                block = lang["entity"][platform]
                assert key in block and block[key]["name"], f"missing {platform}.{key}"


def test_literal_translation_keys_are_declared_in_derived_entities():
    """Every hardcoded ``_attr_translation_key`` must have a name to resolve.

    Catalogue-backed entities compute their key at runtime, so a string literal
    in the package means a non-catalogue entity — it belongs in
    ``tools/derived_entities.json`` or it will show up unnamed. Text-level on
    purpose: these modules import Home Assistant and cannot be imported here.
    """

    declared = {key for entries in DERIVED.values() for key in entries}
    pattern = re.compile(r"_attr_translation_key\s*=\s*[\"']([a-z0-9_]+)[\"']")
    for path in sorted(_PKG.glob("*.py")):
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            assert key in declared, f"{path.name}: '{key}' not in derived_entities.json"


def test_enum_options_have_english_state_labels():
    for descriptor, meta in META.items():
        options = meta.get("options") or []
        if not options:
            continue
        key = KEYS.translation_key(descriptor)
        states = EN["entity"]["sensor"][key].get("state", {})
        for option in options:
            assert option in states, f"{key} missing state label for {option}"


UNITS = _load("units", "units.py")


def test_every_catalogue_unit_is_one_the_project_can_name():
    """A unit missing from ``units.py`` costs a device class, silently.

    ``generate_metadata.device_and_state_class`` matches the canonical string
    exactly, and an unrecognised unit is passed through unchanged -- so it
    matches nothing and the descriptor loses its device class *and* its state
    class, taking unit conversion and long-term statistics with it. Nothing in
    the generated output looks wrong afterwards, which is why this is checked
    against the raw catalogue rather than the metadata.
    """

    unknown = {
        entry["unit"]: entry["descriptor"]
        for entry in CATALOGUE["descriptors"]
        if not UNITS.is_known(entry.get("unit"))
    }
    assert not unknown, (
        f"catalogue.json uses unit(s) units.py does not recognise: {unknown}. "
        "Add them to UNIT_ALIASES/CANONICAL_UNITS and decide in "
        "tools/generate_metadata.py whether they classify."
    )


def test_generated_units_are_already_canonical():
    """The registry must never carry a pass-through spelling."""

    for descriptor, meta in META.items():
        unit = meta["unit"]
        if unit is None:
            continue
        assert unit in UNITS.CANONICAL_UNITS, f"{descriptor} has non-canonical unit {unit!r}"


# The device classes that carry a feature rather than a label: conversion,
# statistics, the Energy dashboard, the card's tyre view. Each is derived purely
# from the descriptor's unit string, so a unit spelling change silently drops
# it -- which reads to a user as "my sensors lost their history", with nothing
# to point at. Same failure shape as issue #6.
UNIT_CRITICAL_DEVICE_CLASSES = {
    "vehicle.chassis.axle.row1.wheel.left.tire.pressure": "pressure",
    "vehicle.chassis.axle.row2.wheel.right.tire.pressureTarget": "pressure",
    "vehicle.vehicle.travelledDistance": "distance",
    "vehicle.drivetrain.batteryManagement.header": "battery",
    "vehicle.drivetrain.batteryManagement.maxEnergy": "energy_storage",
    "vehicle.powertrain.electric.battery.charging.power": "power",
    "vehicle.drivetrain.electricEngine.charging.acVoltage": "voltage",
    "vehicle.drivetrain.electricEngine.charging.acAmpere": "current",
    "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel": "volume_storage",
    "vehicle.cabin.hvac.preconditioning.configuration.defaultSettings.targetTemperature": "temperature",
}


def test_unit_critical_device_classes_survive_regeneration():
    for descriptor, device_class in UNIT_CRITICAL_DEVICE_CLASSES.items():
        assert descriptor in META, f"{descriptor} vanished from the catalogue"
        assert META[descriptor]["device_class"] == device_class, (
            f"{descriptor} lost device_class {device_class!r} "
            f"(now {META[descriptor]['device_class']!r}) -- check its unit in "
            "catalogue.json against units.py."
        )


def test_fuel_tank_level_is_not_a_battery():
    """The tank level must not pose as a battery.

    ``device_and_state_class`` tags any ``%`` field whose name contains ``.level``
    as a battery, which caught ``fuelSystem.level``: Home Assistant showed the
    tank as a battery, and the card's state-of-charge gauge could pick it -- on a
    plug-in hybrid, over the real HV state of charge. Its unit and state class
    must survive the fix, or the entity loses its long-term statistics.
    """

    tank = META["vehicle.drivetrain.fuelSystem.level"]
    assert tank["device_class"] is None
    assert (tank["unit"], tank["state_class"]) == ("%", "measurement")
    for descriptor, meta in META.items():
        if ".fuelsystem." in descriptor.lower():
            assert meta["device_class"] != "battery", descriptor

    # Without a device class the entity loses its battery icon and would fall
    # back to Home Assistant's generic one, so icons.json names it a fuel gauge.
    icons = json.loads((_PKG / "icons.json").read_text(encoding="utf-8"))
    key = KEYS.translation_key("vehicle.drivetrain.fuelSystem.level")
    assert icons["entity"]["sensor"][key]["default"] == "mdi:gas-station"


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
    # Classified on purpose with the unit left to the stream: BMW documents the
    # tank in "litres or gallons", and pinning L would mislabel a US car.
    unit_from_stream = {"vehicle.drivetrain.fuelSystem.remainingFuel"}
    for descriptor, meta in META.items():
        if meta["device_class"] in unit_required and descriptor not in unit_from_stream:
            assert meta["unit"], f"{descriptor}: {meta['device_class']} without unit"


def test_fuel_volume_is_classified_without_pinning_litres():
    meta = META["vehicle.drivetrain.fuelSystem.remainingFuel"]
    assert (meta["device_class"], meta["state_class"], meta["unit"]) == (
        "volume_storage",
        "measurement",
        None,
    )


def test_diagnostic_fields_are_disabled_by_default():
    for meta in META.values():
        if meta["entity_category"] == "diagnostic":
            assert meta["enabled_default"] is False


def test_generators_are_idempotent(tmp_path):
    # Regenerating from the committed sources must reproduce the committed files.
    build = _load("build_catalogue", str(_TOOLS / "build_catalogue.py"))
    meta_gen = _load("generate_metadata", str(_TOOLS / "generate_metadata.py"))
    trans_gen = _load("generate_translations", str(_TOOLS / "generate_translations.py"))

    before_cat = (_PKG / "catalogue.json").read_text(encoding="utf-8")
    before_meta = (_PKG / "descriptor_metadata.py").read_text(encoding="utf-8")
    before_trans = {
        name: (_PKG / "translations" / name).read_text(encoding="utf-8")
        for name in ("en.json", "de.json")
    }
    build.main()
    meta_gen.main()
    trans_gen.main()
    assert (_PKG / "catalogue.json").read_text(encoding="utf-8") == before_cat
    assert (_PKG / "descriptor_metadata.py").read_text(encoding="utf-8") == before_meta
    for name, before in before_trans.items():
        # Catches an edit to derived_entities.json that was never regenerated,
        # and a hand-edit of the generated entity block.
        assert (_PKG / "translations" / name).read_text(encoding="utf-8") == before
