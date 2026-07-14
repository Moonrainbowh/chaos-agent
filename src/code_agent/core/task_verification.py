from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .completion_contract import CompletionAssessment
from .models import ActionRequest, ActionResult, ToolCall
from .task import TaskRecord
from .task_state import TaskState
from .verification_state import VerifierOutcome


@dataclass(frozen=True)
class VerificationAssessment:
    """The bounded completion decision produced outside the core loop."""

    assessment: CompletionAssessment
    outcome: VerifierOutcome
    generation: int
    subject_hash: str
    verification_run_id: str | None = None


class TaskVerificationService(Protocol):
    async def prepare(self, task: TaskRecord, state: TaskState) -> TaskState: ...

    async def record_action(
        self, task: TaskRecord, request: ActionRequest, result: ActionResult, state: TaskState
    ) -> TaskState: ...

    async def assess(self, task: TaskRecord, state: TaskState) -> VerificationAssessment: ...

    async def suggest_verification(self, task: TaskRecord, state: TaskState) -> ToolCall | None: ...

    async def finalize(self, task: TaskRecord, assessment: VerificationAssessment) -> TaskRecord: ...
