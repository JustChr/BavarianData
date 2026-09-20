"""One dialect per language file.

`en.json` had been a mixture: "Tire pressure (front left)" from
`curated_titles.json` next to "Tyre Condition" from `derived_entities.json`,
"Initialising" from the enum label table in the generator, and "Fetch tyre
diagnosis" in the hand-written options block. BMW is the origin of the confusion
— its descriptor *paths* are US (`…wheel.left.tire.pressure`) while the
`element_en` titles in the same catalogue export say "tyre" — but four of our own
files chose sides independently, and nothing noticed.

So: `en.json` is US English, `en-GB.json` is a generated *delta* over it, and
`tools/spelling_en_gb.json` is the only place a dialect pair is written down.
These tests are what stop the drift coming back:

* `en_name()` in `generate_translations.py` falls back to BMW's `element_en`
  when a descriptor has no curated title, so the next catalogue export can put
  "tyre" back into `en.json` without anybody editing anything. That is the leak
  `test_en_json_is_written_in_us_english` watches.
* A hand-written `en-GB.json` would rot: nothing regenerates it when a name
  changes. `tests/test_catalogue.py::test_generators_are_idempotent` covers the
  file itself; here we check its *shape* — every key overlays something real,
  and it holds nothing but the differences.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "custom_components" / "bavariandata"
_TRANS = _PKG / "translations"
_CARD = _PKG / "www" / "bavariandata-card.js"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GEN = _load("generate_en_gb", _ROOT / "tools" / "generate_en_gb.py")
US_TO_UK: dict[str, str] = _GEN.load_words()
UK_TO_US = {uk: us for us, uk in US_TO_UK.items()}
NEVER_TRANSLATE = _GEN.load_never_translate()

EN = json.loads((_TRANS / "en.json").read_text(encoding="utf-8"))
EN_GB = json.loads((_TRANS / "en-GB.json").read_text(encoding="utf-8"))

# Every file in translations/ is either a full language or a regional delta over
# one. Adding a file forces a choice here, because the two are tested — and
# loaded by Home Assistant — differently: a full language must cover every flow
# step, a delta must cover none of them.
FULL_LANGUAGES = {"en.json", "de.json"}
DELTA_LANGUAGES = {"en-GB.json"}


def _leaves(node: object, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for key, value in node.items():
            out += _leaves(value, f"{path}.{key}")
        return out
    if isinstance(node, str):
        return [(path, node)]
    return []


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+", text)


def test_every_translation_file_is_classified() -> None:
    on_disk = {p.name for p in _TRANS.glob("*.json")}
    assert on_disk == FULL_LANGUAGES | DELTA_LANGUAGES, (
        "a translation file was added or removed: say whether it is a full "
        "language or a delta, so the right tests apply to it"
    )


# --- en.json is US English ---------------------------------------------------


def test_en_json_is_written_in_us_english() -> None:
    """No British spelling in any *value* of en.json.

    Keys are exempt on purpose: `tyre_status`, `action_fetch_tyre` and
    `fetch_tyre_diagnosis` are slugs that reached released installs, and renaming
    them would change entity IDs and service names for everyone.
    """

    british = [
        f"{path} -> {word} (US: {UK_TO_US[word.lower()]})"
        for path, value in _leaves(EN)
        for word in _words(value)
        if word.lower() in UK_TO_US
    ]
    assert not british, (
        "en.json is US English; these are British: "
        f"{british}. Fix the source (tools/curated_titles.json, "
        "tools/derived_entities.json, the enum labels in "
        "tools/generate_translations.py) or the hand-written block, not en.json."
    )


def test_the_card_english_table_is_written_in_us_english() -> None:
    tables = _card_translations()
    british = [
        f"{key} -> {word}"
        for key, value in tables["en"].items()
        for word in _words(str(value))
        if word.lower() in UK_TO_US
    ]
    assert not british, f"TRANSLATIONS.en is US English; these are British: {british}"


# --- en-GB.json is a delta, and only a delta --------------------------------


def test_en_gb_overrides_only_keys_that_exist_in_en() -> None:
    """A path that is not in en.json overlays nothing and is dead weight.

    Home Assistant loads `en` and then overlays the requested language on top of
    it key by key (`helpers/translation.py`), so a typo here fails silently: the
    English string keeps showing and the override never runs.
    """

    english = dict(_leaves(EN))
    orphans = [path for path, _ in _leaves(EN_GB) if path not in english]
    assert not orphans, f"en-GB.json overrides paths en.json does not have: {orphans}"


def test_en_gb_changes_every_string_it_overrides() -> None:
    """An override identical to the English is noise that will outlive its reason."""

    english = dict(_leaves(EN))
    same = [path for path, value in _leaves(EN_GB) if english[path] == value]
    assert not same, f"en-GB.json repeats en.json verbatim at: {same}"


def test_en_gb_is_british_throughout() -> None:
    american = [
        f"{path} -> {word}"
        for path, value in _leaves(EN_GB)
        for word in _words(value)
        if word.lower() in US_TO_UK
    ]
    assert not american, (
        f"en-GB.json still holds US spellings: {american}. Add the word to "
        "tools/spelling_en_gb.json and re-run tools/generate_en_gb.py."
    )


def test_en_gb_is_not_a_second_full_translation() -> None:
    """The delta exists so that 1300 strings are not duplicated to change 70.

    If this ever trips, somebody has started copying en.json again — which is
    how the generated `entity` block silently stops being regenerated.
    """

    assert len(_leaves(EN_GB)) < len(_leaves(EN)) / 4


# --- what must survive the rewrite untouched --------------------------------


def test_the_overrides_keep_every_placeholder() -> None:
    """A renamed placeholder is not a typo Home Assistant reports — it drops the
    string.

    `_validate_placeholders` compares the overlay against the cached English and
    discards any resource whose placeholders differ, with a log line nobody
    reads. So `{limit}` must never become `{limite}`, and no word in the map may
    ever match a placeholder's *name*: `{color}` would be rewritten to
    `{colour}` and the whole string would vanish, leaving the English in place
    for a reason invisible from the file.
    """

    english = dict(_leaves(EN))
    holes = re.compile(r"\{[^{}]*\}")
    for path, value in _leaves(EN_GB):
        assert holes.findall(value) == holes.findall(english[path]), (
            f"{path}: en-GB.json changes a placeholder; Home Assistant would "
            "drop the string entirely"
        )


def test_the_overrides_keep_every_code_span() -> None:
    """A slug inside backticks is typed by the user, not read by them."""

    english = dict(_leaves(EN))
    spans = re.compile(r"`[^`]*`")
    for path, value in _leaves(EN_GB):
        assert spans.findall(value) == spans.findall(english[path]), (
            f"{path}: en-GB.json rewrote a code span, which holds an identifier"
        )


def test_strings_that_hold_data_rather_than_prose_are_not_translated() -> None:
    """`_never_translate` is the escape hatch, and it must point at something.

    The case that earned it: the `sections` service field describes itself as
    "Cluster slugs to stream (e.g. electric, status, tire)" — bare slugs, no
    backticks to protect them. Translated, a British user is told to type `tyre`,
    which matches no cluster, and the call quietly does nothing.
    """

    english = dict(_leaves(EN))
    for path in NEVER_TRANSLATE:
        assert path in english, (
            f"{path} is excluded from translation but no longer exists in "
            "en.json; drop it from tools/spelling_en_gb.json"
        )
        assert path not in dict(_leaves(EN_GB)), f"{path} is excluded yet overridden"


# --- the word list itself ----------------------------------------------------


def test_the_spelling_map_is_a_bijection_of_single_words() -> None:
    for us, uk in US_TO_UK.items():
        assert us == us.lower() and uk == uk.lower(), f"{us}/{uk}: write the map in lower case"
        assert " " not in us and " " not in uk, f"{us}/{uk}: whole words only"
        assert us != uk, f"{us}: maps to itself"
    assert len(UK_TO_US) == len(US_TO_UK), "two US spellings map to the same British one"


def test_the_map_does_not_contain_the_words_it_documents_as_traps() -> None:
    """`_deliberately_absent` is reasoning, and reasoning rots quietly.

    "meter" is the standing example: all four in en.json are the device (wallbox,
    house, grid), spelled the same either side of the Atlantic, so mapping it
    would render every one of them as "metre".
    """

    doc = json.loads((_ROOT / "tools" / "spelling_en_gb.json").read_text(encoding="utf-8"))
    for word in doc["_deliberately_absent"]:
        assert word not in US_TO_UK, (
            f"{word} is both mapped and documented as deliberately absent; "
            "the map and its reasoning disagree"
        )


# --- the card's table matches the same word list ----------------------------

NODE = shutil.which("node")

# Same stubs as the other card tests: the file defines a custom element at
# module scope, so it needs a DOM-shaped global before it will evaluate at all.
_DUMP = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
process.stdout.write(JSON.stringify(new Function(src + "\nreturn TRANSLATIONS;")()));
"""


