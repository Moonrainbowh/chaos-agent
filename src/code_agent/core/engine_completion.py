from __future__ import annotations

from .completion_contract import CompletionAssessment, CompletionKind, TaskIntent
from .events import AgentEvent, EventKind
from .limits import TaskBudget, usage_payload
from .models import Usage
from .task import TaskRecord, TaskStatus
from .verification_state import VerifierOutcome, decide_verification_transition


class AgentEngineCompletionMixin:
    """Resolve a model completion through the persistent verification gate."""

    async def _resolve_task_completion(self, task: TaskRecord, thread_id: str, result_metadata=None) -> TaskRecord:
        if task.contract.intent is TaskIntent.MODIFY:
            state = await self._journal.load_task_state(thread_id)
            if not state.files_changed:
                if result_metadata is not None:
                    result_metadata.update(verification='unverified', remaining=(
                        'workspace change or explicit acceptance of no-change delivery',))
                return await self._journal.transition_task(
                    task.id,
                    TaskStatus.WAITING_DECISION,
                    "Requested modification produced no workspace file changes. "
                    "Continue with implementation, or explain why no change is required "
                    "and request explicit acceptance of the unchanged partial delivery.",
                )
            if not getattr(self, "_require_verification", True):
                if result_metadata is not None:
                    result_metadata.update(verification='unverified')
                return await self._journal.transition_task(
                    task.id, TaskStatus.COMPLETED,
                    "task completed without structured verification",
                )
        if self._verification is None:
            assessment = CompletionAssessment(CompletionKind.UNVERIFIED, ("verification evidence",))
            outcome = VerifierOutcome.NOT_RUN
            verification_assessment = None
        else:
            verification_assessment = await self._verification.assess(
                task, await self._journal.load_task_state(thread_id)
            )
            assessment = verification_assessment.assessment
            outcome = verification_assessment.outcome
        transition = decide_verification_transition(task.contract.intent, assessment, outcome)
        diagnostics = getattr(verification_assessment, "diagnostics", ())
        detail = "; ".join(diagnostics)
        explanation = "Verification " + outcome.value + (": " + detail if detail else "")
        if result_metadata is not None:
            result_metadata.update(verification=assessment.kind.value, remaining=assessment.unmet_required)
        if transition.action.value == "complete":
            if (
                verification_assessment is not None
                and verification_assessment.verification_run_id is not None
            ):
                await self._journal.transition_task(
                    task.id, TaskStatus.VERIFYING, "verifying current evidence"
                )
                return await self._verification.finalize(task, verification_assessment)
            return await self._journal.transition_task(
                task.id, TaskStatus.COMPLETED, "task completed"
            )
        # This is the final assessment after all suggested verifiers settled.
        # A VERIFY/REASSESS decision cannot describe work still running here.
        if transition.status is TaskStatus.VERIFYING:
            missing = ", ".join(assessment.unmet_required) or "current verification evidence"
            return await self._journal.transition_task(
                task.id, TaskStatus.WAITING_DECISION,
                ("Verification did not complete. Missing: " + missing
                 + (". " + explanation if detail else ""))[:1024],
            )
        if transition.status is task.status and not detail:
            return task
        return await self._journal.transition_task(
            task.id, transition.status,
            (explanation if detail else "verification evidence is required")[:1024]
        )

    @staticmethod
    def _completed_event(thread_id: str, budget: TaskBudget, usage: Usage) -> AgentEvent:
        return AgentEvent(
            EventKind.COMPLETED,
            {
                "thread_id": thread_id,
                "turns": budget.model_turns,
                "tool_calls": budget.tool_calls,
                "usage": usage_payload(usage),
            },
        )

    def _task_completion_events(
        self, thread_id: str, task: TaskRecord, budget: TaskBudget, usage: Usage
    ) -> tuple[AgentEvent, ...]:
        status = AgentEvent(
            EventKind.TASK_STATUS_CHANGED,
            {"task_id": task.id, "status": task.status.value, "reason": task.stop_reason},
        )
        if task.status is TaskStatus.COMPLETED:
            return (status, self._completed_event(thread_id, budget, usage))
        if task.status is TaskStatus.WAITING_DECISION:
            return (status, AgentEvent(EventKind.TASK_DECISION_REQUIRED, status.payload))
        return (status,)
