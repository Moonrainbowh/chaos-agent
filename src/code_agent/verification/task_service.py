from __future__ import annotations

import hashlib
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
    TaskIntent,
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
from .planner import RiskTier, VerificationPhase, VerificationPlan, VerificationPlanner, check_syntax


_PROJECT_TESTS_CRITERION = "project-tests"
_INTEGRITY_CRITERION = "integrity"


class LedgerTaskVerificationService:
    """Bind typed verifier outcomes to a guarded current workspace subject."""

    def __init__(
        self,
        workspace_root: Path,
        sessions: object,
        planner: VerificationPlanner | None = None,
    ) -> None:
        self._guard = WorkspacePathGuard(workspace_root)
        self._sessions = sessions
        self._planner = planner or VerificationPlanner(self._guard.root)
        self._active_changes: dict[str, set[str]] = {}

    @property
    def planner(self) -> VerificationPlanner:
        return self._planner

    def set_semantic_graph(self, graph: object) -> None:
        if hasattr(self._planner, "set_semantic_graph"):
            self._planner.set_semantic_graph(graph)

    def begin_logical_change(self, task_id: str) -> None:
        """Open a logical change transaction for a task. Edits are batched until commit."""
        self._active_changes.setdefault(task_id, set())

    def in_logical_change(self, task_id: str) -> bool:
        """Check whether a logical change transaction is active for the task."""
        return task_id in self._active_changes

    def get_pending_changes(self, task_id: str) -> tuple[str, ...]:
        """Return the current set of pending file changes in the active transaction."""
        return tuple(sorted(self._active_changes.get(task_id, set())))

    def rollback_logical_change(self, task_id: str) -> None:
        """Discard pending changes in the current logical transaction."""
        self._active_changes.pop(task_id, None)

    async def commit_logical_change(
        self,
        task: TaskRecord,
        state: TaskState,
        phase: VerificationPhase = VerificationPhase.LOCAL_MILESTONE,
    ) -> tuple[TaskState, VerificationPlan | None]:
        """Commit pending edits in a logical change transaction:
        monotonically advances generation once, snapshots subject, and plans verification."""
        pending = self._active_changes.pop(task.id, set())
        if not pending:
            return state, None
        new_generation = state.code_generation + 1
        state = await self._snapshot(task, state, new_generation)
        plan = self._planner.plan(tuple(sorted(pending)), phase=phase)
        return state, plan

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
        is_edit = request.name in {"write_file", "replace_text"} and not result.is_error
        edit_changed = (
            request.name == "apply_workspace_edit_plan_v1"
            and (result.output.get("workspace_may_have_changed") is True if isinstance(result.output, Mapping) else False)
        )
        is_attempted_cmd = request.name in {"run_command", "run_process_v1"} and (
            result.metadata.get("execution_attempted") is True
        )

        if is_edit or edit_changed:
            changed_paths: list[str] = []
            if request.name in {"write_file", "replace_text"}:
                arg_path = request.arguments.get("path") if isinstance(request.arguments, Mapping) else None
                if isinstance(arg_path, str) and arg_path:
                    changed_paths.append(arg_path)
            elif edit_changed and isinstance(result.output, Mapping):
                for p in result.output.get("paths", ()):
                    if isinstance(p, str) and p:
                        changed_paths.append(p)

            # Phase 1: In-Flight L0 Fast Syntax Check (< 5ms)
            for path in changed_paths:
                if path.endswith((".py", ".json", ".toml")):
                    check_syntax(path, workspace_root=self._guard.root)

            if self.in_logical_change(task.id):
                self._active_changes[task.id].update(changed_paths)
                return state
            return await self._snapshot(task, state, state.code_generation + 1)

        if is_attempted_cmd:
            if self.in_logical_change(task.id) and self._active_changes.get(task.id):
                state, _ = await self.commit_logical_change(task, state)
                return state
            return await self._snapshot(task, state, state.code_generation + 1)

        if request.name != "run_verification":
            return state

        if self.in_logical_change(task.id) and self._active_changes.get(task.id):
            state, _ = await self.commit_logical_change(task, state)

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
        if task.contract.intent is not TaskIntent.MODIFY and not state.files_changed:
            return None
        current = tuple(
            item for item in await self._sessions.list_verification_evidence(task.id)
            if isinstance(item, EvidenceRecord)
            and item.criterion_id == _PROJECT_TESTS_CRITERION
            and item.generation == state.code_generation
            and item.subject_hash == state.subject_hash
        )
        if current:
            return None

        # Check with VerificationPlanner for changed files
        if state.files_changed:
            plan = self._planner.plan(state.files_changed, phase=VerificationPhase.FINAL_GATE)
            if plan.skip_tests:
                return None

        for project in discover_projects(self._guard.root, self._guard):
            kind = _test_kind(project.kind, project.recipes)
            if kind is not None:
                targets: list[str] = []
                if state.files_changed:
                    plan = self._planner.plan(state.files_changed, phase=VerificationPhase.FINAL_GATE)
                    if plan.targeted_tests and not plan.require_full_gate:
                        targets = list(plan.targeted_tests[:1]) if kind == "python_unittest" else list(plan.targeted_tests)

                args: dict[str, object] = {"kind": kind, "cwd": project.root}
                if targets:
                    args["targets"] = targets
                return ToolCall(
                    f"system-verify-{uuid.uuid4().hex}",
                    "run_verification",
                    args,
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
