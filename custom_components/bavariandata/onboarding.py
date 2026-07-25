"""Guided-onboarding orchestrator: one in-browser snippet, results back to HA.

The BMW CarData portal exposes the *whole* onboarding chain on its
``/utilities/bmw/api/cd/*`` and ``/mybmw/api/mapped-vehicle/*`` routes — the
registered API client (``/applications`` → the client id you otherwise copy by
hand), the mapping/consent state, and the MQTT stream selection (``/streams``).
All of it is gated by the interactive portal session (httpOnly cookies + Akamai
bot-defense) — see [`stream_activation.py`](stream_activation.py) and
[`docs/reference/stream-attribute-activation.md`](../../docs/reference/stream-attribute-activation.md).

Rather than extract that fragile session into Home Assistant, this module builds
a snippet the user runs **on the portal page**, where the session already lives:
same-origin ``fetch`` includes the httpOnly cookies automatically, the browser
sets the ``Origin``/``Referer``/``Sec-Fetch-*`` headers Akamai wants, and **no
secret ever leaves the browser**. The snippet discovers the client id, verifies
the mapping, activates the chosen stream fields, and prints a compact,
**non-secret** result blob the user pastes back into the config flow.

Kept free of Home Assistant imports so the snippet generation and the result
parsing are unit-testable in isolation.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

try:  # normal case: imported as part of the package
    from .descriptors import descriptors_for_sections
except ImportError:  # pragma: no cover - loaded standalone (tests / tools)
    from descriptors import descriptors_for_sections

# The scope that marks an application usable for this integration (the same
# ``cardata:api:read`` the device flow needs). Used to pick a primary client id
# when the account has more than one registered application.
PRIMARY_SCOPE = "cardata:api:read"

# Result blob framing. The snippet base64-encodes a small JSON object and prefixes
# it with ``BAVARIANDATA<version>:`` so the parser can recognise it, version it,
# and reject anything else the user might paste. Bump the integer on a breaking
# change to the payload shape.
RESULT_PREFIX = "BAVARIANDATA1:"
RESULT_VERSION = 1


class OnboardingParseError(Exception):
    """Raised when the pasted onboarding result can't be decoded/validated."""


# Injected marker (not a JS/format token) so the template stays valid JS and can
# carry the chosen descriptor ids verbatim — same technique as
# ``descriptors.build_portal_snippet``.
_IDS_MARKER = "__CARDATA_IDS__"

