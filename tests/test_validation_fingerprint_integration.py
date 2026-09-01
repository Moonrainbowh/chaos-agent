from __future__ import annotations

import unittest

from code_agent.core.engine_actions import _validation_fingerprint
from code_agent.core.models import ActionRequest
from code_agent.runtime.models import CommandResult, TerminationReason
from code_agent_win.tool_support import command_action_result


def failed_result(stderr: bytes) -> CommandResult:
    return CommandResult(
        argv=("python",),
        display_command="python",
        returncode=1,
        reason=TerminationReason.EXITED,
        stdout=b"",
        stderr=stderr,
        duration_s=0,
        truncated=False,
        cwd=".",
    )


def integrated_fingerprint(stderr: bytes) -> str | None:
    request = ActionRequest(
        "verify",
        "run_verification",
        {"kind": "pytest", "cwd": ".", "targets": ["tests"]},
    )
    action_result = command_action_result(request, failed_result(stderr))
    return _validation_fingerprint(request, action_result)


class ValidationFingerprintIntegrationTests(unittest.TestCase):
    def test_live_supervision_retains_failure_difference_after_four_kib(self) -> None:
        prefix = b"x" * 4_100

        first = integrated_fingerprint(prefix + b"A")
        second = integrated_fingerprint(prefix + b"B")

        self.assertIsNotNone(first)
        self.assertNotEqual(first, second)

    def test_live_supervision_normalizes_temp_paths_and_timestamps(self) -> None:
        first = integrated_fingerprint(
            b"failed C:\\Users\\me\\AppData\\Local\\Temp\\one.txt "
            b"2026-09-01T01:02:03+08:00"
        )
        second = integrated_fingerprint(
            b"failed C:\\Users\\me\\AppData\\Local\\Temp\\two.txt "
            b"2027-10-02T03:04:05+08:00"
        )

        self.assertIsNotNone(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
