"""Unit tests for the descriptor-coverage self-test ("Beyond the roadmap").

The analysis in ``coverage.py`` is Home Assistant-free by design -- it turns a
cluster selection, the descriptors that have arrived, and a grace clock into a
per-vehicle report -- so it is covered here without an HA install, the same
discipline as the history layer. ``coverage_store.py`` (the Store wrapper) and
the service / repair-issue wiring import HA and are exercised on a live instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

coverage = load_module("coverage")

analyze_coverage = coverage.analyze_coverage
DEFAULT_GRACE_DAYS = coverage.DEFAULT_GRACE_DAYS

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)

LABELS = {"electric": "Electric", "tire": "Tyres"}
EXPECTED = {
    "electric": ["desc.soc", "desc.range", "desc.power"],
    "tire": ["desc.tyre.fl", "desc.tyre.fr"],
}


def _analyze(seen, *, since=NOW - timedelta(days=30), grace=DEFAULT_GRACE_DAYS):
    return analyze_coverage(
        vin="VIN1",
        expected_by_section=EXPECTED,
        labels=LABELS,
        seen=seen,
        monitoring_since=since,
        now=NOW,
        grace_days=grace,
    )


def test_full_coverage_is_complete_and_100_percent():
    every = [d for group in EXPECTED.values() for d in group]
    report = _analyze(every)
    assert report.expected == 5
    assert report.seen == 5
    assert report.coverage_percent == 100.0
    assert report.complete
    assert report.missing == []
    assert report.overdue == []
    assert not report.has_gaps


def test_partial_within_grace_lists_missing_but_not_overdue():
    report = _analyze(["desc.soc", "desc.tyre.fl"], since=NOW - timedelta(days=2))
    assert report.seen == 2
    assert report.expected == 5
    assert not report.past_grace
    # Everything unseen is "missing" but nothing is "overdue" yet.
    assert set(report.missing) == {"desc.range", "desc.power", "desc.tyre.fr"}
    assert report.overdue == []
    assert not report.has_gaps
    assert report.overdue_clusters() == []


def test_partial_past_grace_promotes_missing_to_overdue():
    report = _analyze(["desc.soc"], since=NOW - timedelta(days=10))
    assert report.past_grace
    assert set(report.missing) == {
        "desc.range",
        "desc.power",
        "desc.tyre.fl",
        "desc.tyre.fr",
    }
    assert report.overdue == report.missing
    assert report.has_gaps
    # Both clusters carry an overdue descriptor.
    assert {c.section for c in report.overdue_clusters()} == {"electric", "tire"}


def test_missing_preserves_cluster_and_descriptor_order():
    report = _analyze([], since=NOW - timedelta(days=10))
    # electric's descriptors come before tire's, each in declaration order.
    assert report.missing == [
        "desc.soc",
        "desc.range",
        "desc.power",
        "desc.tyre.fl",
        "desc.tyre.fr",
    ]


def test_per_cluster_tallies():
    report = _analyze(["desc.soc", "desc.range"], since=NOW - timedelta(days=10))
    by_section = {c.section: c for c in report.clusters}
    assert by_section["electric"].expected == 3
    assert by_section["electric"].seen == 2
    assert by_section["electric"].missing == ["desc.power"]
    assert not by_section["electric"].complete
    assert by_section["tire"].seen == 0
    assert by_section["tire"].missing == ["desc.tyre.fl", "desc.tyre.fr"]
    assert by_section["electric"].label == "Electric"


def test_coverage_percent_rounds_to_one_decimal():
    # 1 of 5 seen -> 20.0, 2 of 3 in electric alone would be 66.7 etc.
    report = _analyze(["desc.soc"], since=NOW - timedelta(days=10))
    assert report.coverage_percent == 20.0


def test_no_monitoring_since_never_alarms():
    # Before the clock has started there is nothing to be overdue against.
    report = analyze_coverage(
        vin="VIN1",
        expected_by_section=EXPECTED,
        labels=LABELS,
        seen=[],
        monitoring_since=None,
        now=NOW,
    )
    assert report.monitoring_since is None
    assert report.monitoring_days == 0.0
    assert not report.past_grace
    assert report.missing  # still reported as missing...
    assert report.overdue == []  # ...but never overdue without a clock


def test_empty_selection_is_trivially_complete():
    report = analyze_coverage(
        vin="VIN1",
        expected_by_section={},
        labels=LABELS,
        seen=[],
        monitoring_since=NOW - timedelta(days=99),
        now=NOW,
    )
    assert report.expected == 0
    assert report.coverage_percent == 100.0
    assert report.complete
    assert not report.has_gaps


def test_grace_boundary_is_inclusive():
    # Exactly grace_days old counts as past grace.
    report = _analyze(["desc.soc"], since=NOW - timedelta(days=DEFAULT_GRACE_DAYS))
    assert report.past_grace
    assert report.overdue


def test_to_dict_is_serialisable_and_shaped():
    report = _analyze(["desc.soc"], since=NOW - timedelta(days=10))
    data = report.to_dict()
    assert data["vin"] == "VIN1"
    assert data["expected"] == 5
    assert data["seen"] == 1
    assert data["coverage_percent"] == 20.0
    assert data["past_grace"] is True
    assert isinstance(data["clusters"], list)
    assert data["clusters"][0]["section"] == "electric"
    assert data["monitoring_days"] == 10.0
    # Round-trips through JSON without custom encoders.
    import json

    assert json.loads(json.dumps(data)) == data


def test_seen_accepts_any_collection():
    # A set is the natural call shape from the store's union; a list also works.
    as_set = _analyze({"desc.soc", "desc.range"}, since=NOW - timedelta(days=10))
    as_list = _analyze(["desc.soc", "desc.range"], since=NOW - timedelta(days=10))
    assert as_set.to_dict() == as_list.to_dict()
