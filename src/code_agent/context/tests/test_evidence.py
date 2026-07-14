from __future__ import annotations

import unittest

from code_agent.context.evidence import render_evidence_summary
from code_agent.core.completion_contract import AcceptanceCriterion, CriterionRequirement, CriterionStrength, TaskContractRevision, TaskIntent
from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord


class EvidenceContextTests(unittest.TestCase):
    def test_only_current_subject_evidence_is_rendered_as_valid(self) -> None:
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),))
        stale = EvidenceRecord.from_output("old", "tests", EvidenceOutcome.PASS, EvidenceProvenance.SYSTEM_VERIFIER, 1, "old-subject", "ok", "passed")
        current = EvidenceRecord.from_output("new", "tests", EvidenceOutcome.FAIL, EvidenceProvenance.SYSTEM_VERIFIER, 2, "subject", "bad", "failed")
        summary = render_evidence_summary(contract, 2, "subject", (stale, current), 100)
        self.assertIn("tests=fail", summary)
        self.assertNotIn("tests=pass", summary)

    def test_unmet_condition_survives_tight_budget(self) -> None:
        contract = TaskContractRevision(1, TaskIntent.MODIFY, (AcceptanceCriterion("tests", "tests pass", CriterionRequirement.REQUIRED, CriterionStrength.USER),))
        self.assertIn("unmet: tests", render_evidence_summary(contract, 1, "subject", (), 20))
