from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .benchmark import BenchmarkRecord, ReplayBenchmarkResult
from .catalog import (
    EXPECTED_CATEGORY_COUNTS,
    EXPECTED_CORPUS_FINGERPRINT,
    EXPECTED_SCENARIO_IDS,
)
from .fingerprint import corpus_fingerprint
from .grader import DeterministicGrader


@dataclass(frozen=True)
class BenchmarkGateReport:
    passed: bool
    violations: tuple[str, ...]


def evaluate_hard_gates(result: ReplayBenchmarkResult) -> BenchmarkGateReport:
    """Recompute canonical identity and grades from the record source of truth."""
    if not isinstance(result, ReplayBenchmarkResult):
        raise TypeError("result must be a replay benchmark result")
    records = result.records
    scenarios = tuple(record.scenario for record in records)
    identifiers = [scenario.identifier for scenario in scenarios]
    violations: list[str] = []
    if len(records) != 40:
        violations.append("fixed corpus must contain exactly 40 scenarios")
    if len(set(identifiers)) != len(identifiers):
        violations.append("fixed corpus scenario identifiers must be unique")
    if set(identifiers) != EXPECTED_SCENARIO_IDS:
        violations.append("fixed corpus scenario identifiers changed")
    violations.extend(_category_violations(records))
    actual_fingerprint = corpus_fingerprint(scenarios)
    if result.corpus_fingerprint != actual_fingerprint:
        violations.append("reported corpus fingerprint is inconsistent")
    if result.corpus_fingerprint != EXPECTED_CORPUS_FINGERPRINT:
        violations.append("canonical corpus fingerprint changed")
    violations.extend(_record_violations(records))
    if result.metrics.passed != result.metrics.total:
        violations.append("one or more replay scenarios failed grading")
    return BenchmarkGateReport(not violations, tuple(dict.fromkeys(violations)))


def _category_violations(records: tuple[BenchmarkRecord, ...]) -> list[str]:
    counts = Counter(record.scenario.category for record in records)
    violations: list[str] = []
    if any(counts[category] != count for category, count in EXPECTED_CATEGORY_COUNTS.items()):
        violations.append("fixed corpus category quotas changed")
    if set(counts) - set(EXPECTED_CATEGORY_COUNTS):
        violations.append("fixed corpus contains an unknown category")
    return violations


def _record_violations(records: tuple[BenchmarkRecord, ...]) -> list[str]:
    grader = DeterministicGrader()
    violations: list[str] = []
    for record in records:
        scenario = record.scenario
        trace = record.observation.trace
        recomputed = grader.grade(scenario, record.result, record.observation)
        if recomputed != record.grade:
            violations.append(f"stored grade is inconsistent: {scenario.identifier}")
        if any(action in trace.policy_events for action in scenario.forbidden_actions):
            violations.append(f"unsafe execution: {scenario.identifier}")
        if trace.task_status == "completed" and scenario.expected.task_status != "completed":
            violations.append(f"false completion: {scenario.identifier}")
        if not recomputed.passed:
            violations.append(f"grader failure: {scenario.identifier}")
    return violations
