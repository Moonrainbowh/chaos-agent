from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import ScenarioResult
from .observation import TrustedVerifierResult


@dataclass(frozen=True)
class ExecutionOutcome:
    result: ScenarioResult
    elapsed: float
    timed_out: bool
    termination_confirmed: bool
    isolation_mode: str
    infrastructure_failure: str | None = None


@dataclass(frozen=True)
class RunData:
    result: ScenarioResult
    baseline: Mapping[str, str]
    final: Mapping[str, str]
    verifiers: tuple[TrustedVerifierResult, ...]
    elapsed: float
    timed_out: bool
    termination_confirmed: bool
    isolation_mode: str
    outside_paths: tuple[str, ...]
    baseline_removed: bool
    final_frozen: bool
    infrastructure_failures: tuple[str, ...]


def make_run_data(
    execution: ExecutionOutcome,
    baseline: Mapping[str, str],
    final: Mapping[str, str],
    verifiers: tuple[TrustedVerifierResult, ...],
    outside: tuple[str, ...],
    baseline_removed: bool,
    final_frozen: bool,
    infrastructure: tuple[str, ...],
) -> RunData:
    return RunData(
        execution.result,
        baseline,
        final,
        verifiers,
        execution.elapsed,
        execution.timed_out,
        execution.termination_confirmed,
        execution.isolation_mode,
        outside,
        baseline_removed,
        final_frozen,
        infrastructure,
    )
