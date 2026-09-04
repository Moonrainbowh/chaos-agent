from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from code_agent.core.completion_contract import (
    CompletionCandidate,
    TaskContractRevision,
    TaskIntent,
    assess_completion,
)
from code_agent.core.models import ActionRequest, ActionResult, ToolCall
from code_agent.core.task import TaskRecord
from code_agent.core.task_state import TaskState
from code_agent.core.task_verification import (
    InFlightValidationError,
    VerificationAssessment,
)
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.subject import snapshot_subject
from code_agent.projects.discovery import discover_projects

from .evidence import EvidenceRecord, evidence_satisfies_required
from .planner import RiskTier, VerificationPhase, VerificationPlan, VerificationPlanner, check_syntax
from .task_evidence import (
    FINAL_TESTS_CRITERION,
    INTEGRITY_CRITERION,
    RISK_VALIDATION_CRITERION,
    build_kind,
    evidence_from_result,
    has_passing,
    is_legacy_default_contract,
    planner_attestation_allowed,
    record_planner_attestation,
    test_kind,
    validation_contract,
    verifier_outcome,
)
from .task_plans import PlannedCallRegistry


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
        self._syntax_failures: dict[str, tuple[str, ...]] = {}
        self._planned_calls = PlannedCallRegistry()

    @property
    def planner(self) -> VerificationPlanner:
        return self._planner

    def set_semantic_graph(self, graph: object) -> None:
        self._planner.set_semantic_graph(graph)

    def set_semantic_snapshot(self, snapshot: object) -> None:
        self._planner.set_semantic_snapshot(snapshot)

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
        """Abandon pending validation metadata; workspace rollback is separate."""
        self._active_changes.pop(task_id, None)
        self._syntax_failures.pop(task_id, None)

    async def commit_logical_change(
        self,
        task: TaskRecord,
        state: TaskState,
        phase: VerificationPhase = VerificationPhase.LOCAL_MILESTONE,
    ) -> tuple[TaskState, VerificationPlan | None]:
        """Commit pending edits in a logical change transaction:
        monotonically advances generation once, snapshots subject, and plans verification."""
        pending = self._active_changes.pop(task.id, set())
        failures = self._syntax_failures.pop(task.id, ())
        if not pending:
            return state, None
        new_generation = state.code_generation + 1
        state = await self._snapshot(task, state, new_generation)
        plan = self._planner.plan(tuple(sorted(pending)), phase=phase)
        if failures:
            return state, None
        if plan.tier is RiskTier.LOW and planner_attestation_allowed(
            plan.changed_files
        ):
            await record_planner_attestation(
                self._sessions, task.id, state, plan
            )
        return state, plan

    async def prepare(self, task: TaskRecord, state: TaskState) -> TaskState:
        contract = await self._sessions.load_task_contract_revision(task.id)
        if contract is None:
            contract = validation_contract(1, task.contract.intent)
            await self._sessions.save_task_contract_revision(task.id, contract)
        elif isinstance(contract, TaskContractRevision) and is_legacy_default_contract(
            contract
        ):
            contract = validation_contract(contract.revision + 1, contract.intent)
            await self._sessions.save_task_contract_revision(task.id, contract)
        if state.subject_hash:
            return state
        return await self._snapshot(task, state, state.code_generation)

    async def record_action(
        self, task: TaskRecord, request: ActionRequest, result: ActionResult, state: TaskState
    ) -> TaskState:
        changed_paths = _changed_paths(request, result)
        if changed_paths is not None:
            return await self._record_edit(task, state, changed_paths)
        is_attempted_cmd = request.name in {"run_command", "run_process_v1"} and (
            result.metadata.get("execution_attempted") is True
        )
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
        criterion = self._planned_calls.consume_criterion(task.id, request.id)
        kind = request.arguments.get("kind")
        targets = request.arguments.get("targets", ())
        if (
            criterion == INTEGRITY_CRITERION
            and kind in {"python_unittest", "pytest", "node_test", "dotnet_test"}
            and not targets
        ):
            criterion = RISK_VALIDATION_CRITERION
        evidence = evidence_from_result(
            run_id,
            result,
            state.code_generation,
            state.subject_hash,
            criterion,
        )
        await self._sessions.append_verification_evidence(run_id, task.id, evidence)
        await self._sessions.close_verification_run(run_id, "completed")
        return state

    async def _record_edit(
        self, task: TaskRecord, state: TaskState, changed_paths: tuple[str, ...]
    ) -> TaskState:
        if self.in_logical_change(task.id):
            self._active_changes[task.id].update(changed_paths)
            updated = state
        else:
            updated = await self._snapshot(
                task, state, state.code_generation + 1
            )
        failures = tuple(
            result.format_diagnostic()
            for result in (
                check_syntax(path, workspace_root=self._guard.root)
                for path in changed_paths
                if path.endswith((".py", ".json", ".toml"))
            )
            if not result.is_valid
        )
        if failures:
            self._syntax_failures[task.id] = failures
            raise InFlightValidationError(updated, "; ".join(failures))
        return updated

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
        outcome = verifier_outcome(current)
        run_id = await self._sessions.latest_completed_verification_run(
            task.id, state.code_generation, state.subject_hash
        )
        return VerificationAssessment(
            assessment, outcome, state.code_generation, state.subject_hash, run_id
        )

    async def suggest_verification(self, task: TaskRecord, state: TaskState) -> ToolCall | None:
        """Choose the next Host-planned final-gate step for the current subject."""
        if task.contract.intent is not TaskIntent.MODIFY and not state.files_changed:
            return None
        current = tuple(
            item for item in await self._sessions.list_verification_evidence(task.id)
            if isinstance(item, EvidenceRecord)
            and item.generation == state.code_generation
            and item.subject_hash == state.subject_hash
        )
        if has_passing(current, RISK_VALIDATION_CRITERION):
            return None
        plan = self._planner.plan(
            state.files_changed, phase=VerificationPhase.FINAL_GATE
        )
        if plan.tier is RiskTier.LOW and planner_attestation_allowed(
            state.files_changed
        ):
            await record_planner_attestation(
                self._sessions, task.id, state, plan
            )
            return None
        project = next(iter(discover_projects(self._guard.root, self._guard)), None)
        if project is None:
            return None
        if plan.tier is RiskTier.CRITICAL and has_passing(
            current, FINAL_TESTS_CRITERION
        ):
            return self._planned_calls.create(
                task.id,
                plan,
                build_kind(project.kind),
                project.root,
                (),
                "build",
            )
        kind = test_kind(project.kind, project.recipes)
        if kind is None:
            return None
        targets = plan.targeted_tests if not plan.require_full_gate else ()
        return self._planned_calls.create(
            task.id, plan, kind, project.root, targets, "tests"
        )

    def milestone_verification(
        self, task: TaskRecord, plan: VerificationPlan | None
    ) -> ToolCall | None:
        if plan is None or plan.skip_tests:
            return None
        project = next(iter(discover_projects(self._guard.root, self._guard)), None)
        if project is None:
            return None
        kind = test_kind(project.kind, project.recipes)
        if kind is None:
            return None
        return self._planned_calls.create(
            task.id,
            plan,
            kind,
            project.root,
            plan.targeted_tests,
            "tests",
        )

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


def _changed_paths(
    request: ActionRequest, result: ActionResult
) -> tuple[str, ...] | None:
    if request.name in {"write_file", "replace_text"} and not result.is_error:
        path = request.arguments.get("path")
        return (path,) if isinstance(path, str) and path else ()
    output = result.output
    if (
        request.name != "apply_workspace_edit_plan_v1"
        or not isinstance(output, Mapping)
        or output.get("workspace_may_have_changed") is not True
    ):
        return None
    return tuple(
        path for path in output.get("paths", ())
        if isinstance(path, str) and path
    )
