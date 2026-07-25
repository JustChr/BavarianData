"""Guided-onboarding activator: one click in the user's own browser session.

The BMW CarData portal exposes the whole onboarding chain on its
``/utilities/bmw/api/cd/*`` and ``/mybmw/api/mapped-vehicle/*`` routes — the
registered API client (``/applications`` → the client id you otherwise copy by
hand), the mapping/consent state, and the MQTT stream selection (``/streams``).
All of it is gated by the interactive portal session (httpOnly cookies + Akamai
bot-defense), so it can only be driven from *inside* the logged-in bmw.at tab —
see [`docs/reference/stream-attribute-activation.md`](../../docs/reference/stream-attribute-activation.md).

This module builds a small **activator** that runs there (delivered as a
bookmarklet or a console one-liner). Because it runs same-origin, the httpOnly
cookies ride along, the browser sets the Akamai-required headers, and **no secret
leaves the browser**. The activator is deliberately bulletproof:

* **Page-aware** — refuses politely if it's not on a bmw.at vehicle page.
* **Self-checking** — reads what's already on and only activates the delta.
* **Additive** — unions the wanted attributes with the current selection, so it
  never removes a field the user already had.
* **Idempotent** — safe to run twice; a fully-configured car reports "nothing to
  do".
* **Self-reporting** — POSTs a compact, **non-secret** result to a one-time Home
  Assistant webhook (so there is no paste-back), and also copies it to the
  clipboard as a fallback for setups where the browser blocks the auto-POST
  (plain-HTTP Home Assistant → mixed content).

Kept free of Home Assistant imports so the generation and parsing are
unit-testable in isolation.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import escape
from typing import Any, Optional

try:  # normal case: imported as part of the package
    from .descriptors import default_sections, descriptors_for_sections
except ImportError:  # pragma: no cover - loaded standalone (tests / tools)
    from descriptors import default_sections, descriptors_for_sections

# The scope that marks an application usable for this integration (the same
# ``cardata:api:read`` the device flow needs). Used to pick a primary client id
# when the account has more than one registered application.
PRIMARY_SCOPE = "cardata:api:read"

# Result blob framing. The activator base64-encodes a small JSON object and
# prefixes it so the parser can recognise/version/validate it. Bump the integer
# on a breaking change to the payload shape.
RESULT_PREFIX = "BAVARIANDATA1:"
RESULT_VERSION = 1


class OnboardingParseError(Exception):
    """Raised when an onboarding result can't be decoded/validated."""


# Injected markers (not JS/format tokens) so the template stays valid JS and can
# carry the wanted descriptors + the report URL verbatim.
_WANT_MARKER = "__WANT__"
_REPORT_MARKER = "__REPORT__"