# The orchestrator. Runs on the portal's stream-setup page. It derives the market
# origin, locale and mapped-vehicle id from ``location`` (so it never has to be
# told which market/vehicle), calls the CarData BFF same-origin, and emits a
# non-secret result blob. Cookies ride along via ``credentials:'include'``; the
# browser — not us — sets Origin/Referer/Sec-Fetch, which is exactly why this is
# robust where a headless client is throttled.
_ONBOARDING_SNIPPET_TEMPLATE = """(async () => {
  const IDS = __CARDATA_IDS__;
  const origin = location.origin;
  const parts = location.pathname.split('/').filter(Boolean);
  const locale = parts[0] || '';
  const mvIdx = parts.indexOf('mapped-vehicle');
  const mappedId = mvIdx >= 0 ? parts[mvIdx + 1] : null;
  const base = origin + '/' + locale;
  const H = { 'Accept': 'application/json', 'x-ocp-stage': 'prod' };
  const out = { v: 1, origin, locale, mappedVehicleId: mappedId,
                clientIds: [], vehicle: {}, activated: null, errors: [] };
  const asJson = async (r) => { try { return await r.json(); } catch (e) { return null; } };

  try {
    const apps = await fetch(base + '/utilities/bmw/api/cd/applications',
                             { headers: H, credentials: 'include' }).then(asJson);
    (apps && apps.data || []).forEach(a =>
      (a.credentials || []).forEach(c => {
        if (c && c.apikey) out.clientIds.push({ apikey: c.apikey, scopes: c.scopes || [] });
      }));
  } catch (e) { out.errors.push('applications: ' + e.message); }

  if (!mappedId) {
    out.errors.push('No mapped-vehicle id in the URL - open a vehicle\\'s stream-setup page first.');
  } else {
    try {
      const md = await fetch(base + '/mybmw/api/mapped-vehicle/' + mappedId + '/mapping-details',
                             { headers: H, credentials: 'include' }).then(asJson);
      const d = (md && md.data) || {};
      out.vehicle = { mappingStatus: d.mappingStatus, subscriberStatus: d.subscriberStatus,
                      isElectricOrHybrid: d.isElectricOrHybrid };
    } catch (e) { out.errors.push('mapping-details: ' + e.message); }

    if (IDS.length) {
      try {
        const r = await fetch(base + '/utilities/bmw/api/cd/streams/' + mappedId, {
          method: 'POST', credentials: 'include',
          headers: Object.assign({ 'Content-Type': 'application/json' }, H),
          body: JSON.stringify({ attributes: IDS }),
        });
        const res = await asJson(r);
        const accepted = (res && res.data && res.data.attributes) || null;
        out.activated = { status: r.status, requested: IDS.length,
                          accepted: accepted ? accepted.length : IDS.length,
                          ok: r.status === 201 };
      } catch (e) { out.errors.push('streams: ' + e.message); }
    }
  }

  const blob = 'BAVARIANDATA1:' + btoa(unescape(encodeURIComponent(JSON.stringify(out))));
  try { await navigator.clipboard.writeText(blob); } catch (e) {}
  console.log('%c BavarianData setup - result copied to clipboard ',
              'background:#1c69d4;color:#fff;padding:2px 6px;border-radius:3px');
  console.log(blob);
  try { window.prompt('Copy this and paste it back into Home Assistant:', blob); } catch (e) {}
})();"""


def build_onboarding_snippet(
    sections: Iterable[str], *, include_diagnostic: bool = False
) -> str:
    """Return the guided-onboarding snippet for a cluster selection.

    Run on the portal's stream-setup page, it discovers the client id, verifies
    the mapping, activates the selected clusters' descriptors on the stream, and
    prints a result blob for :func:`parse_onboarding_result`. The descriptor ids
    are embedded (sorted, deduplicated) exactly like the stream activation call,
    so the snippet and the in-HA service stream the same set for a given choice.
    """

    ids = descriptors_for_sections(sections, include_diagnostic=include_diagnostic)
    return _ONBOARDING_SNIPPET_TEMPLATE.replace(
        _IDS_MARKER, json.dumps(sorted(ids), ensure_ascii=False)
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
    """Decoded, non-secret result of one guided-onboarding snippet run."""

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

        Prefer an application that actually carries ``cardata:api:read``; fall
        back to the first discovered client so a non-standard scope set still
        yields *something* to try rather than nothing.
        """

        for client in self.clients:
            if client.is_primary:
                return client.apikey
        return self.clients[0].apikey if self.clients else None

    @property
    def activated_count(self) -> Optional[int]:
        if isinstance(self.activated, dict):
            value = self.activated.get("accepted")
            return int(value) if isinstance(value, (int, float)) else None
        return None

    @property
    def activation_ok(self) -> bool:
        return bool(isinstance(self.activated, dict) and self.activated.get("ok"))


def parse_onboarding_result(blob: str) -> OnboardingResult:
    """Decode the blob the onboarding snippet prints.

    Tolerant of the surrounding whitespace/quotes a paste can pick up. Raises
    :class:`OnboardingParseError` with a user-facing message on anything that
    isn't a valid, current-version BavarianData result.
    """

    if not blob or not blob.strip():
        raise OnboardingParseError("Nothing pasted.")
    text = blob.strip().strip('"').strip("'").strip()
    if not text.startswith(RESULT_PREFIX):
        raise OnboardingParseError(
            "That doesn't look like a BavarianData setup result. Run the snippet "
            "on the BMW portal page and paste exactly what it copied."
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
            "That result was produced by a different version of the snippet. "
            "Re-copy the snippet from Home Assistant and run it again."
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
