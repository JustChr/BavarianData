"""Entity icons come from icons.json, keyed by translation key (Gold: icon-translations).

An ``_attr_icon`` in the code would override icons.json, and an icons.json key
that matches no translated entity names nothing, so both are drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "bavariandata"


def test_no_entity_hardcodes_its_icon():
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(_PKG.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\b_attr_icon\b", line)
    ]
    assert not offenders, f"icons belong in icons.json: {offenders}"


def test_every_icon_names_a_translated_entity():
    icons = json.loads((_PKG / "icons.json").read_text(encoding="utf-8"))["entity"]
    names = json.loads((_PKG / "translations" / "en.json").read_text(encoding="utf-8"))["entity"]
    for platform, keys in icons.items():
        for key, icon in keys.items():
            assert key in names.get(platform, {}), f"{platform}.{key} has no entity"
            assert icon["default"].startswith("mdi:"), f"{platform}.{key}"