# The activator. Runs in the portal tab. Derives origin/locale/mapped-vehicle id
# from ``location`` (so it needn't be told the market/vehicle), talks to the
# CarData BFF same-origin (cookies via ``credentials:'include'``; the browser —
# not us — sets Origin/Referer/Sec-Fetch, which is why this works where a headless
# client is throttled), and shows an on-page overlay so the result is never hidden
# in the console.
_ACTIVATOR_TEMPLATE = """(async () => {
  const WANT = __WANT__;
  const REPORT = __REPORT__;
  const H = { 'Accept': 'application/json', 'x-ocp-stage': 'prod' };
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;'
    + 'max-width:360px;background:#1c69d4;color:#fff;font:14px/1.45 Arial,sans-serif;'
    + 'padding:14px 16px;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.35)';
  const show = (html) => { box.innerHTML = '<b>BavarianData</b><br>' + html; };
  document.body.appendChild(box);

  // Every request gets a hard timeout, so a stalled connection (BMW's bot-defense
  // occasionally holds one open) can never hang the whole run. On timeout/error
  // the helper returns {json:null}; callers degrade gracefully.
  const TIMEOUT = 12000;
  const req = async (url, opts) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
    try {
      const r = await fetch(url, Object.assign(
        { credentials: 'include', signal: ctrl.signal },
        opts || {},
        { headers: Object.assign({}, H, (opts && opts.headers) || {}) }));
      clearTimeout(timer);
      let json = null; try { json = await r.json(); } catch (e) {}
      return { status: r.status, json: json };
    } catch (e) { clearTimeout(timer); return { status: 0, json: null, error: e }; }
  };
  const sleep = (ms) => new Promise(res => setTimeout(res, ms));

  const origin = location.origin;
  const parts = location.pathname.split('/').filter(Boolean);
  const locale = parts[0] || '';
  const mvIdx = parts.indexOf('mapped-vehicle');
  const mappedId = mvIdx >= 0 ? parts[mvIdx + 1] : null;
  const base = origin + '/' + locale;
  const out = { v: 1, origin, locale, mappedVehicleId: mappedId,
                clientIds: [], vehicle: {}, activated: null, errors: [] };

  if (origin.indexOf('bmw') === -1) {
    show('Open your BMW portal and sign in first, then click this again.'); return;
  }
  if (!mappedId) {
    show('Open a vehicle&#39;s <b>stream setup</b> page on the BMW portal, then click this again.');
    return;
  }
  show('Checking your account&hellip;');

  const apps = await req(base + '/utilities/bmw/api/cd/applications');
  if (apps.json && apps.json.data) {
    apps.json.data.forEach(a => (a.credentials || []).forEach(c => {
      if (c && c.apikey) out.clientIds.push({ apikey: c.apikey, scopes: c.scopes || [] });
    }));
  } else { out.errors.push('applications: unavailable'); }

  const md = await req(base + '/mybmw/api/mapped-vehicle/' + mappedId + '/mapping-details');
  if (md.json && md.json.data) {
    const d = md.json.data;
    out.vehicle = { mappingStatus: d.mappingStatus, subscriberStatus: d.subscriberStatus,
                    isElectricOrHybrid: d.isElectricOrHybrid };
  }

  const s = await req(base + '/utilities/bmw/api/cd/streams/' + mappedId + '?includeAttributes=true');
  const current = (s.json && s.json.data && s.json.data.attributes) || [];

  // BMW rejects the whole POST with HTTP 500 if it contains any descriptor it
  // doesn't list as streamable for this vehicle, so fetch its streamable
  // catalogue (paged 10 at a time, exactly like the portal) and keep only wanted
  // ids that appear there. A stalled page just stops paging and uses what we got
  // (a partial valid set is still safe -- it can only under-activate, never post
  // something invalid).
  const valid = new Set();
  let partial = false;
  for (let off = 0; off < 1000; off += 10) {
    show('Reading available fields&hellip; ' + valid.size);
    const c = await req(base + '/utilities/bmw/api/cd/catalogue?streamable=true&offset=' + off + '&q=&category=');
    if (!c.json) { partial = true; out.errors.push('catalogue: stalled at offset ' + off); break; }
    const items = (c.json.data && c.json.data.items) || [];
    items.forEach(it => { if (it && it.id && it.streamable) valid.add(it.id); });
    if (items.length < 10) break;
    await sleep(120);
  }

  if (valid.size === 0) {
    out.errors.push('Could not read the streamable field list; nothing changed.');
    out.activated = { status: 0, current: current.length, added: 0,
                      total: current.length, skipped: 0, partial: partial, ok: false };
  } else {
    // Additive + streamable-filtered: union current with the wanted fields BMW
    // will accept, so we never drop an existing field nor post an invalid one.
    const wanted = WANT.filter(a => valid.has(a));
    const skipped = WANT.length - wanted.length;
    const set = new Set(current);
    let added = 0;
    wanted.forEach(a => { if (!set.has(a)) { set.add(a); added++; } });
    const desired = Array.from(set).sort();

    if (added > 0) {
      show('Activating ' + added + ' fields&hellip;');
      const r = await req(base + '/utilities/bmw/api/cd/streams/' + mappedId, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ attributes: desired }),
      });
      const acc = (r.json && r.json.data && r.json.data.attributes) || desired;
      out.activated = { status: r.status, current: current.length, added: added,
                        total: acc.length, skipped: skipped, partial: partial,
                        ok: r.status === 201 };
      if (r.status !== 201) out.errors.push('streams-write: HTTP ' + r.status);
    } else {
      out.activated = { status: 200, current: current.length, added: 0,
                        total: current.length, skipped: skipped, partial: partial, ok: true };
    }
  }

  const blob = 'BAVARIANDATA1:' + btoa(unescape(encodeURIComponent(JSON.stringify(out))));
  let reported = false;
  if (REPORT) {
    // text/plain keeps it a CORS "simple request" (no preflight); fire-and-forget.
    try {
      await fetch(REPORT, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: blob });
      reported = true;
    } catch (e) {}
  }

  const a = out.activated || {};
  const head = a.ok
    ? ('&#10003; ' + a.total + ' fields active (' + a.added + ' newly added'
        + (partial ? ', partial read &mdash; click again to add the rest' : '') + ').')
    : ('&#9888; Activation hit a problem &mdash; ' + (out.errors[out.errors.length - 1] || 'try again') + '.');

  if (reported) {
    show(head + '<br><br>Home Assistant will continue automatically &mdash; you can close this tab.');
    return;
  }
  // Manual fallback: a programmatic clipboard write fails this late (the click
  // gesture that launched us has long expired), so present the code with a Copy
  // button -- the button's own click is a fresh gesture that is allowed to copy.
  box.innerHTML = '<b>BavarianData</b><br>' + head
    + '<br><br>Copy this and paste it into Home Assistant:'
    + '<textarea id="bd-blob" readonly style="width:100%;height:56px;margin-top:6px;'
    + 'font:11px/1.3 monospace;box-sizing:border-box"></textarea>'
    + '<button id="bd-copy" style="margin-top:6px;padding:7px 14px;border:0;border-radius:5px;'
    + 'background:#fff;color:#1c69d4;font-weight:600;cursor:pointer">Copy</button>';
  const ta = box.querySelector('#bd-blob'); ta.value = blob;
  const btn = box.querySelector('#bd-copy');
  btn.onclick = async () => {
    ta.focus(); ta.select();
    let done = false;
    try { await navigator.clipboard.writeText(blob); done = true; } catch (e) {}
    if (!done) { try { done = document.execCommand('copy'); } catch (e) {} }
    btn.textContent = done ? 'Copied!' : 'Press Ctrl+C';
  };
})();"""


