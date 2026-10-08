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
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        values = tuple(self.diagnostics)
        if len(values) > 8 or any(not isinstance(item, str) or not item.strip()
                                   or len(item) > 512 for item in values):
            raise ValueError("verification diagnostics must be bounded non-blank text")
        object.__setattr__(self, "diagnostics", values)


class InFlightValidationError(RuntimeError):
    """Report a failed post-write L0 check while preserving reduced task state."""

    def __init__(self, state: TaskState, diagnostic: str) -> None:
        if not isinstance(state, TaskState):
            raise TypeError("state must be a TaskState")
        if not isinstance(diagnostic, str) or not diagnostic.strip():
            raise ValueError("diagnostic must be non-blank text")
        super().__init__("in-flight validation failed")
        self.state = state
        self.diagnostic = diagnostic.strip()[:2_000]


class TaskVerificationService(Protocol):
    def begin_logical_change(self, task_id: str) -> None: ...

    async def commit_logical_change(
        self, task: TaskRecord, state: TaskState
    ) -> tuple[TaskState, ToolCall | None]: ...

    async def prepare(self, task: TaskRecord, state: TaskState) -> TaskState: ...

    async def record_action(
        self, task: TaskRecord, request: ActionRequest, result: ActionResult, state: TaskState
    ) -> TaskState: ...

    async def assess(self, task: TaskRecord, state: TaskState) -> VerificationAssessment: ...

    async def suggest_verification(self, task: TaskRecord, state: TaskState) -> ToolCall | None: ...

    async def finalize(self, task: TaskRecord, assessment: VerificationAssessment) -> TaskRecord: ...
