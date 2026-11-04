"""The automation blueprints the integration ships, kept in the user's blueprint folder.

The Marketplace installs only ``custom_components/bavariandata``, so the blueprints travel
inside it and are copied on setup to ``blueprints/automation/bavariandata`` in
the configuration folder. They then appear under Settings -> Blueprints without
an import, and an update of the integration updates them. A copy imported by URL
before sits in ``blueprints/automation/JustChr`` (Home Assistant names the folder
after the GitHub user); it is updated by the same rule, but never created.

A file is replaced only while it is still what we last wrote. One edited by hand
(or re-imported from GitHub, newer than this release) is left alone from then
on. A file found without any record, from an import by URL before we managed
the folder, is taken over. Removing the integration leaves the files: an
automation made from one keeps working without it.

No Home Assistant imports: ``__init__`` reads and saves the record and decides
whether automations need a reload.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

_LOGGER = logging.getLogger(__name__)

SHIPPED = Path(__file__).parent / "blueprints"
# Below the configuration folder.
TARGET = ("blueprints", "automation", "bavariandata")
# Where Home Assistant saves an import by URL from this repository: it names the
# folder after the GitHub user, not ours. Such a copy is kept up to date as well
# (never created), so automations made from it get every fix too. Its record
# keys carry the folder, so they can't meet the ones above.
IMPORTED = ("blueprints", "automation", "JustChr")
IMPORTED_KEY = "JustChr/"
# What marks an imported file as one of ours rather than another repository's.
SOURCE = b"github.com/JustChr/BavarianData/"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Plan:
    """What to write, and what to remember having written."""

    write: tuple[str, ...]
    # An existing file is among them: automations already made from it run the
    # old version until they are reloaded.
    replaces: bool
    record: dict[str, str]


def plan(
    shipped: Mapping[str, bytes],
    present: Mapping[str, Optional[bytes]],
    record: Mapping[str, str],
) -> Plan:
    """Which shipped files to write, given what is on disk and what we wrote last."""

    write: list[str] = []
    replaces = False
    kept: dict[str, str] = {}
    for name, data in sorted(shipped.items()):
        new = digest(data)
        current = present.get(name)
        if current is None:
            write.append(name)
            kept[name] = new
            continue
        found = digest(current)
        if found == new:
            kept[name] = new
        elif name in record and found != record[name]:
            # Changed since we wrote it: someone's own version. Keep the old
            # record, so it stays theirs even after a later update.
            kept[name] = record[name]
        else:
            write.append(name)
            replaces = True
            kept[name] = new
    return Plan(write=tuple(write), replaces=replaces, record=kept)


def sync(config_dir: str, record: Mapping[str, str]) -> Plan:
    """Bring the user's blueprint folder in line with the shipped files (blocking I/O)."""

    if not SHIPPED.is_dir():
        return Plan(write=(), replaces=False, record=dict(record))
    shipped = {path.name: path.read_bytes() for path in sorted(SHIPPED.glob("*.yaml"))}
    target = Path(config_dir).joinpath(*TARGET)
    present = {
        name: (target / name).read_bytes() if (target / name).is_file() else None
        for name in shipped
    }
    own_record = {key: value for key, value in record.items() if not key.startswith(IMPORTED_KEY)}
    result = plan(shipped, present, own_record)
    _write(target, shipped, result.write)

    imported = Path(config_dir).joinpath(*IMPORTED)
    found = {
        name: (imported / name).read_bytes() for name in shipped if (imported / name).is_file()
    }
    found = {name: data for name, data in found.items() if SOURCE in data}
    copies = plan(
        {name: shipped[name] for name in found},
        found,
        {
            key.removeprefix(IMPORTED_KEY): value
            for key, value in record.items()
            if key.startswith(IMPORTED_KEY)
        },
    )
    _write(imported, shipped, copies.write)
    return Plan(
        write=result.write + tuple(IMPORTED_KEY + name for name in copies.write),
        replaces=result.replaces or copies.replaces,
        record={
            **result.record,
            **{IMPORTED_KEY + name: value for name, value in copies.record.items()},
        },
    )


def _write(folder: Path, shipped: Mapping[str, bytes], names: tuple[str, ...]) -> None:
    if not names:
        return
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(shipped[name])
    _LOGGER.info("Installed or updated blueprints in %s: %s", folder, ", ".join(names))
