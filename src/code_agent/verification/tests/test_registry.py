from __future__ import annotations

import unittest

from code_agent.runtime.models import CommandResult, StreamName, TerminationReason
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

    def test_invalid_bytes_produce_distinct_failure_fingerprints(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        first = normalize_verification_result(
            "run-1", "tests", command, result(1, stderr=b"failed \x80"), 2, "subject"
        )
        second = normalize_verification_result(
            "run-2", "tests", command, result(1, stderr=b"failed \x81"), 2, "subject"
        )

        self.assertFalse(no_progress(first, second))
        self.assertNotEqual(first.evidence.output_hash, second.evidence.output_hash)

    def test_failure_difference_after_four_kib_is_not_discarded(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        prefix = b"x" * 4_100
        first = normalize_verification_result(
            "run-1", "tests", command, result(1, stderr=prefix + b"A"), 2, "subject"
        )
        second = normalize_verification_result(
            "run-2", "tests", command, result(1, stderr=prefix + b"B"), 2, "subject"
        )

        self.assertFalse(no_progress(first, second))

    def test_temp_paths_and_timestamps_remain_normalized(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        first = normalize_verification_result(
            "run-1",
            "tests",
            command,
            result(1, stderr=b"failed /tmp/a 2026-09-01T01:02:03+08:00"),
            2,
            "subject",
        )
        second = normalize_verification_result(
            "run-2",
            "tests",
            command,
            result(1, stderr=b"failed /tmp/b 2027-10-02T03:04:05+08:00"),
            2,
            "subject",
        )

        self.assertTrue(no_progress(first, second))

    def test_non_temp_windows_path_segments_are_not_normalized(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        first = normalize_verification_result(
            "run-1", "tests", command,
            result(1, stderr=b"failed C:\\attempt\\one.txt"), 2, "subject",
        )
        second = normalize_verification_result(
            "run-2", "tests", command,
            result(1, stderr=b"failed C:\\attempt\\two.txt"), 2, "subject",
        )

        self.assertFalse(no_progress(first, second))

    def test_temp_prefixes_are_not_treated_as_complete_path_segments(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        cases = (
            (b"failed C:\\Tempest\\same.txt", b"failed D:\\Tempest\\same.txt"),
            (b"failed C:\\tmpfoo\\same.txt", b"failed D:\\tmpfoo\\same.txt"),
            (b"failed /tmpfoo/same.txt", b"failed /TMPfoo/same.txt"),
        )

        for first_output, second_output in cases:
            with self.subTest(first=first_output, second=second_output):
                first = normalize_verification_result(
                    "run-1", "tests", command,
                    result(1, stderr=first_output), 2, "subject",
                )
                second = normalize_verification_result(
                    "run-2", "tests", command,
                    result(1, stderr=second_output), 2, "subject",
                )
                self.assertFalse(no_progress(first, second))

    def test_quoted_windows_temp_paths_with_spaces_are_normalized(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        first = normalize_verification_result(
            "run-1", "tests", command,
            result(1, stderr=b'failed "C:\\Temp\\one dir\\a.txt"'), 2, "subject",
        )
        second = normalize_verification_result(
            "run-2", "tests", command,
            result(1, stderr=b'failed "C:\\Temp\\two dir\\b.txt"'), 2, "subject",
        )

        self.assertTrue(no_progress(first, second))

    def test_truncation_status_is_scoped_to_the_stream_that_lost_bytes(self) -> None:
        command = VerificationCommand(("python", "-m", "pytest"), ".", 30)
        command_result = CommandResult(
            ("python",),
            "python",
            1,
            TerminationReason.OUTPUT_LIMIT,
            b"stdout \xe4",
            b"stderr \xe4",
            0,
            True,
            ".",
            truncated_streams=frozenset({StreamName.STDOUT}),
        )

        normalized = normalize_verification_result(
            "run-1", "tests", command, command_result, 2, "subject"
        )

        self.assertIn(
            "stdout:\n[decoding=incomplete_tail;", normalized.evidence.diagnostic
        )
        self.assertIn(
            "stderr:\n[decoding=unknown_or_mixed;", normalized.evidence.diagnostic
        )
