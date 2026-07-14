from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from code_agent.core.completion_contract import (
    AcceptanceCriterion,
    CompletionCandidate,
    CriterionRequirement,
    CriterionStrength,
    TaskContractRevision,
    assess_completion,
)
from code_agent.core.models import ActionRequest, ActionResult, ToolCall
from code_agent.core.task import TaskRecord
from code_agent.core.task_state import TaskState
from code_agent.core.task_verification import VerificationAssessment
from code_agent.core.verification_state import VerifierOutcome
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.subject import snapshot_subject
from code_agent.projects.discovery import ProjectKind, discover_projects

from .evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord, evidence_satisfies_required


_PROJECT_TESTS_CRITERION = "project-tests"
_INTEGRITY_CRITERION = "integrity"


class LedgerTaskVerificationService:
    """Bind typed verifier outcomes to a guarded current workspace subject."""

    def __init__(self, workspace_root: Path, sessions: object) -> None:
        self._guard = WorkspacePathGuard(workspace_root)
        self._sessions = sessions

    async def prepare(self, task: TaskRecord, state: TaskState) -> TaskState:
        contract = await self._sessions.load_task_contract_revision(task.id)
        if contract is None:
            contract = TaskContractRevision(
                1,
                task.contract.intent,
                (
                    AcceptanceCriterion(
                        _PROJECT_TESTS_CRITERION,
                        "A trusted project test passes for the current workspace subject.",
                        CriterionRequirement.REQUIRED,
                        CriterionStrength.INTEGRITY,
                    ),
                ),
            )
            await self._sessions.save_task_contract_revision(task.id, contract)
        if state.subject_hash:
            return state
        return await self._snapshot(task, state, state.code_generation)

    async def record_action(
        self, task: TaskRecord, request: ActionRequest, result: ActionResult, state: TaskState
    ) -> TaskState:
        if request.name in {"write_file", "replace_text"} and not result.is_error:
            return await self._snapshot(task, state, state.code_generation + 1)
        if request.name != "run_verification":
            return state
        state = await self._snapshot(task, state, state.code_generation)
        run_id = uuid.uuid4().hex
        await self._sessions.begin_verification_run(
            run_id, task.id, state.code_generation, state.subject_hash
        )
        evidence = _evidence_from_result(
            run_id, result, state.code_generation, state.subject_hash
        )
        await self._sessions.append_verification_evidence(run_id, task.id, evidence)
        await self._sessions.close_verification_run(run_id, "completed")
        return state

    async def assess(self, task: TaskRecord, state: TaskState) -> VerificationAssessment:
        state = await self._snapshot(task, state, state.code_generation)
        contract = await self._sessions.load_task_contract_revision(task.id)
        if not isinstance(contract, TaskContractRevision):
            raise RuntimeError("task contract revision is unavailable")
        evidence = tuple(await self._sessions.list_verification_evidence(task.id))
        current = tuple(
            item for item in evidence
            if isinstance(item, EvidenceRecord)
            and item.generation == state.code_generation
            and item.subject_hash == state.subject_hash
        )
        candidates = tuple(
            CompletionCandidate(item.criterion_id, evidence_satisfies_required(item), item.generation, item.subject_hash)
            for item in current
        )
        assessment = assess_completion(contract, state.code_generation, state.subject_hash, candidates)
        outcome = _outcome(current)
        run_id = await self._sessions.latest_completed_verification_run(
            task.id, state.code_generation, state.subject_hash
        )
        return VerificationAssessment(
            assessment, outcome, state.code_generation, state.subject_hash, run_id
        )

    async def suggest_verification(self, task: TaskRecord, state: TaskState) -> ToolCall | None:
        """Choose one available project test recipe only when no current test exists."""
        current = tuple(
            item for item in await self._sessions.list_verification_evidence(task.id)
            if isinstance(item, EvidenceRecord)
            and item.criterion_id == _PROJECT_TESTS_CRITERION
            and item.generation == state.code_generation
            and item.subject_hash == state.subject_hash
        )
        if current:
            return None
        for project in discover_projects(self._guard.root, self._guard):
            kind = _test_kind(project.kind, project.recipes)
            if kind is not None:
                return ToolCall(
                    f"system-verify-{uuid.uuid4().hex}",
                    "run_verification",
                    {"kind": kind, "cwd": project.root},
                )
        return None

    async def finalize(self, task: TaskRecord, assessment: VerificationAssessment) -> TaskRecord:
        if assessment.verification_run_id is None:
            raise RuntimeError("current verification run is unavailable")
        contract = await self._sessions.load_task_contract_revision(task.id)
        if not isinstance(contract, TaskContractRevision):
            raise RuntimeError("task contract revision is unavailable")
        return await self._sessions.finalize_task(
            task.id,
            assessment.verification_run_id,
            contract,
            assessment.generation,
            assessment.subject_hash,
        )

    async def _snapshot(self, task: TaskRecord, state: TaskState, generation: int) -> TaskState:
        snapshot = snapshot_subject(self._guard, generation, state.files_changed)
        updated = replace(state, code_generation=generation, subject_hash=snapshot.subject_hash)
        await self._sessions.save_task_state(task.thread_id, updated)
        return updated


def _evidence_from_result(
    identifier: str, result: ActionResult, generation: int, subject_hash: str
) -> EvidenceRecord:
    output = result.output if isinstance(result.output, Mapping) else {}
    diagnostic = str(output.get("stderr") or output.get("detail") or output.get("error") or "verification passed")
    if not result.is_error:
        outcome = EvidenceOutcome.PASS
    elif output.get("error") == "verification unavailable":
        outcome = EvidenceOutcome.UNAVAILABLE
    else:
        outcome = EvidenceOutcome.FAIL
    return EvidenceRecord.from_output(
        identifier,
            _criterion_for(result),
        outcome,
        EvidenceProvenance.SYSTEM_VERIFIER,
        generation,
        subject_hash,
        repr(dict(output)),
        diagnostic,
    )


def _outcome(records: Sequence[EvidenceRecord]) -> VerifierOutcome:
    if any(item.outcome is EvidenceOutcome.UNAVAILABLE for item in records):
        return VerifierOutcome.UNAVAILABLE
    if any(item.outcome in {EvidenceOutcome.FAIL, EvidenceOutcome.ERROR, EvidenceOutcome.UNSTABLE} for item in records):
        return VerifierOutcome.FAIL
    if any(evidence_satisfies_required(item) for item in records):
        return VerifierOutcome.PASS
    return VerifierOutcome.NOT_RUN


def _criterion_for(result: ActionResult) -> str:
    """Only project tests can satisfy the default behavior-completion criterion."""
    kind = result.output.get("kind") if isinstance(result.output, Mapping) else None
    return _PROJECT_TESTS_CRITERION if kind in {"python_unittest", "pytest", "node_test", "dotnet_test"} else _INTEGRITY_CRITERION


def _test_kind(kind: ProjectKind, recipes: Sequence[object]) -> str | None:
    available = {getattr(recipe, "label", None) for recipe in recipes if getattr(recipe, "available", False)}
    if kind is ProjectKind.PYTHON and "python unittest" in available:
        return "python_unittest"
    if kind is ProjectKind.NODE and "npm test" in available:
        return "node_test"
    if kind is ProjectKind.DOTNET and "dotnet test" in available:
        return "dotnet_test"
    return None