def _normalize(attributes: Iterable[str]) -> list[str]:
    return sorted({a.strip() for a in attributes if a and a.strip()})


def build_activator_js(attributes: Iterable[str], *, report_url: str = "") -> str:
    """Return the raw activator JavaScript for a set of wanted attributes.

    ``report_url`` (a Home Assistant webhook) makes the activator self-report; an
    empty string falls back to clipboard-only. The attribute set is embedded
    sorted+deduplicated, matching the stream-activation service byte-for-byte.
    """

    want = json.dumps(_normalize(attributes), ensure_ascii=False)
    report = json.dumps(report_url or "")
    return _ACTIVATOR_TEMPLATE.replace(_WANT_MARKER, want).replace(_REPORT_MARKER, report)


def build_bookmarklet(attributes: Iterable[str], *, report_url: str = "") -> str:
    """Return the activator as a ``javascript:`` bookmarklet URL.

    Percent-encoded so it is safe inside an anchor ``href`` and as a bookmark; the
    browser decodes and runs it in the current (bmw.at) tab when clicked.
    """

    js = build_activator_js(attributes, report_url=report_url)
    return "javascript:" + urllib.parse.quote(js, safe="")


def build_console_snippet(attributes: Iterable[str], *, report_url: str = "") -> str:
    """The activator as a paste-into-console one-liner (bookmarklet alternative)."""

    return build_activator_js(attributes, report_url=report_url)


def default_activator_attributes() -> list[str]:
    """The attributes the guided flow activates by default: the default clusters."""

    return descriptors_for_sections(default_sections())


