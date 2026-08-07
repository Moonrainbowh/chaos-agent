from __future__ import annotations

from pathlib import Path

from code_agent.evaluation.models import LifecycleEvent, Scenario, ScenarioResult
from code_agent.evaluation.trace import TrustedTraceRecorder
from code_agent.evaluation.verifier import CommandOutcome


def apply_hidden_golden(workspace: Path, scenario: Scenario) -> None:
    for relative_path, content in scenario.golden_files.items():
        path = workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative_path in scenario.golden_absent_files:
        path = workspace / relative_path
        if path.exists():
            path.unlink()


def successful_result(
    scenario: Scenario,
    recorder: TrustedTraceRecorder | None = None,
) -> ScenarioResult:
    oracle = scenario.expected.lifecycle
    generation = int(bool(scenario.expected.workspace.required_changes))
    events: list[LifecycleEvent] = []
    parent: str | None = None
    for index, kind in enumerate(oracle.required_events):
        event_id = f"event-{index}"
        events.append(
            LifecycleEvent(
                event_id,
                kind,
                generation,
                effect_id=f"effect-{index}",
                parent_event_id=parent,
            )
        )
        parent = event_id
    result = ScenarioResult(
        scenario.expected.task_status,
        policy_events=oracle.required_policy_events,
        model_turns=2,
        tool_calls=4,
        evidence_generation=generation,
        lifecycle_events=tuple(events),
    )
    if recorder is not None:
        recorder.record_status(scenario.expected.task_status)
        for policy_event in oracle.required_policy_events:
            recorder.record_policy(policy_event)
        recorder.record_budget(model_turns=2, tool_calls=4)
        recorder.record_evidence_generation(generation)
        for event in events:
            recorder.record_lifecycle(event)
        recorder.seal()
    return result


async def deterministic_verifier(
    _workspace: Path,
    _oracle: object,
    phase: str,
) -> CommandOutcome:
    return CommandOutcome(1 if phase == "baseline" else 0)
