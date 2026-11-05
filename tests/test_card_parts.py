"""The card's class is mixed together from parts; the parts must not collide.

``bavariandata-card.js`` stacks every ``bavariandata-card-<part>.js`` under its
own class, one mixin over the next. A method defined in two of them is not an
error in JavaScript: the later one silently wins, and which one that is depends
on the order of ``CARD_PARTS``. So every method lives in exactly one file.
"""

from __future__ import annotations

import collections
import re

from .card_source import card_files, card_parts, part_file

# A method (or static field) of a class body, at the two-space indent every
# file keeps its members at.
_MEMBER = re.compile(r"^  (?:static |async |get |set )?([A-Za-z_$][\w$]*)\s*(?:\(|=)", re.MULTILINE)
# In the card file, only its own class body counts: the helpers above it and the
# editor below it are not the card's members.
_CARD_CLASS = re.compile(r"^class BavarianDataCard extends .*?^\}$", re.MULTILINE | re.DOTALL)


def _members(text: str) -> list[str]:
    card_class = _CARD_CLASS.search(text)
    return _MEMBER.findall(card_class.group(0) if card_class else text)


def test_no_method_is_defined_in_two_files() -> None:
    owners: dict[str, list[str]] = collections.defaultdict(list)
    for path in card_files():
        for name in _members(path.read_text(encoding="utf-8")):
            owners[name].append(path.name)
    twice = {name: files for name, files in owners.items() if len(files) > 1}
    assert not twice, f"defined more than once, the later part silently wins: {twice}"


def test_every_part_is_a_mixin_over_the_card() -> None:
    for part in card_parts():
        text = part_file(part).read_text(encoding="utf-8")
        assert re.search(
            rf"^export default \(Base(?:, \{{[^}}]*\}})?\) => class {part.capitalize()}Part extends Base \{{$",
            text,
            re.MULTILINE,
        ), (
            f"{part_file(part).name} is not `export default (Base, {{...}}) => class ... extends Base`"
        )
        assert _members(text), f"{part_file(part).name} holds no methods"
