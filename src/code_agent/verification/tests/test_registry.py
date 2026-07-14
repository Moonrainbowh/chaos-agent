from __future__ import annotations

import unittest

from code_agent.runtime.models import CommandResult, TerminationReason
from code_agent.verification.evidence import EvidenceOutcome
from code_agent.verification.models import VerificationCommand, VerificationKind, VerificationUnavailable
from code_agent.verification.registry import no_progress, normalize_verification_result


def result(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> CommandResult:
    return CommandResult(("python",), "python", returncode, TerminationReason.EXITED, stdout, stderr, 0, False, ".")


class RegistryTests(unittest.TestCase):
    def test_normalizes_pass_failure_and_unavailable(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        passed = normalize_verification_result("run-1", "tests", command, result(0), 2, "subject")
        failed = normalize_verification_result("run-2", "tests", command, result(1, stderr=b"failed C:\\Temp\\a"), 2, "subject")
        unavailable = normalize_verification_result("run-3", "tests", VerificationUnavailable(VerificationKind.PYTEST, "missing pytest"), None, 2, "subject")
        self.assertEqual(passed.evidence.outcome, EvidenceOutcome.PASS)
        self.assertIsNotNone(failed.repair)
        self.assertEqual(unavailable.evidence.outcome, EvidenceOutcome.UNAVAILABLE)
        self.assertIsNone(unavailable.repair)

    def test_same_subject_and_failure_is_no_progress(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        first = normalize_verification_result("run-1", "tests", command, result(1, stderr=b"failed /tmp/a"), 2, "subject")
        second = normalize_verification_result("run-2", "tests", command, result(1, stderr=b"failed /tmp/b"), 2, "subject")
        self.assertTrue(no_progress(first, second))
