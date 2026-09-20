"""Diagnostics support for the BavarianData integration.

Nearly every failure here is BMW-side -- Data Selection not saved, a cluster the
car never produces, the daily REST quota spent, or another client holding the
single per-account stream -- and all of them look identical from the outside: a
quiet, healthy-seeming integration. A redacted diagnostics download turns "it
doesn't work" into 30-second triage by dumping the four things that actually
disambiguate those causes: quota state, the selected clusters, per-VIN
descriptor arrival counts + last-message timestamps, and the MQTT
connect/disconnect history with its rc codes.

VIN, GCID, client id, tokens and GPS never leave the box: the payload is built
from a deliberately safe subset (descriptor *names* and counts, never values),
and ``async_redact_data`` runs over the result as a second line of defence.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import CardataConfigEntry, _coverage_reports
from .const import (
    BOOTSTRAP_COMPLETE,
    DOMAIN,
    OPTION_GRID_ENERGY_ENTITY,
    OPTION_PRICE_ENTITY,
    OPTION_STREAM_SECTIONS,
    REQUEST_LIMIT,
)

# Redacted by key wherever they appear in the payload (including inside the
# stream ``parameters`` and each vehicle entry). ``topic`` is here because its
# value embeds the GCID (``<gcid>/+``).
TO_REDACT = {
    "vin",
    "gcid",
    "client_id",
    "id_token",
    "access_token",
    "refresh_token",
    "password",
    "serial_number",
    "hv_container_id",
    "topic",
    "latitude",
    "longitude",
}


# Options whose value is an entity id the user picked. Triage needs to know
# whether one is configured, never which one: an entity id is free text chosen by
# whichever integration created it, and it routinely embeds identifiers of its
# own. A real report carried
# ``sensor.octopus_energy_electricity_<meter serial>_<MPAN>_current_accumulative_cost``
# -- an electricity meter serial and the supply point of a home, pasted into a
# public issue by a file we asked for. Reduced to the domain, which is all the
# triage actually used.
ENTITY_ID_OPTIONS = frozenset({OPTION_PRICE_ENTITY, OPTION_GRID_ENERGY_ENTITY})


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _fingerprint(value: Any) -> Optional[str]:
    """A short, stable, non-reversible stand-in for a redacted identifier.

    The GCID is redacted -- it identifies the account -- but "do these two
    config entries belong to the same account?" is the first question when two
    of them misbehave together, because BMW allows **one concurrent stream per
    GCID**. Redaction made that unanswerable from two dumps (issue #23's
    reporter runs two accounts). A truncated digest answers it and reveals
    nothing: it cannot be reversed, and it only ever matches another dump of
    the same account.
    """

    if not value or not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_options(options: Any) -> dict[str, Any]:
    """Config-entry options with user-chosen entity ids reduced to their domain."""

    safe: dict[str, Any] = {}
    for key, value in dict(options or {}).items():
        if key in ENTITY_ID_OPTIONS and isinstance(value, str) and value:
            domain = value.split(".", 1)[0]
            safe[key] = f"{domain}.**REDACTED**" if "." in value else "**REDACTED**"
        else:
            safe[key] = value
    return safe


def _drivetrain_facts(coordinator: Any, vin: str) -> dict[str, Any]:
    """``driveTrain``/``propulsionType`` and the model, straight from basic data."""

    attrs = (coordinator.device_metadata.get(vin) or {}).get("extra_attributes") or {}
    return {
        "model_name": attrs.get("model_name"),
        "drive_train": attrs.get("drive_train"),
        "propulsion_type": attrs.get("propulsion_type"),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CardataConfigEntry
) -> dict[str, Any]:
    """Return a redacted diagnostics snapshot for the config entry."""

    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    integration = await async_get_integration(hass, DOMAIN)

    quota = runtime.quota_manager
    quota_info: dict[str, Any] = {"configured": quota is not None}
    if quota is not None:
        quota_info.update(
            {
                "limit": REQUEST_LIMIT,
                "used": quota.used,
                "remaining": quota.remaining,
                "next_reset": quota.next_reset_iso,
            }
        )

    # Map coverage reports by VIN so each vehicle can carry its own summary
    # without dumping the (long) full missing-descriptor list twice.
    coverage_by_vin = {report.vin: report for report in _coverage_reports(runtime)}

    vehicles: list[dict[str, Any]] = []
    for vin in coordinator.data:
        counts = coordinator.descriptor_counts.get(vin, {})
        report = coverage_by_vin.get(vin)
        vehicles.append(
            {
                "vin": vin,
                "name": coordinator.names.get(vin),
                "descriptor_count": len(coordinator.data.get(vin, {})),
                "arrivals_total": sum(counts.values()),
                "last_message_at": _iso(coordinator.last_message_by_vin.get(vin)),
                "coverage": None
                if report is None
                else {
                    "expected": report.expected,
                    "seen": report.seen,
                    "coverage_percent": report.coverage_percent,
                    "past_grace": report.past_grace,
                    "overdue_count": len(report.overdue),
                    "silent_clusters": [c.section for c in report.overdue_clusters()],
                    "not_applicable": list(report.not_applicable),
                },
                "descriptors": coordinator.descriptor_diagnostics(vin),
                # Not a value from the car's stream but the answer to "what kind
                # of car is this", which decides the card's whole overview and
                # which EV-only entities exist. Neither is sensitive -- they are
                # model facts, not identifiers -- and without them a wrong layout
                # cannot be diagnosed from a dump at all (issue #23).
                **_drivetrain_facts(coordinator, vin),
            }
        )

    payload: dict[str, Any] = {
        "integration": {
            "domain": DOMAIN,
            "version": str(integration.version) if integration.version else None,
        },
        "home_assistant_version": HA_VERSION,
        "config_entry": {
            "title": entry.title,
            "bootstrap_complete": bool(entry.data.get(BOOTSTRAP_COMPLETE)),
            "selected_clusters": entry.data.get(OPTION_STREAM_SECTIONS) or [],
            "options": _safe_options(entry.options),
        },
        "quota": quota_info,
        "stream": {
            "connection_status": coordinator.connection_status,
            "last_disconnect_reason": coordinator.last_disconnect_reason,
            "last_message_at": _iso(coordinator.last_message_at),
            "stream_started_at": _iso(coordinator.stream_started_at),
            "connection_history": list(coordinator.connection_history),
            "parameters": runtime.stream.debug_info,
            # Survives the redaction of ``gcid`` above; see ``_fingerprint``.
            "account_fingerprint": _fingerprint((runtime.stream.debug_info or {}).get("gcid")),
        },
        "vehicles": vehicles,
    }

    return async_redact_data(payload, TO_REDACT)
