"""Cluster / descriptor / streaming-scope helpers.

Single source of truth for turning the catalogue's *clusters* (BMW's sections,
e.g. ``electric``, ``status``, ``tire``) into the set of descriptors they cover
and into the granular OAuth streaming scopes BMW uses to gate the MQTT stream.

Kept free of Home Assistant imports so the config flow, the token flow, the
generators in ``tools/`` and the unit tests can all share one derivation and can
never drift apart. The data comes from the generated
:mod:`descriptor_metadata` (``SECTIONS`` and ``DESCRIPTOR_META``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable

try:  # normal case: imported as part of the package
    from .descriptor_metadata import DESCRIPTOR_META, SECTIONS
except ImportError:  # pragma: no cover - loaded standalone (tests / tools)
    from descriptor_metadata import DESCRIPTOR_META, SECTIONS

# BMW expresses per-descriptor streaming entitlements as OAuth scopes of the
# form ``cardata:streaming:<descriptor>``. See
# docs/reference/bmw-cardata-streaming-guide.md ("dynamic scopes").
STREAMING_SCOPE_PREFIX = "cardata:streaming:"

# Scopes always requested regardless of the cluster selection: user auth, OpenID,
# the read side of the REST API and the coarse streaming scope. The granular
# per-descriptor streaming scopes are appended on top of these.
#
# ``cardata:streaming:read`` is kept even when granular scopes are added: BMW's
# device-code endpoint rejects a streaming authorization that omits it with a
# generic ``invalid_request`` (400). Keeping the base ordering identical to
# DEFAULT_SCOPE means an empty cluster selection reproduces DEFAULT_SCOPE exactly.
BASE_SCOPES: tuple[str, ...] = (
    "authenticate_user",
    "openid",
    "cardata:api:read",
    "cardata:streaming:read",
)


def section_labels() -> dict[str, str]:
    """Return an ordered ``slug -> human label`` mapping of every cluster."""

    return dict(SECTIONS)


def default_sections() -> list[str]:
    """Clusters worth enabling by default.

    A cluster is "relevant" when it contains at least one descriptor that is
    enabled by default (i.e. not part of the diagnostic long tail) *and* that BMW
    can actually stream. This is the pre-checked set in the picker, so one
    confirmation yields a sensible stream.
    """

    relevant = {
        meta["section"]
        for meta in DESCRIPTOR_META.values()
        if meta.get("enabled_default") and meta.get("streamable", True)
    }
    # Preserve catalogue order from SECTIONS.
    return [slug for slug in SECTIONS if slug in relevant]


def descriptors_for_sections(
    sections: Iterable[str],
    *,
    include_diagnostic: bool = False,
    include_unstreamable: bool = False,
) -> list[str]:
    """Return the sorted *streamable* descriptors belonging to the given clusters.

    Every caller of this is about the MQTT stream — the cluster picker, the portal
    snippet, the stream activator and the coverage self-test — so descriptors BMW
    marks as not streaming-capable are excluded. They still get entities; some
    arrive over REST via a container instead. Asking for them would put fields on
    the wire that can never answer, and make the coverage report list them as
    permanently missing.

    By default the diagnostic long tail is excluded too — those entities are
    created disabled anyway, so streaming them only adds wire noise. Pass
    ``include_diagnostic=True`` to stream everything in the selected clusters, and
    ``include_unstreamable=True`` to get the raw cluster membership (used by the
    tests that assert the clusters partition the whole catalogue).
    """

    wanted = set(sections)
    result = [
        descriptor
        for descriptor, meta in DESCRIPTOR_META.items()
        if meta["section"] in wanted
        and (include_diagnostic or meta.get("enabled_default"))
        and (include_unstreamable or meta.get("streamable", True))
    ]
    return sorted(result)


# Descriptors BMW serves only from their own REST endpoint. Per the Integration
# Guide (3.3.2, 3.3.4) they may be *added* to a container, but GET /telematicData
# will never return them — so putting them in one only makes the request bigger.
DEDICATED_ENDPOINT_DESCRIPTORS: frozenset[str] = frozenset(
    {
        "vehicle.chassis.axle.wheel.tire.diagnosis",
        "vehicle.look.image",
        "vehicle.powertrain.electric.battery.charging.history.sessionsList",
        "vehicle.powertrain.electric.battery.charging.settingsList",
    }
)

# BMW's BASIC_DATA category is likewise excluded from /telematicData; those fields
# come from GET /basicData instead. Our catalogue calls that section "basic".
_BASIC_DATA_SECTION = "basic"


def container_retrievable(descriptor: str) -> bool:
    """Whether ``GET /telematicData`` can actually return this descriptor."""

    meta = DESCRIPTOR_META.get(descriptor)
    if meta is None:
        return False
    return (
        meta["section"] != _BASIC_DATA_SECTION
        and descriptor not in DEDICATED_ENDPOINT_DESCRIPTORS
    )


def container_descriptors(seed: Iterable[str] = ()) -> list[str]:
    """The descriptor set worth holding in the REST container.

    Everything BMW cannot stream — those fields have no other route into Home
    Assistant — plus any ``seed`` descriptors the caller wants fetched eagerly
    (the battery keys, so a fresh install shows values before the first stream
    message). Descriptors the container endpoint can't serve are dropped either
    way, so the daily call never carries dead weight.

    One call returns the whole set, which is what makes a daily refresh affordable
    against BMW's 50-requests-per-day quota.
    """

    candidates = set(seed) | {
        descriptor
        for descriptor, meta in DESCRIPTOR_META.items()
        if not meta.get("streamable", True)
    }
    return sorted(d for d in candidates if container_retrievable(d))


def streaming_scope(descriptor: str) -> str:
    """Return the ``cardata:streaming:`` scope for a single descriptor."""

    return STREAMING_SCOPE_PREFIX + descriptor


def build_scope(
    sections: Iterable[str],
    *,
    include_diagnostic: bool = False,
    base_scopes: Iterable[str] = BASE_SCOPES,
) -> str:
    """Build the space-delimited OAuth scope string for a cluster selection.

    Combines the always-on :data:`BASE_SCOPES` with one streaming scope per
    descriptor in the selected clusters. Order is stable (base scopes first,
    then descriptors sorted) so the same selection always yields the same
    string — useful for change detection and idempotent re-auth.

    Note: BMW's device-code endpoint rejects the granular streaming scopes with
    ``400 invalid_request`` (see docs/reference/stream-scope-investigation.md), so
    this is retained for reference/tests only — the runtime uses the portal
    snippet (:func:`build_portal_snippet`) instead.
    """

    scopes = list(base_scopes)
    scopes.extend(
        streaming_scope(descriptor)
        for descriptor in descriptors_for_sections(
            sections, include_diagnostic=include_diagnostic
        )
    )
    return " ".join(scopes)


# Injected marker (not a JS/format token) so the template stays valid JS.
_IDS_MARKER = "__CARDATA_IDS__"

# The BMW portal's Data Selection page is a table whose "Technischer Beschreiber"
# column is the raw descriptor. Each checkbox <label> lives in a cell
# (``div.css-k008qs``) next to a <p> holding that descriptor, so the cell's text
# is exactly the descriptor. We match that against the selected clusters' set.
_PORTAL_SNIPPET_TEMPLATE = """(() => {
  const wanted = new Set(__CARDATA_IDS__);
  const labels = [...document.querySelectorAll('.css-k008qs label.chakra-checkbox')];
  let matched = 0, checked = 0;
  labels.forEach(label => {
    const cell = label.parentElement;              // div.css-k008qs (label + descriptor <p>)
    const descriptor = (cell ? cell.textContent : '').replace(/\\s+/g, '');
    if (!wanted.has(descriptor)) return;
    matched++;
    const input = label.querySelector('input.chakra-checkbox__input[type="checkbox"]');
    if (!input || input.disabled || input.checked) return;
    label.click();
    if (!input.checked) { const c = label.querySelector('.chakra-checkbox__control'); if (c) c.click(); }
    if (!input.checked) {
      input.checked = true;
      ['click', 'input', 'change'].forEach(t => input.dispatchEvent(new Event(t, { bubbles: true })));
    }
    if (input.checked) checked++;
  });
  console.log(`Matched ${matched} of ${labels.length} attributes for your clusters; newly checked ${checked}. Existing selections were left as-is; save in the portal.`);
})();"""


def build_portal_snippet(
    sections: Iterable[str], *, include_diagnostic: bool = False
) -> str:
    """Return a browser-console snippet that ticks the selected clusters.

    Run in the BMW portal's Data Selection page, it checks only the checkboxes
    whose descriptor is in the selected clusters (matched exactly against the
    portal's technical-descriptor column), leaving other selections untouched,
    and logs how many it matched.
    """

    ids = descriptors_for_sections(sections, include_diagnostic=include_diagnostic)
    return _PORTAL_SNIPPET_TEMPLATE.replace(
        _IDS_MARKER, json.dumps(sorted(ids), ensure_ascii=False)
    )
