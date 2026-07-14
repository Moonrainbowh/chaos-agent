from __future__ import annotations

import unittest

from code_agent.core.completion_contract import AcceptanceCriterion, CompletionCandidate, CompletionKind, CriterionRequirement, CriterionStrength, TaskContractRevision, TaskIntent, assess_completion


class CompletionContractTests(unittest.TestCase):
    def test_user_required_criterion_cannot_be_downgraded(self) -> None:
        criterion = AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER)
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (criterion,))
        with self.assertRaises(ValueError):
            contract.revise((AcceptanceCriterion("tests", "tests pass", CriterionRequirement.OPTIONAL, CriterionStrength.USER),))

    def test_integrity_is_always_required_and_revisions_are_monotonic(self) -> None:
        with self.assertRaises(ValueError):
            AcceptanceCriterion("subject", "hash matches", CriterionRequirement.OPTIONAL, CriterionStrength.INTEGRITY)
        contract = TaskContractRevision(1, TaskIntent.ANALYZE, (AcceptanceCriterion("read", "analysis complete", CriterionRequirement.REQUIRED, CriterionStrength.SELF_AUTHORED),))
        self.assertEqual(contract.revise(contract.criteria).revision, 2)

    def test_only_current_generation_and_subject_can_verify_completion(self) -> None:
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),))
        stale = CompletionCandidate("tests", True, 1, "old")
        self.assertEqual(assess_completion(contract, 2, "new", (stale,)).kind, CompletionKind.UNVERIFIED)
        current = CompletionCandidate("tests", True, 2, "new")
        self.assertEqual(assess_completion(contract, 2, "new", (current,)).kind, CompletionKind.VERIFIED)
