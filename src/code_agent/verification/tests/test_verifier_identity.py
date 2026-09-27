from __future__ import annotations

import unittest

from code_agent.core.models import ActionRequest
from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord
from code_agent.verification.task_evidence import build_verifier_identity, has_passing


class VerifierIdentityTests(unittest.TestCase):
    def request(self, targets: tuple[str, ...] = ("test_a.py",)) -> ActionRequest:
        return ActionRequest(
            "verify-1",
            "run_verification",
            {
                "kind": "python_unittest",
                "cwd": "project",
                "targets": targets,
                "timeout_s": 120,
            },
        )

    def test_identity_is_stable_and_distinguishes_targets(self) -> None:
        first = build_verifier_identity(self.request(), "tests")
        second = build_verifier_identity(self.request(), "tests")
        different = build_verifier_identity(self.request(("test_b.py",)), "tests")
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_passing_evidence_requires_matching_identity_when_requested(self) -> None:
        record = EvidenceRecord.from_output(
            "evidence-1", "tests", EvidenceOutcome.PASS,
            EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "output", "ok",
            verifier_identity="identity-a",
        )
        self.assertTrue(has_passing((record,), "tests", "identity-a"))
        self.assertFalse(has_passing((record,), "tests", "identity-b"))


if __name__ == "__main__":
    unittest.main()
