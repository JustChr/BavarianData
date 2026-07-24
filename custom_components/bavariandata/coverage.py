"""Descriptor coverage self-test ("Beyond the roadmap").

Compares the descriptors a user asked BMW to stream -- their cluster selection --
against those that have actually arrived. A cluster the car never produces, or a
portal Data Selection that silently doesn't match the picker, otherwise looks
identical to a healthy-but-quiet stream: entities simply never appear and nobody
knows whether that is the car, the selection, or a bug. This turns that unknown
into an answer.

Pure functions and dataclasses only -- no Home Assistant import -- so the whole
analysis runs in the repo's HA-free test harness. Persistence (the grace clock,
the seen-descriptor record) and the service / repair-issue wiring live in the
HA-importing siblings (``coverage_store.py``) and ``__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Collection, Mapping, Optional

# How long a selected-but-unseen descriptor is given before it is called
# "overdue". BMW streams many descriptors only on a state change (a charge, a
# drive, a door), so a fresh install must not cry wolf on day one -- seven days
# is long enough to have driven and charged at least once.
DEFAULT_GRACE_DAYS = 7


@dataclass
class ClusterCoverage:
    """Per-cluster tally of expected vs. actually-seen descriptors."""

    section: str
    label: str
    expected: int
    seen: int
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "label": self.label,
            "expected": self.expected,
            "seen": self.seen,
            "missing": list(self.missing),
        }


@dataclass
class CoverageReport:
    """The self-test result for one vehicle."""

    vin: str
    monitoring_since: Optional[str]
    monitoring_days: float
    grace_days: int
    past_grace: bool
    expected: int
    seen: int
    coverage_percent: float
    missing: list[str] = field(default_factory=list)
    # ``missing`` restricted to descriptors that are now genuinely overdue (past
    # the grace window). Empty while still within grace, so callers can wait
    # before alarming.
    overdue: list[str] = field(default_factory=list)
    clusters: list[ClusterCoverage] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def has_gaps(self) -> bool:
        """A gap worth surfacing: something is overdue, not merely still waiting."""

        return bool(self.overdue)

    def overdue_clusters(self) -> list[ClusterCoverage]:
        """Clusters carrying at least one overdue descriptor (past grace only)."""

        if not self.past_grace:
            return []
        return [cluster for cluster in self.clusters if cluster.missing]

    def to_dict(self) -> dict[str, Any]:
        return {
            "vin": self.vin,
            "monitoring_since": self.monitoring_since,
            "monitoring_days": round(self.monitoring_days, 2),
            "grace_days": self.grace_days,
            "past_grace": self.past_grace,
            "expected": self.expected,
            "seen": self.seen,
            "coverage_percent": self.coverage_percent,
            "complete": self.complete,
            "missing": list(self.missing),
            "overdue": list(self.overdue),
            "clusters": [cluster.to_dict() for cluster in self.clusters],
        }


def analyze_coverage(
    *,
    vin: str,
    expected_by_section: Mapping[str, Collection[str]],
    labels: Mapping[str, str],
    seen: Collection[str],
    monitoring_since: Optional[datetime],
    now: datetime,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> CoverageReport:
    """Build a :class:`CoverageReport` from the raw inputs.

    ``expected_by_section`` maps each enabled cluster to the descriptors it
    should stream (already narrowed to the non-diagnostic set the picker
    requests). ``seen`` is every descriptor that has actually arrived for this
    vehicle. Cluster order in the report follows ``expected_by_section``.
    """

    seen_set = set(seen)
    monitoring_days = 0.0
    if monitoring_since is not None:
        monitoring_days = max((now - monitoring_since).total_seconds() / 86400.0, 0.0)
    past_grace = monitoring_since is not None and monitoring_days >= grace_days

    clusters: list[ClusterCoverage] = []
    all_missing: list[str] = []
    total_expected = 0
    total_seen = 0
    for section, descriptors in expected_by_section.items():
        ordered = list(descriptors)
        missing = [descriptor for descriptor in ordered if descriptor not in seen_set]
        seen_count = len(ordered) - len(missing)
        total_expected += len(ordered)
        total_seen += seen_count
        all_missing.extend(missing)
        clusters.append(
            ClusterCoverage(
                section=section,
                label=labels.get(section, section),
                expected=len(ordered),
                seen=seen_count,
                missing=missing,
            )
        )

    coverage_percent = (
        round(100.0 * total_seen / total_expected, 1) if total_expected else 100.0
    )
    overdue = list(all_missing) if past_grace else []

    return CoverageReport(
        vin=vin,
        monitoring_since=monitoring_since.isoformat() if monitoring_since else None,
        monitoring_days=monitoring_days,
        grace_days=grace_days,
        past_grace=past_grace,
        expected=total_expected,
        seen=total_seen,
        coverage_percent=coverage_percent,
        missing=all_missing,
        overdue=overdue,
        clusters=clusters,
    )
