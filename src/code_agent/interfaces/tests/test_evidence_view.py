from __future__ import annotations

import unittest

from code_agent.interfaces.evidence_view import format_evidence_summary
from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord


class EvidenceViewTests(unittest.TestCase):
    def test_renders_evidence_without_claiming_completion(self) -> None:
        record = EvidenceRecord.from_output("run", "tests", EvidenceOutcome.FAIL, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "bad", "failed")
        self.assertEqual(format_evidence_summary((record,)), "tests: fail (system_verifier)")
        self.assertEqual(format_evidence_summary(()), "未验证")
