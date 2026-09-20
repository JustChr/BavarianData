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

# How long a cluster that has produced *nothing* keeps being called a gap while
# every other selected cluster is healthy. Past this, the likelier reading flips:
# the selection demonstrably saved (the others arrived), so what is left is a car
# that does not have those fields at all. A 2019 i3s streams no tyre pressure and
# never will; before this, it was told for the rest of its life that 178 of 224
# fields were overdue and shown a repair it could do nothing about (issue #13).
# A month is long enough to have driven, charged and locked the car repeatedly.
UNSUPPORTED_AFTER_DAYS = 30

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


def monitoring_since(
    started_at: Optional[datetime], first_seen: Optional[datetime]
) -> Optional[datetime]:
    """When one vehicle's grace window starts.

    ``started_at`` is the entry's clock, which restarts whenever the cluster
    selection changes -- a new selection has to be given its own chance. It
    cannot answer for a car added to the account later: one entry covers a whole
    CarData account, so a car bought months in would be judged against the day
    the *first* car was set up, be past grace on arrival, and be reported as a
    cluster that has sent nothing before it had a chance to send anything.

    So the window starts at whichever is later. A vehicle with no first sighting
    (every record written before this existed) keeps the entry's clock, which is
    exactly what it was already judged by.
    """

    if first_seen is None:
        return started_at
    if started_at is None:
        return first_seen
    return max(started_at, first_seen)


def backfill_first_seen(
    seen: Mapping[str, Mapping[str, str]], first_seen: Mapping[str, str]
) -> dict[str, str]:
    """First sightings to add for cars recorded before they were kept.

    Each car's earliest descriptor arrival is when it turned up, and the stored
    record has those timestamps already. Without reading them back, the upgrade
    that introduced per-vehicle clocks would itself look like first contact:
    every car would be handed a brand-new grace window and every real coverage
    gap would go quiet for a week.
    """

    recovered: dict[str, str] = {}
    for vin, descriptors in seen.items():
        if vin in first_seen:
            continue
        stamps = [stamp for stamp in descriptors.values() if stamp]
        if stamps:
            recovered[vin] = min(stamps)
    return recovered


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


def unsupported_sections(
    clusters: "Collection[ClusterCoverage]",
    *,
    monitoring_days: float,
    excluded: Collection[str] = (),
    unsupported_after_days: int = UNSUPPORTED_AFTER_DAYS,
) -> set[str]:
    """Clusters whose silence is better explained by the car than the selection.

    Deliberately narrow, because the cost of being wrong is silencing a genuine
    misconfiguration. All three have to hold:

    * the car has been watched for ``unsupported_after_days``;
    * **exactly one** cluster is silent -- two or more is the signature of a
      Data Selection that did not save, which is worth the repair; and
    * at least one other cluster has delivered, proving the selection *did*
      save and the stream works.

    Event-driven clusters and ones already excluded (the high-voltage clusters
    on a combustion car) never take part: their silence is explained already.
    """

    if monitoring_days < unsupported_after_days:
        return set()
    excluded_set = set(excluded)
    candidates = [
        cluster
        for cluster in clusters
        if cluster.expected
        and cluster.section not in EVENT_DRIVEN_SECTIONS
        and cluster.section not in excluded_set
    ]
    if len(candidates) < 2:
        return set()
    silent = [cluster for cluster in candidates if not cluster.seen]
    if len(silent) != 1:
        return set()
    return {silent[0].section}


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
    # Selected clusters this car can never fill: the high-voltage ones on a
    # combustion car, plus any single cluster still silent after
    # ``UNSUPPORTED_AFTER_DAYS`` while the rest of the stream is healthy. Still
    # tallied in ``expected``/``missing``, never overdue and never warned about.
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
    unsupported_after_days: int = UNSUPPORTED_AFTER_DAYS,
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
    drivetrain_excluded = (
        HIGH_VOLTAGE_SECTIONS & set(expected_by_section)
        if is_combustion_only(seen_set)
        else set()
    )
    not_applicable = sorted(
        drivetrain_excluded
        | unsupported_sections(
            clusters,
            monitoring_days=monitoring_days,
            excluded=drivetrain_excluded,
            unsupported_after_days=unsupported_after_days,
        )
    )
    # A field the car cannot produce is not overdue, it is absent -- counting it
    # is what turned a healthy i3s into "178 overdue" (issue #13).
    overdue = (
        [
            descriptor
            for cluster in clusters
            if cluster.section not in not_applicable
            for descriptor in cluster.missing
        ]
        if past_grace
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
