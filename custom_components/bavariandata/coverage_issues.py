"""The coverage self-test as repairs: which selected clusters have sent nothing, per car."""

from __future__ import annotations

import logging
from typing import Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
)
from .coverage import CoverageReport, coverage_issue_id, legacy_coverage_issue_id
from .debug import (
    mask_vin,
)
from .runtime import CardataRuntimeData

_LOGGER = logging.getLogger(__name__)


def coverage_reports(
    runtime: "CardataRuntimeData", vin: Optional[str] = None
) -> list[CoverageReport]:
    """Build the coverage self-test reports, folding in live descriptor state.

    The persisted store outlives a disabled entity; the live coordinator state
    covers a wiped store or a descriptor seen before the store existed. Their
    union is what the report should judge against.
    """

    if runtime.coverage is None:
        return []
    live = {
        known_vin: set(descriptors) for known_vin, descriptors in runtime.coordinator.data.items()
    }
    reports = runtime.coverage.reports(
        live,
        drive_trains={
            known_vin: runtime.coordinator.drive_train(known_vin)
            for known_vin in set(live) | set(runtime.coordinator.device_metadata)
        },
    )
    if vin is not None:
        reports = [report for report in reports if report.vin == vin]
    return reports


@callback
def refresh_coverage_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Create or clear a per-vehicle repair issue from the coverage self-test."""

    entry = hass.config_entries.async_get_entry(entry_id)
    runtime: Optional["CardataRuntimeData"] = (
        getattr(entry, "runtime_data", None) if entry else None
    )
    if runtime is None or runtime.coverage is None:
        return
    for report in coverage_reports(runtime):
        issue_id = coverage_issue_id(entry_id, report.vin)
        # Clear the pre-0.9.6 id unconditionally: it embedded the raw VIN, and
        # leaving it in place would both orphan a duplicate repair and keep the
        # VIN in the issue registry that diagnostics downloads dump.
        ir.async_delete_issue(hass, DOMAIN, legacy_coverage_issue_id(entry_id, report.vin))
        overdue_clusters = report.overdue_clusters()
        if not overdue_clusters:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        # Falls back to the masked VIN, never the raw one: a repair notification
        # is a screenshot away from a public issue (see debug.mask_vin).
        vehicle = runtime.coordinator.names.get(report.vin) or mask_vin(report.vin)
        cluster_labels = ", ".join(cluster.label for cluster in overdue_clusters)
        # A handful of concrete descriptors makes the issue actionable without
        # dumping the whole missing list into a notification body.
        silent = [d for cluster in overdue_clusters for d in cluster.missing]
        examples = ", ".join(silent[:5])
        if len(silent) > 5:
            examples = f"{examples}, ..."
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="stream_coverage_gaps",
            translation_placeholders={
                "vehicle": vehicle,
                "count": str(len(silent)),
                "days": str(report.grace_days),
                "clusters": cluster_labels,
                "descriptors": examples,
            },
        )
