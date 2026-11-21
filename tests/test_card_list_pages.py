"""A month's trips and charges can come a page of whole days at a time (#356).

A busy month listed every trip in one column and pushed the rest of the
dashboard far down. With ``page_size`` set, the list holds up to that many
entries, cut at day boundaries so a day's heading never splits, with a pager
at its foot whose arrows run as the month control's do (back to earlier days,
on to later). Unset or 0, the month is one page, as it always was.

The presses go through the card's own listeners: the fake shadow root hands
out an element for every ``[data-…]`` match in the rendered HTML, so the
wiring is covered and not only the cut.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from .card_source import evaluable_card
from .test_card_configurator import _editor
from .test_card_drivetrain import I5

_CARD = evaluable_card()

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

_HARNESS = r"""
const fs = require("fs");
const FIXED = Date.parse("2026-11-21T18:00:00Z");
const RealDate = Date;
class FrozenDate extends RealDate {
  constructor(...a) { if (a.length === 0) super(FIXED); else super(...a); }
  static now() { return FIXED; }
}
globalThis.Date = FrozenDate;
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const card = Object.create(Card.prototype);
card.setConfig(input.config);
card._hass = {
  states: input.states, language: "en", locale: { language: "en" },
  devices: { device: { id: "device", name: "Car", identifiers: [["bavariandata", "WBS1"]] } },
  callService: () => new Promise(() => {}),
};
// Every [data-name] in the HTML becomes an element whose click listener the
// test can fire, as a browser would hand it to the card.
let listeners = [];
const elements = (sel) => {
  const m = /^\[([a-z-]+)\]$/.exec(sel);
  if (!m) return [];
  const out = [];
  for (const tag of card.shadowRoot.innerHTML.match(/<[a-z-]+\s[^>]*>/g) || []) {
    if (!tag.includes(` ${m[1]}=`)) continue;
    const attrs = {};
    for (const [, k, v] of tag.matchAll(/([a-z-]+)="([^"]*)"/g)) attrs[k] = v;
    const disabled = /\sdisabled[\s>]/.test(tag);
    out.push({
      getAttribute: (k) => (k in attrs ? attrs[k] : null),
      hasAttribute: (k) => (k === "disabled" ? disabled : k in attrs),
      addEventListener: (type, fn) => { if (type === "click") listeners.push({ attrs, disabled, fn }); },
    });
  }
  return out;
};
card.shadowRoot = { innerHTML: "", querySelectorAll: elements, querySelector: () => null };
card.attachShadow = () => card.shadowRoot;
card._wireTaps = () => {};
card._wireExport = () => {};
card._styles = () => "";
card._resolveDeviceId = () => "device";
card._deviceEntities = () => Object.keys(input.states);
if (input.trips) {
  card._trpMonth = "2026-11";
  card._trp = { vin: "WBS1", trigger: "", month: "2026-11", loading: false, ...input.trips };
  card._trpPage = input.page || 0;
  card._render = () => { card._sig = null; card._paintTrips("device", Object.keys(input.states)); };
}
if (input.sessions) {
  card._chgMonth = "2026-11";
  card._chg = { vin: "WBS1", trigger: "", month: "2026-11", data: input.sessions, loading: false };
  card._chgPage = input.page || 0;
  card._render = () => { card._sig = null; card._paintCharging("device", Object.keys(input.states)); };
}
if (input.room) card._enterRoom(input.room);
const paint = () => { listeners = []; card._render(); return card.shadowRoot.innerHTML; };
const frames = [paint()];
for (const [name, value] of input.press || []) {
  const hit = listeners.find(({ attrs }) => attrs[name] === String(value));
  if (!hit) throw new Error(`nothing to press: ${name}=${value}`);
  if (hit.disabled) throw new Error(`pressed a disabled button: ${name}=${value}`);
  // A month press refetches; hand the same month back as the service would.
  const before = card._trp;
  hit.fn({ stopPropagation() {} });
  if (input.trips && !card._trp) card._trp = { ...before, month: card._trpMonth };
  frames.push(paint());
}
process.stdout.write(JSON.stringify({ frames, trpPage: card._trpPage, chgPage: card._chgPage }));
"""


def _run(**extra) -> dict:
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(_CARD)],
        input=json.dumps({"states": {k: {**v, "entity_id": k} for k, v in I5.items()}, **extra}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
        env={**os.environ, "TZ": "UTC"},
    )
    return json.loads(result.stdout)


# A November with 29 trips: 4, 2, 2, 5, 3, 12 and 1 a day, newest first, as
# get_trips answers. Ten a page, whole days: [21 20 19] [18 17] [16] [15].
_PER_DAY = {21: 4, 20: 2, 19: 2, 18: 5, 17: 3, 16: 12, 15: 1}


def _trip(day: int, n: int) -> dict:
    return {
        "start": f"2026-11-{day:02d}T{6 + n:02d}:00:00+00:00",
        "end": f"2026-11-{day:02d}T{6 + n:02d}:20:00+00:00",
        "start_place": {"label": "Home"},
        "end_place": {"label": "Work"},
        "distance_km": 10.0,
        "duration_s": 1200,
        "classification": "private",
        "classification_source": "auto",
    }


TRIPS = [_trip(day, n) for day, count in _PER_DAY.items() for n in reversed(range(count))]
SUMMARY = {
    "total_km": 290.0,
    "trip_count": len(TRIPS),
    "split": {"private_km": 290.0, "private_percent": 100.0},
}
OPEN = {
    "start": "2026-11-21T17:50:00+00:00",
    "start_place": {"label": "Home"},
    "distance_km": 3.0,
    "in_progress": True,
}
TRIPS_VIEW = {"type": "custom:bavariandata-card", "view": "trips", "page_size": 10}


def _days(html: str) -> list[str]:
    return re.findall(r'<div class="tr__day"><span>[A-Za-z]+, November (\d+)<', html)


def _rows(html: str) -> int:
    return html.count("data-trip=")


def _pager(html: str) -> str:
    m = re.search(r'class="mnav mnav--foot">.*?class="mnav__label">([^<]*)<', html, re.S)
    return m.group(1) if m else ""


def _enabled(html: str, step: int) -> bool:
    m = re.search(rf'data-page-step="{step}"[^>]*>', html)
    assert m, f"no pager button {step}"
    return "disabled" not in m.group(0)


def test_a_long_month_comes_a_page_of_whole_days_at_a_time() -> None:
    out = _run(
        config=TRIPS_VIEW,
        trips={"trips": TRIPS, "summary": SUMMARY, "open": None},
        press=[["data-page-step", 1]] * 3 + [["data-page-step", -1]],
    )
    first, second, third, last, back = out["frames"]
    assert (_days(first), _rows(first)) == (["21", "20", "19"], 8)
    assert _pager(first) == "19 Nov – 21 Nov"
    # The newest page can only go back; the oldest only forward.
    assert _enabled(first, 1) and not _enabled(first, -1)
    assert (_days(second), _rows(second)) == (["18", "17"], 8)
    assert _enabled(second, 1) and _enabled(second, -1)
    # A day busier than a page keeps its trips together on a page of its own.
    assert (_days(third), _rows(third)) == (["16"], 12)
    assert _pager(third) == "16 Nov"
    assert (_days(last), _rows(last)) == (["15"], 1)
    assert not _enabled(last, 1) and _enabled(last, -1)
    assert _days(back) == ["16"]
    # The month in review is the month's on every page.
    assert all("tr__review" in html for html in out["frames"])


def test_the_drive_under_way_leads_the_newest_page_only() -> None:
    trips = {"trips": TRIPS, "summary": SUMMARY, "open": OPEN}
    first, second = _run(config=TRIPS_VIEW, trips=trips, press=[["data-page-step", 1]])["frames"]
    assert "tr__badge--live" in first and "tr__badge--live" not in second


@pytest.mark.parametrize("size", [None, 0, "x"])
def test_without_a_page_size_the_month_is_one_page(size) -> None:
    config = {"type": "custom:bavariandata-card", "view": "trips"}
    if size is not None:
        config["page_size"] = size
    html = _run(config=config, trips={"trips": TRIPS, "summary": SUMMARY, "open": None})["frames"][
        0
    ]
    assert _rows(html) == len(TRIPS) and "data-page-step" not in html


def test_a_page_size_on_the_hub_pages_its_rooms() -> None:
    out = _run(
        config={"type": "custom:bavariandata-card", "hub": True, "page_size": 10},
        room="trips",
        trips={"trips": TRIPS, "summary": SUMMARY, "open": None},
    )
    assert _days(out["frames"][0]) == ["21", "20", "19"]


def test_a_month_that_fits_has_no_pager() -> None:
    out = _run(config=TRIPS_VIEW, trips={"trips": TRIPS[:8], "summary": SUMMARY, "open": None})
    html = out["frames"][0]
    assert _rows(html) == 8 and "mnav--foot" not in html and "data-page-step" not in html


def test_a_page_past_the_end_shows_the_oldest() -> None:
    # The list can shrink under a reader (a trip reclassified away by a filter,
    # a month refetched); the page they were on clamps rather than going blank.
    out = _run(config=TRIPS_VIEW, trips={"trips": TRIPS, "summary": SUMMARY, "open": None}, page=9)
    assert _days(out["frames"][0]) == ["15"] and out["trpPage"] == 3


def test_the_type_filter_and_the_month_start_again_on_the_newest_page() -> None:
    trips = {"trips": TRIPS, "summary": SUMMARY, "open": None}
    out = _run(config=TRIPS_VIEW, trips=trips, page=2, press=[["data-trip-filter", "private"]])
    assert out["trpPage"] == 0 and _days(out["frames"][-1]) == ["21", "20", "19"]
    out = _run(config=TRIPS_VIEW, trips=trips, page=2, press=[["data-month-step", -1]])
    assert out["trpPage"] == 0


def _session(day: int) -> dict:
    return {
        "id": f"s{day}",
        "start": f"2026-11-{day:02d}T20:00:00+00:00",
        "end": f"2026-11-{day:02d}T23:00:00+00:00",
        "energy_kwh": 11.0,
        "soc_start": 40.0,
        "soc_end": 80.0,
        "location": {"zone": "home"},
    }


def test_the_charging_list_pages_the_same_way() -> None:
    sessions = [_session(day) for day in range(21, 7, -1)]  # 14 charges, one a day
    out = _run(
        config={"type": "custom:bavariandata-card", "view": "charging", "page_size": 10},
        sessions=sessions,
        press=[["data-page-step", 1]],
    )
    first, second = out["frames"]
    assert (first.count("data-session="), second.count("data-session=")) == (10, 4)
    assert _pager(first) == "12 Nov – 21 Nov" and _pager(second) == "8 Nov – 11 Nov"
    assert not _enabled(second, 1) and _enabled(second, -1)


def test_the_editor_offers_the_page_size_where_there_is_a_month_list() -> None:
    for config in ({"view": "trips"}, {"view": "charging"}, {"hub": True}):
        assert "page_size" in _editor(config)["fields"], config
    for config in ({}, {"view": "map"}, {"view": "efficiency"}, {"cluster": "tire"}):
        assert "page_size" not in _editor(config)["fields"], config


@pytest.mark.parametrize(
    ("change", "stored"),
    [
        ({"cluster": "trips", "page_size": 10}, {"view": "trips", "page_size": 10}),
        ({"cluster": "charging", "page_size": 7.6}, {"view": "charging", "page_size": 7}),
        ({"cluster": "trips", "page_size": 0}, {"view": "trips"}),  # the default
        ({"cluster": "map", "page_size": 10}, {"view": "map"}),  # not its view
    ],
)
def test_the_editor_stores_only_a_page_size_that_pages(change, stored) -> None:
    result = _editor({"type": "custom:bavariandata-card", "view": "trips"}, change)
    assert result["stored"] == {"type": "custom:bavariandata-card", **stored}