_HELPER_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BavarianData — activate BMW data</title>
<style>
  body { font: 16px/1.5 -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         max-width: 640px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }
  h1 { font-size: 22px; } ol { padding-left: 20px; } li { margin: 10px 0; }
  .bm { display: inline-block; background: #1c69d4; color: #fff; text-decoration: none;
        padding: 12px 20px; border-radius: 8px; font-weight: 600; cursor: grab; }
  .hint { color: #555; font-size: 14px; } code, textarea { font-family: ui-monospace, monospace; }
  textarea { width: 100%; height: 90px; margin-top: 8px; }
  details { margin-top: 24px; } summary { cursor: pointer; color: #1c69d4; }
  @media (prefers-color-scheme: dark) { body { background:#111; color:#eee; } .hint{color:#aaa;} }
</style></head><body>
<h1>Activate your BMW CarData</h1>
<ol>
  <li><b>Drag this button to your bookmarks bar</b> (once):<br><br>
      <a class="bm" href="__BOOKMARKLET__">Activate BMW data</a>
      <div class="hint">Can't drag? Right-click it &rarr; &ldquo;Bookmark link&rdquo;, or use the console option below.</div></li>
  <li>Open the <b>BMW portal</b>, sign in, and go to a vehicle's <b>stream setup</b> page.</li>
  <li>Click the <b>Activate BMW data</b> bookmark. It turns on __COUNT__ data fields
      (only the ones not already on) and tells Home Assistant automatically.</li>
</ol>
<details>
  <summary>No bookmarks bar? Use the console instead</summary>
  <p class="hint">On the BMW stream-setup page, press <b>F12</b> &rarr; <b>Console</b>, paste this, and press Enter:</p>
  <textarea readonly onclick="this.select()">__CONSOLE__</textarea>
</details>
</body></html>"""


def build_helper_page(*, bookmarklet: str, console_js: str, attribute_count: int) -> str:
    """Return the self-contained HTML helper page hosting the bookmarklet.

    Served from Home Assistant's own origin (a config-flow dialog can't hold a
    ``javascript:`` link — HA sanitizes it), so it can present the draggable
    bookmarklet, a right-click hint and the console fallback in one place.
    """

    return (
        _HELPER_PAGE_TEMPLATE
        .replace("__BOOKMARKLET__", escape(bookmarklet, quote=True))
        .replace("__COUNT__", str(attribute_count))
        .replace("__CONSOLE__", escape(console_js))
    )


@dataclass
class OnboardingClient:
    """One registered CarData API client discovered from ``/applications``."""

    apikey: str
    scopes: list[str] = field(default_factory=list)

    @property
    def is_primary(self) -> bool:
        return PRIMARY_SCOPE in self.scopes


@dataclass
class OnboardingResult:
    """Decoded, non-secret result of one activator run."""

    origin: Optional[str] = None
    locale: Optional[str] = None
    mapped_vehicle_id: Optional[str] = None
    clients: list[OnboardingClient] = field(default_factory=list)
    vehicle: dict[str, Any] = field(default_factory=dict)
    activated: Optional[dict[str, Any]] = None
    errors: list[str] = field(default_factory=list)

    @property
    def primary_client_id(self) -> Optional[str]:
        """The client id to feed the device flow.

        Prefer an application carrying ``cardata:api:read``; fall back to the
        first discovered client so a non-standard scope set still yields something.
        """

        for client in self.clients:
            if client.is_primary:
                return client.apikey
        return self.clients[0].apikey if self.clients else None

    def _activated_int(self, key: str) -> Optional[int]:
        if isinstance(self.activated, dict):
            value = self.activated.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    @property
    def activated_count(self) -> Optional[int]:
        """Total fields streaming after activation."""

        return self._activated_int("total")

    @property
    def added_count(self) -> Optional[int]:
        """How many fields this run newly switched on."""

        return self._activated_int("added")

    @property
    def skipped_count(self) -> Optional[int]:
        """How many wanted fields were dropped as not streamable for this vehicle."""

        return self._activated_int("skipped")

    @property
    def activation_ok(self) -> bool:
        return bool(isinstance(self.activated, dict) and self.activated.get("ok"))


def parse_onboarding_result(blob: str) -> OnboardingResult:
    """Decode the blob the activator emits (webhook body or manual paste).

    Tolerant of surrounding whitespace/quotes a paste can pick up. Raises
    :class:`OnboardingParseError` with a user-facing message on anything that
    isn't a valid, current-version BavarianData result.
    """

    if not blob or not blob.strip():
        raise OnboardingParseError("Nothing received.")
    text = blob.strip().strip('"').strip("'").strip()
    if not text.startswith(RESULT_PREFIX):
        raise OnboardingParseError(
            "That doesn't look like a BavarianData result. Run the activator on "
            "the BMW portal page and use exactly what it produced."
        )
    encoded = text[len(RESULT_PREFIX):].strip()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise OnboardingParseError(f"Couldn't decode the result ({err}).") from err
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise OnboardingParseError(f"The result wasn't valid data ({err}).") from err
    if not isinstance(data, dict):
        raise OnboardingParseError("The result had an unexpected shape.")
    if data.get("v") != RESULT_VERSION:
        raise OnboardingParseError(
            "That result came from a different version of the activator. Reload "
            "the setup page in Home Assistant and run it again."
        )

    clients = [
        OnboardingClient(
            apikey=str(c["apikey"]),
            scopes=[str(s) for s in c.get("scopes", []) if s],
        )
        for c in data.get("clientIds", [])
        if isinstance(c, dict) and c.get("apikey")
    ]
    return OnboardingResult(
        origin=data.get("origin"),
        locale=data.get("locale"),
        mapped_vehicle_id=data.get("mappedVehicleId"),
        clients=clients,
        vehicle=data.get("vehicle") if isinstance(data.get("vehicle"), dict) else {},
        activated=data.get("activated") if isinstance(data.get("activated"), dict) else None,
        errors=[str(e) for e in data.get("errors", []) if e],
    )
