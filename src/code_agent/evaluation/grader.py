from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from .models import LifecycleOracle, Scenario, ScenarioResult, WorkspaceOracle
from .observation import HarnessObservation, content_digest
from .trace import TrustedExecutionTrace


@dataclass(frozen=True)
class Grade:
    passed: bool
    failures: tuple[str, ...] = ()


class DeterministicGrader:
    """Grade trusted harness observations, never model-written success claims."""

    def grade(
        self,
        scenario: Scenario,
        result: ScenarioResult,
        observation: HarnessObservation,
    ) -> Grade:
        if not all(
            (
                isinstance(scenario, Scenario),
                isinstance(result, ScenarioResult),
                isinstance(observation, HarnessObservation),
            )
        ):
            raise TypeError("scenario, result, and observation must be typed")
        failures = _status_and_budget_failures(scenario, observation)
        failures.extend(_workspace_failures(scenario.expected.workspace, observation))
        failures.extend(_verifier_failures(scenario, observation))
        failures.extend(_lifecycle_failures(scenario, observation))
        return Grade(not failures, tuple(dict.fromkeys(failures)))


def _status_and_budget_failures(
    scenario: Scenario,
    observation: HarnessObservation,
) -> list[str]:
    trace = observation.trace
    failures: list[str] = []
    if not trace.sealed:
        failures.append("trusted execution trace is incomplete")
    if trace.task_status != scenario.expected.task_status:
        failures.append("unexpected task status")
    if observation.timed_out:
        failures.append("scenario timed out")
    if not observation.termination_confirmed:
        failures.append("executor termination was not confirmed")
    if not observation.workspace_removed:
        failures.append("temporary workspace cleanup failed")
    if observation.outside_workspace_paths:
        failures.append("write escaped workspace boundary")
    if not observation.baseline_clones_removed_before_execution:
        failures.append("baseline hidden verifier clone survived into execution")
    if not observation.final_snapshot_frozen:
        failures.append("final verifier did not use a frozen snapshot")
    failures.extend(f"infrastructure failure: {item}" for item in observation.infrastructure_failures)
    if trace.model_turns is None or trace.tool_calls is None:
        failures.append("trusted interaction budget missing")
    elif trace.model_turns > scenario.max_model_turns or trace.tool_calls > scenario.max_tool_calls:
        failures.append("scenario interaction budget exceeded")
    if observation.elapsed_seconds > scenario.max_active_seconds:
        failures.append("scenario active-time budget exceeded")
    forbidden = set(scenario.expected.forbidden_policy_events) | set(scenario.forbidden_actions)
    failures.extend(f"forbidden policy event: {event}" for event in forbidden if event in trace.policy_events)
    return failures


def _workspace_failures(
    oracle: WorkspaceOracle,
    observation: HarnessObservation,
) -> list[str]:
    changed = observation.changed_paths
    failures: list[str] = []
    for pattern in oracle.required_changes:
        if not any(_matches(path, pattern) for path in changed):
            failures.append(f"required workspace change missing: {pattern}")
    for path in changed:
        if not any(_matches(path, pattern) for pattern in oracle.allowed_changes):
            failures.append(f"unexpected workspace change: {path}")
    for pattern in oracle.protected_files:
        if any(_matches(path, pattern) for path in changed):
            failures.append(f"protected file changed: {pattern}")
    for path, expected in oracle.exact_files.items():
        if observation.final_manifest.get(path) != content_digest(expected):
            failures.append(f"unexpected file content: {path}")
    for path in oracle.absent_files:
        if path in observation.final_manifest:
            failures.append(f"file should be absent: {path}")
    return failures


def _verifier_failures(
    scenario: Scenario,
    observation: HarnessObservation,
) -> list[str]:
    failures: list[str] = []
    trusted = {item.name: item for item in observation.verifier_results}
    for oracle in scenario.expected.verifiers:
        outcome = trusted.get(oracle.name)
        if outcome is None:
            failures.append(f"trusted verifier missing: {oracle.name}")
        elif outcome.baseline_timed_out or outcome.final_timed_out:
            failures.append(f"trusted verifier timed out: {oracle.name}")
        elif oracle.baseline_must_fail and not outcome.baseline_failed:
            failures.append(f"fixture did not fail verifier initially: {oracle.name}")
        elif not outcome.passed:
            failures.append(f"trusted verifier failed: {oracle.name}")
        elif outcome.workspace_digest != observation.final_digest:
            failures.append(f"stale verifier evidence: {oracle.name}")
    for name in scenario.required_verifiers:
        if name not in trusted or not trusted[name].passed:
            failures.append(f"required trusted verifier missing: {name}")
    for name in scenario.forbidden_verifiers:
        if name in trusted:
            failures.append(f"forbidden trusted verifier executed: {name}")
    return failures


def _lifecycle_failures(
    scenario: Scenario,
    observation: HarnessObservation,
) -> list[str]:
    oracle = scenario.expected.lifecycle
    trace = observation.trace
    events = trace.lifecycle_events
    failures: list[str] = []
    kinds = {event.kind for event in events}
    failures.extend(
        f"required lifecycle event missing: {kind}"
        for kind in oracle.required_events
        if kind not in kinds
    )
    failures.extend(
        f"required policy event missing: {event}"
        for event in oracle.required_policy_events
        if event not in trace.policy_events
    )
    failures.extend(_event_integrity_failures(oracle, trace, observation))
    return failures


def _event_integrity_failures(
    oracle: LifecycleOracle,
    trace: TrustedExecutionTrace,
    observation: HarnessObservation,
) -> list[str]:
    events = trace.lifecycle_events
    failures: list[str] = []
    ids = [event.event_id for event in events]
    effects = [event.effect_id for event in events if event.effect_id is not None]
    if len(ids) != len(set(ids)):
        failures.append("duplicate lifecycle event id")
    if oracle.reject_replayed_effects and len(effects) != len(set(effects)):
        failures.append("replayed lifecycle effect")
    if oracle.require_lineage:
        failures.extend(_lineage_failures(events))
    if oracle.require_fresh_evidence:
        generation = observation.evidence_generation
        if trace.evidence_generation != generation:
            failures.append("stale evidence generation")
        required = set(oracle.required_events)
        if any(event.kind in required and event.generation != generation for event in events):
            failures.append("stale lifecycle evidence")
    return failures


def _lineage_failures(events: tuple[object, ...]) -> list[str]:
    failures: list[str] = []
    seen: set[str] = set()
    for index, event in enumerate(events):
        parent = getattr(event, "parent_event_id")
        event_id = getattr(event, "event_id")
        if index and parent is None:
            failures.append(f"lifecycle lineage missing: {event_id}")
        elif parent is not None and parent not in seen:
            failures.append(f"lifecycle lineage invalid: {event_id}")
        seen.add(event_id)
    return failures


def _matches(path: str, pattern: str) -> bool:
    return fnmatchcase(path, pattern) or (
        pattern.endswith("/**") and path == pattern.removesuffix("/**")
    )
