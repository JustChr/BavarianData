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

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .coverage_issues import coverage_reports
from .runtime import CardataConfigEntry
from .const import (
    BOOTSTRAP_COMPLETE,
    DOMAIN,
    OPTION_STREAM_SECTIONS,
    REQUEST_LIMIT,
)
from .diagnostics_privacy import fingerprint, safe_options

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
    "service_container_id",
    "topic",
    "latitude",
    "longitude",
}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


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
    coverage_by_vin = {report.vin: report for report in coverage_reports(runtime)}

    vehicles: list[dict[str, Any]] = []
    for vin in coordinator.data:
        counts = coordinator.descriptor_counts.get(vin, {})
        report = coverage_by_vin.get(vin)
        vehicles.append(
            {
                "vin": vin,
                # Matches this car's settings in ``options.vehicles``.
                "vin_fingerprint": fingerprint(vin),
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
                # How this car reports (``car_profile.py``): counts and gaps only.
                "behaviour": None
                if (profile := coordinator.car_profiles.get(vin)) is None
                else profile.as_dict(datetime.now(timezone.utc)),
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
            "options": safe_options(entry.options),
        },
        "quota": quota_info,
        "stream": {
            "connection_status": coordinator.connection_status,
            "last_disconnect_reason": coordinator.last_disconnect_reason,
            "last_message_at": _iso(coordinator.last_message_at),
            "stream_started_at": _iso(coordinator.stream_started_at),
            "connection_history": list(coordinator.connection_history),
            "parameters": runtime.stream.debug_info,
            # Survives the redaction of ``gcid`` above; see ``diagnostics_privacy.fingerprint``.
            "account_fingerprint": fingerprint((runtime.stream.debug_info or {}).get("gcid")),
        },
        "vehicles": vehicles,
    }

    return async_redact_data(payload, TO_REDACT)