def _card_translations() -> dict[str, dict[str, str]]:
    if NODE is None:
        pytest.skip("Node.js is not installed")
    result = subprocess.run(
        [NODE, "-e", _DUMP, str(_CARD)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_card_en_gb_table_is_exactly_the_delta_of_its_en_table() -> None:
    """Derived from the same word list as en-GB.json, so the two cannot diverge.

    `t()` falls back to `TRANSLATIONS.en` per key, so the British table needs the
    differing keys and nothing else — and it needs *all* of them, which is the
    half that is easy to forget (`check_tyres` was missed the first time).
    """

    tables = _card_translations()
    expected = {
        key: _GEN.britishise(str(value), US_TO_UK)
        for key, value in tables["en"].items()
        if _GEN.britishise(str(value), US_TO_UK) != value
    }
    assert tables.get("en-GB") == expected, (
        "TRANSLATIONS['en-GB'] must be exactly the British delta of "
        "TRANSLATIONS.en. Expected keys: " + repr(sorted(expected))
    )


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_card_offers_the_same_languages_as_the_integration() -> None:
    tables = _card_translations()
    assert set(tables) == {"en", "en-GB", "de"}, (
        "the card and translations/ must offer the same languages, or a user "
        "gets a German card with English tiles (or the reverse)"
    )
