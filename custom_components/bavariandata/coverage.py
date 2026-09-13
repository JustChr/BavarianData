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

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Collection, Mapping, Optional

# How long a selected-but-unseen descriptor is given before it is called
# "overdue". BMW streams many descriptors only on a state change (a charge, a
# drive, a door), so a fresh install must not cry wolf on day one -- seven days
# is long enough to have driven and charged at least once.
DEFAULT_GRACE_DAYS = 7

# BMW publishes one catalogue for the whole fleet, so no car streams every
# descriptor of a cluster: an i5 has no third seat row, convertible roof or fuel
# tank. Measured on the maintainer's i5, a per-descriptor alarm listed 154
# "overdue" fields on a perfectly healthy stream. What the self-test can honestly
# catch is a cluster that has sent *nothing* -- a Data Selection that never saved
# or does not match the picker -- so only that raises a repair.

# Clusters that fire only on a rare event -- a teleservice call can be months
# apart -- where silence says nothing about the selection.
EVENT_DRIVEN_SECTIONS = frozenset({"events"})

# Clusters that exist for a high-voltage battery. The default selection asks for
# them on every car, and a combustion car can never answer.
HIGH_VOLTAGE_SECTIONS = frozenset({"electric", "basic"})

# Any of these arriving marks a high-voltage battery (the same signals sensor.py
# uses to decide battery health is worth tracking). Deliberately not "anything
# from the electric cluster": a petrol F87 M2 streams the EV charge *target*.
HIGH_VOLTAGE_SIGNALS = frozenset(
    {
        "vehicle.drivetrain.batteryManagement.batterySizeMax",
        "vehicle.drivetrain.batteryManagement.maxEnergy",
        "vehicle.drivetrain.batteryManagement.header",
    }
)
COMBUSTION_PREFIXES = (
    "vehicle.drivetrain.fuelSystem.",
    "vehicle.drivetrain.internalCombustionEngine.",
)


def has_high_voltage(seen: Collection[str]) -> bool:
    """Whether the car has shown a high-voltage battery."""

    return not HIGH_VOLTAGE_SIGNALS.isdisjoint(seen)


def has_combustion(seen: Collection[str]) -> bool:
    """Whether the car has shown a fuel system or an engine."""

    return any(descriptor.startswith(COMBUSTION_PREFIXES) for descriptor in seen)


def is_combustion_only(seen: Collection[str]) -> bool:
    """Whether the car has shown an engine and no high-voltage battery.

    Needs positive evidence both ways: a car that has sent neither yet (a fresh
    install, or the status cluster unselected) is not assumed to be anything.
    """

    seen_set = set(seen)
    return has_combustion(seen_set) and not has_high_voltage(seen_set)


def is_plug_in_hybrid(seen: Collection[str]) -> bool:
    """Whether the car has shown both a high-voltage battery and a fuel system."""

    seen_set = set(seen)
    return has_high_voltage(seen_set) and has_combustion(seen_set)


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
    # Selected clusters this car's drivetrain can never fill (the high-voltage
    # clusters on a combustion car). Still tallied, never warned about.
    not_applicable: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def has_gaps(self) -> bool:
        """A gap worth surfacing: a cluster that is silent past grace."""

        return bool(self.overdue_clusters())

    def overdue_clusters(self) -> list[ClusterCoverage]:
        """Clusters worth a repair: past grace, and not one descriptor arrived.

        A partly-filled cluster is the fleet catalogue meeting one car, not a
        fault (see the module note above ``EVENT_DRIVEN_SECTIONS``). Event-driven
        clusters and ones the drivetrain cannot fill are left out.
        """

        if not self.past_grace:
            return []
        return [
            cluster
            for cluster in self.clusters
            if cluster.expected
            and not cluster.seen
            and cluster.section not in EVENT_DRIVEN_SECTIONS
            and cluster.section not in self.not_applicable
        ]

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
            "not_applicable": list(self.not_applicable),
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
    not_applicable = (
        sorted(HIGH_VOLTAGE_SECTIONS & set(expected_by_section))
        if is_combustion_only(seen_set)
        else []
    )

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
        not_applicable=not_applicable,
    )


# --- Repair-issue identity ------------------------------------------------

COVERAGE_ISSUE_PREFIX = "stream_coverage_gaps"


def coverage_issue_id(entry_id: str, vin: str) -> str:
    """A stable per-vehicle repair id that does not contain the VIN.

    Repair ids are **not** covered by the integration's diagnostics redaction:
    Home Assistant's own diagnostics wrapper appends the registered issues to the
    download, outside the payload ``diagnostics.py`` builds and redacts. A VIN
    embedded here therefore rides straight into whatever the user attaches to a
    public issue -- which is exactly how ``WBY1...`` reached issue #6, in the one
    file we ask people to upload.

    The digest keeps the id stable across restarts (so an existing repair is
    still found and cleared) and unique per vehicle within an entry, without
    carrying the identifier itself.
    """

    digest = hashlib.sha256(vin.encode("utf-8")).hexdigest()[:12]
    return f"{COVERAGE_ISSUE_PREFIX}_{entry_id}_{digest}"


def legacy_coverage_issue_id(entry_id: str, vin: str) -> str:
    """The pre-0.9.6 id, which embedded the raw VIN.

    Kept only so an install that already raised one can have it deleted rather
    than orphaned beside its replacement -- which also clears the leaked value
    out of that user's issue registry.
    """

    return f"{COVERAGE_ISSUE_PREFIX}_{entry_id}_{vin}"
