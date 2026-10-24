"""The frontend pin in requirements_test_ha.txt is the one Home Assistant names."""

from __future__ import annotations

import json
from pathlib import Path

import homeassistant.components.frontend as frontend

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements_test_ha.txt"


def test_the_frontend_pin_matches_home_assistant() -> None:
    manifest = json.loads((Path(frontend.__file__).parent / "manifest.json").read_text())
    (wanted,) = [r for r in manifest["requirements"] if r.startswith("home-assistant-frontend")]
    pins = [line.strip() for line in REQUIREMENTS.read_text().splitlines()]

    assert wanted in pins, f"requirements_test_ha.txt must pin {wanted}"
