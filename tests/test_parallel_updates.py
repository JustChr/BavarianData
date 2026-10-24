"""Every entity platform declares PARALLEL_UPDATES (Silver: parallel-updates).

Every entity here is pushed by the coordinator and none polls, so 0 (no limit)
is the right value everywhere; a platform that leaves it out gets HA's default
of one update at a time.
"""

from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "bavariandata"
_PLATFORMS = ("binary_sensor", "calendar", "device_tracker", "image", "sensor")


def test_every_platform_declares_parallel_updates():
    for platform in _PLATFORMS:
        source = (_PKG / f"{platform}.py").read_text(encoding="utf-8")
        assert re.search(r"^PARALLEL_UPDATES = 0$", source, re.M), platform


def test_the_list_covers_every_forwarded_platform():
    source = (_PKG / "__init__.py").read_text(encoding="utf-8")
    block = re.search(r"^PLATFORMS: list\[Platform\] = \[(.*?)\]", source, re.M | re.S)
    forwarded = set(re.findall(r"Platform\.([A-Z_]+)", block.group(1)))
    assert {name.lower() for name in forwarded} == set(_PLATFORMS)
