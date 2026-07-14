from __future__ import annotations

import unittest

from code_agent.core.completion_contract import CompletionAssessment, CompletionKind, TaskIntent
from code_agent.core.task import TaskStatus
from code_agent.core.verification_state import VerificationAction, VerifierOutcome, decide_verification_transition


class VerificationStateTests(unittest.TestCase):
    def assessment(self, kind: CompletionKind) -> CompletionAssessment:
        return CompletionAssessment(kind, () if kind is CompletionKind.VERIFIED else ("tests",))

    def test_modify_without_evidence_enters_verifying(self) -> None:
        transition = decide_verification_transition(TaskIntent.MODIFY, self.assessment(CompletionKind.UNVERIFIED), VerifierOutcome.NOT_RUN)
        self.assertEqual((transition.status, transition.action), (TaskStatus.VERIFYING, VerificationAction.VERIFY))

    def test_failure_repairs_unavailable_waits_and_interrupted_reassesses(self) -> None:
        assessment = self.assessment(CompletionKind.UNVERIFIED)
        self.assertTrue(decide_verification_transition(TaskIntent.MODIFY, assessment, VerifierOutcome.FAIL).consume_repair_cycle)
        self.assertEqual(decide_verification_transition(TaskIntent.MODIFY, assessment, VerifierOutcome.UNAVAILABLE).status, TaskStatus.WAITING_DECISION)
        self.assertEqual(decide_verification_transition(TaskIntent.MODIFY, assessment, VerifierOutcome.INTERRUPTED).action, VerificationAction.REASSESS)

    def test_only_verified_assessment_completes(self) -> None:
        transition = decide_verification_transition(TaskIntent.MODIFY, self.assessment(CompletionKind.VERIFIED), VerifierOutcome.PASS)
        self.assertEqual((transition.status, transition.action), (TaskStatus.COMPLETED, VerificationAction.COMPLETE))
