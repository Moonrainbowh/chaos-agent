from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .completion_contract import CompletionAssessment, CompletionKind, TaskIntent
from .task import TaskStatus


class VerifierOutcome(str, Enum):
    NOT_RUN = "not_run"
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    INTERRUPTED = "interrupted"


class VerificationAction(str, Enum):
    COMPLETE = "complete"
    VERIFY = "verify"
    REPAIR = "repair"
    WAIT = "wait"
    REASSESS = "reassess"


@dataclass(frozen=True)
class VerificationTransition:
    status: TaskStatus
    action: VerificationAction
    consume_repair_cycle: bool = False


def decide_verification_transition(intent: TaskIntent, assessment: CompletionAssessment, outcome: VerifierOutcome) -> VerificationTransition:
    if not isinstance(intent, TaskIntent) or not isinstance(assessment, CompletionAssessment) or not isinstance(outcome, VerifierOutcome):
        raise TypeError("verification transition inputs must be typed")
    if assessment.kind is CompletionKind.VERIFIED:
        return VerificationTransition(TaskStatus.COMPLETED, VerificationAction.COMPLETE)
    if outcome is VerifierOutcome.INTERRUPTED:
        return VerificationTransition(TaskStatus.VERIFYING, VerificationAction.REASSESS)
    if outcome is VerifierOutcome.UNAVAILABLE:
        return VerificationTransition(TaskStatus.WAITING_DECISION, VerificationAction.WAIT)
    if outcome is VerifierOutcome.FAIL:
        if intent is not TaskIntent.MODIFY:
            return VerificationTransition(TaskStatus.COMPLETED, VerificationAction.COMPLETE)
        return VerificationTransition(TaskStatus.RUNNING, VerificationAction.REPAIR, True)
    if intent is TaskIntent.MODIFY:
        return VerificationTransition(TaskStatus.VERIFYING, VerificationAction.VERIFY)
    return VerificationTransition(TaskStatus.COMPLETED, VerificationAction.COMPLETE)
