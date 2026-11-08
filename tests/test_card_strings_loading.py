"""The card loads English at once and the user's language when it first sees it.

Every other card test evaluates the card through ``card_source.evaluable_card``,
which swaps the strings loader for all the tables at once, so none of them runs
the loader itself. This imports the real module under Node, the way a browser
does (top-level ``await``, ``import.meta.url`` with a ``?v=``), from a copy of
``www/`` so a missing file can be staged without touching the package.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .card_source import WWW

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# Prints, after each step, the language tables the card asked for (by the
# strings files Node loaded) and what one key reads as.
_RUN = r"""
const { pathToFileURL } = require("url");
const dir = process.argv[1];
const lang = process.argv[2];
const loaded = new Set();
const { registerHooks } = require("module");
registerHooks({
  resolve(spec, ctx, next) {
    const out = next(spec, ctx);
    const m = /bavariandata-card-strings-([A-Za-z-]+)\.js/.exec(out.url);
    if (m) loaded.add(m[1]);
    return out;
  },
});
globalThis.HTMLElement = class {};
const defined = {};
globalThis.customElements = { define(tag, cls) { defined[tag] = cls; }, get(tag) { return defined[tag]; }, whenDefined() {} };
globalThis.window = globalThis;
globalThis.document = { createElement() { return {}; } };
console.info = () => {};
const errors = [];
console.error = (msg) => errors.push(String(msg));
(async () => {
  await import(pathToFileURL(dir + "/bavariandata-card.js").href + "?v=9.9.9");
  const atLoad = [...loaded].sort();
  const Card = defined["bavariandata-card"];
  const card = new Card();
  card._render = () => {};
  card.hass = { language: lang, locale: { language: lang }, states: {}, devices: {}, entities: {} };
  const before = card._t("tires");
  await new Promise((r) => setTimeout(r, 200));
  process.stdout.write(JSON.stringify({ atLoad, after: [...loaded].sort(), before, now: card._t("tires"), errors }));
})().catch((e) => { process.stdout.write(JSON.stringify({ crash: String(e && e.stack || e) })); });
"""


def _run(tmp_path: Path, lang: str, drop: str | None = None) -> dict:
    www = tmp_path / "www"
    shutil.copytree(WWW, www)
    if drop:
        (www / f"bavariandata-card-strings-{drop}.js").unlink()
    result = subprocess.run(
        [NODE, "-e", _RUN, str(www), lang],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "crash" not in out, out["crash"]
    return out


def test_english_loads_with_the_card_and_nothing_else(tmp_path: Path) -> None:
    out = _run(tmp_path, "en")
    assert out["atLoad"] == ["en"]
    assert out["after"] == ["en"]
    assert out["now"] == "Tires"


def test_the_users_language_loads_on_the_first_hass(tmp_path: Path) -> None:
    out = _run(tmp_path, "de")
    assert out["atLoad"] == ["en"]
    assert out["after"] == ["de", "en"]
    assert out["before"] == "Tires", "English stands in until German has arrived"
    assert out["now"] == "Reifen"


def test_a_regional_language_loads_its_delta_and_its_base(tmp_path: Path) -> None:
    out = _run(tmp_path, "en-GB")
    assert out["after"] == ["en", "en-GB"]
    assert out["now"] == "Tyres"


def test_a_language_without_its_own_file_reads_its_base(tmp_path: Path) -> None:
    out = _run(tmp_path, "de-AT")
    assert out["after"] == ["de", "en"]
    assert out["now"] == "Reifen"


def test_a_language_the_card_does_not_ship_stays_english(tmp_path: Path) -> None:
    out = _run(tmp_path, "ja")
    assert out["after"] == ["en"]
    assert out["now"] == "Tires"


def test_a_missing_language_file_falls_back_to_english(tmp_path: Path) -> None:
    out = _run(tmp_path, "de", drop="de")
    assert out["now"] == "Tires"
    assert any("could not load its de strings" in e for e in out["errors"])
