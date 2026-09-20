"""Guards on the bundled Lovelace card's HTML composition.

The card has no build step and no framework: it composes every view as an HTML
string and assigns it to ``innerHTML``. That makes escaping a hand-applied
discipline, and hand-applied discipline regresses -- v0.9.4 had to escape a
dozen sites at once, and a thirteenth (the cluster empty-state, where the card
``title:`` option reached ``innerHTML`` through a ``_t()`` placeholder) still
slipped through and was only caught by re-reading the file.

So the rule is enforced mechanically instead:

* **Escaping.** Text the card does not author itself -- the dashboard author's
  ``title:``, the device name as renamed in Home Assistant, entity
  ``friendly_name``\\ s and states, BMW's charging addresses, OpenStreetMap's
  geocoded place names -- must pass through ``_esc()``/``_attr()`` before it is
  interpolated into an HTML template literal.
* **Self-escaping helpers.** The escaping is allowed to happen *inside* a helper
  (``_fmt()`` escapes what it returns, so ``${this._fmt(st)}`` is fine). Those
  helpers are listed in :data:`SAFE_CALLS`, and a second test asserts each one
  really does escape -- otherwise the allowlist would quietly rot the moment
  somebody rewrote a helper.
* **No external resources.** A card that pulls a CDN script or font at runtime
  is its own HACS rejection class, and the card is meant to stay
  dependency-free.

Home Assistant-free, like the rest of the suite: this reads the shipped file as
text. It is a linter, not a renderer -- it proves the escaping call is *there*,
not that the page is safe.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

SOURCE = _CARD.read_text(encoding="utf-8")

# Calls that escape their own return value, so interpolating their result is
# safe. Kept honest by ``test_self_escaping_helpers_actually_escape``.
SAFE_CALLS = ("_esc(", "_attr(", "_fmt(", "_shortName(", "_fmtCost(")

# Expressions carrying text somebody other than the card authored. Matched as
# substrings of an interpolated expression.
TAINT_SOURCES = (
    "_config.title",  # the dashboard author's card title
    "_deviceName(",  # the device name, renameable in Home Assistant
    "friendly_name",  # entity names, renameable and localized
    "_tripPlace(",  # zone name or reverse-geocoded address
    ".address",  # BMW's formattedAddress on a charging session
)

# An HTML template literal is one that actually opens a tag. Templates that only
# build a string (a redraw signature, a ``textContent`` assignment, a CSS blob)
# cannot execute markup and are out of scope.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/]")


# --------------------------------------------------------------------------
# A very small JavaScript scanner
#
# Only enough to find template literals and their ``${...}`` interpolations
# without being fooled by nesting -- the miss this test exists for was an inner
# template inside an interpolation inside an outer template. Strings, comments
# and regex literals are skipped so a backtick or quote inside one cannot
# desync the scan (``_esc`` contains ``/[&<>"]/g``, which does exactly that to a
# naive parser).
# --------------------------------------------------------------------------

_REGEX_ALLOWED_AFTER = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def _skip_string(src: str, i: int) -> int:
    quote = src[i]
    i += 1
    while i < len(src):
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == quote:
            return i + 1
        i += 1
    return i


def _skip_regex(src: str, i: int) -> int:
    i += 1
    in_class = False
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return i + 1
        elif c == "\n":
            return i  # not a regex after all; bail out
        i += 1
    return i


def _starts_regex(src: str, i: int) -> bool:
    """True if the ``/`` at ``i`` opens a regex literal rather than a division."""

    j = i - 1
    while j >= 0 and src[j] in " \t":
        j -= 1
    return j < 0 or src[j] in _REGEX_ALLOWED_AFTER


def _line_of(src: str, index: int) -> int:
    return src.count("\n", 0, index) + 1


def _scan(src: str) -> list[dict]:
    """Return every template literal as ``{"line", "text", "interps"}``.

    ``text`` is the literal's raw text with each interpolation replaced by a
    placeholder; ``interps`` is a list of ``(line, expression)``. Nested
    templates appear as their own entries.
    """

    templates: list[dict] = []
    stack: list[dict] = []
    i, n = 0, len(src)

    while i < n:
        top = stack[-1] if stack else None
        in_template = top is not None and top["kind"] == "tpl"

        if in_template:
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if src.startswith("${", i):
                stack.append({"kind": "expr", "start": i + 2, "depth": 0})
                top["text"].append("\x00")
                i += 2
                continue
            if c == "`":
                frame = stack.pop()
                templates.append(
                    {
                        "line": _line_of(src, frame["start"]),
                        "text": "".join(frame["text"]),
                        "interps": frame["interps"],
                    }
                )
                i += 1
                continue
            top["text"].append(c)
            i += 1
            continue

        # Code context (top level, or inside a ``${...}``).
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
            continue
        if c == "/" and _starts_regex(src, i):
            i = _skip_regex(src, i)
            continue
        if c == "`":
            stack.append({"kind": "tpl", "start": i + 1, "text": [], "interps": []})
            i += 1
            continue
        if top is not None and top["kind"] == "expr":
            if c == "{":
                top["depth"] += 1
            elif c == "}":
                if top["depth"] == 0:
                    frame = stack.pop()
                    stack[-1]["interps"].append(
                        (_line_of(src, frame["start"]), src[frame["start"] : i])
                    )
                    i += 1
                    continue
                top["depth"] -= 1
        i += 1

    return templates


TEMPLATES = _scan(SOURCE)


# --------------------------------------------------------------------------
# Taint
# --------------------------------------------------------------------------

_DECL_RE = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]*)")


def _declarations() -> list[tuple[int, str, str]]:
    """Every simple ``const x = ...`` as ``(line, name, right-hand side)``."""

    return [
        (_line_of(SOURCE, m.start()), m.group(1), m.group(2)) for m in _DECL_RE.finditer(SOURCE)
    ]


DECLARATIONS = _declarations()

# Class methods start at exactly two spaces of indentation; a method body runs
# until the next one. Used to scope taint, so a `label` parameter of a helper in
# one method is not confused with a `label` holding the card title in another.
_METHOD_RE = re.compile(r"^  (?:static\s+)?[A-Za-z_$][\w$]*\(", re.MULTILINE)
METHOD_STARTS = [_line_of(SOURCE, m.start()) for m in _METHOD_RE.finditer(SOURCE)]


def _method_start(line: int) -> int:
    """First line of the method containing ``line`` (0 if outside any method)."""

    enclosing = [start for start in METHOD_STARTS if start <= line]
    return enclosing[-1] if enclosing else 0


def _is_tainted_local(name: str, line: int) -> bool:
    """Whether the local ``name`` visible at ``line`` holds unescaped foreign text.

    Approximates lexical scope by taking the nearest preceding assignment *in the
    same method*, which is exact enough for this file: every render method
    declares its own locals. A right-hand side that already escapes
    (``const to = ... this._esc(...)``) yields a clean value, so it does not
    taint.
    """

    scope_start = _method_start(line)
    nearest = None
    for decl_line, decl_name, rhs in DECLARATIONS:
        if decl_name == name and scope_start <= decl_line <= line:
            nearest = rhs
    if nearest is None:
        return False
    if any(safe in nearest for safe in SAFE_CALLS):
        return False
    return any(source in nearest for source in TAINT_SOURCES)


def _violations() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for template in TEMPLATES:
        if not _HTML_TAG_RE.search(template["text"]):
            continue
        for line, expr in template["interps"]:
            if any(safe in expr for safe in SAFE_CALLS):
                continue
            tainted = any(source in expr for source in TAINT_SOURCES)
            if not tainted:
                bare = expr.strip()
                tainted = bool(re.fullmatch(r"[A-Za-z_$][\w$]*", bare)) and _is_tainted_local(
                    bare, line
                )
            if tainted:
                out.append((line, " ".join(expr.split())[:90]))
    return out


def test_html_templates_escape_user_controlled_text() -> None:
    """Foreign text must be escaped before it reaches an HTML template."""

    found = _violations()
    assert not found, "unescaped user-controlled text in card HTML:\n" + "\n".join(
        f"  {_CARD.name}:{line}  ${{{expr}}}" for line, expr in found
    )


@pytest.mark.parametrize("call", [c for c in SAFE_CALLS if c not in ("_esc(", "_attr(")])
def test_self_escaping_helpers_actually_escape(call: str) -> None:
    """The SAFE_CALLS allowlist must not outlive the escaping it vouches for."""

    name = call.rstrip("(")
    match = re.search(rf"^  {re.escape(name)}\(", SOURCE, re.MULTILINE)
    assert match, f"{name}() is on the safe list but no longer exists"

    # The method body runs to the next method at the same indentation.
    rest = SOURCE[match.start() :]
    end = re.search(r"\n  [A-Za-z_$][\w$]*\(", rest[1:])
    body = rest[: end.start() + 1] if end else rest
    assert "_esc(" in body or "_attr(" in body, (
        f"{name}() is on the safe list but its body no longer escapes; "
        "either restore the escaping or drop it from SAFE_CALLS"
    )


def test_leaflet_tooltips_are_escaped() -> None:
    """Leaflet renders tooltip/popup content as HTML, so it is an innerHTML sink."""

    for match in re.finditer(r"\.(bindTooltip|bindPopup|setTooltipContent)\(", SOURCE):
        line = _line_of(SOURCE, match.start())
        tail = SOURCE[match.end() : SOURCE.find("\n", match.end())]
        assert any(safe in tail for safe in SAFE_CALLS), (
            f"{_CARD.name}:{line}: {match.group(1)}() content is not escaped"
        )


def test_card_loads_no_external_resources() -> None:
    """The card must stay dependency-free -- no CDN script, font or stylesheet."""

    urls = set(re.findall(r"https?://[^\s\"'`)]+", SOURCE))
    allowed = {
        "http://www.w3.org/2000/svg",  # the SVG namespace, not a fetch
        "https://github.com/JustChr/BavarianData",  # console banner link
    }
    assert not (urls - allowed), f"card references external URL(s): {sorted(urls - allowed)}"


def test_the_overview_range_names_the_descriptor_it_wants() -> None:
    """Three "electric range" entities tie on keywords; only one is the truth.

    BMW's ``remainingElectricRange`` is the estimate *during charging* -- on a
    parked car it is whatever was predicted mid-charge, and it showed 128 km on
    an i5 sitting at 86 % with a real 379. ``powertrain.electric.range.target``
    is the range at the *target* state of charge. Both score identically to
    ``kombiRemainingElectricRange``, the figure on the car's own display, so
    which one the overview card showed came down to entity-registry order.
    """

    source = _CARD.read_text(encoding="utf-8")
    start = source.index("      range:\n        cfg.range ||")
    block = source[start : source.index("charging:", start)]
    assert "kombi remaining electric range" in block, (
        "The overview range pick no longer names kombiRemainingElectricRange, "
        "leaving the car's own displayed range to win a keyword tie by luck."
    )
    for impostor in ("electricengine.remainingelectricrange", "range.target"):
        assert impostor in block, (
            f"The overview range pick no longer rejects {impostor}: a "
            "during-charging estimate or a target-based range can be shown as "
            "the range the car has now."
        )
