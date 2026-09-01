from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.runtime.models import (  # noqa: E402
    CommandResult,
    StreamName,
    TerminationReason,
)
from code_agent_win.process_actions import (  # noqa: E402
    run_powershell_action,
    run_process_action,
)
from code_agent_win.tool_support import command_action_result  # noqa: E402
from code_agent_win.tools import validate_tool_arguments  # noqa: E402


def command_result(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int | None = 0,
    reason: TerminationReason = TerminationReason.EXITED,
    truncated: bool = False,
    truncated_streams: frozenset[StreamName] = frozenset(),
) -> CommandResult:
    return CommandResult(
        argv=("tool.exe",),
        display_command="tool.exe",
        returncode=returncode,
        reason=reason,
        stdout=stdout,
        stderr=stderr,
        duration_s=0,
        truncated=truncated,
        cwd=".",
        truncated_streams=truncated_streams,
    )


class CommandActionResultTests(unittest.TestCase):
    def test_decoded_output_does_not_duplicate_raw_base64(self) -> None:
        request = ActionRequest("call", "run_process_v1", {})

        result = command_action_result(
            request,
            command_result(stdout="中文".encode("utf-8")),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["stdout"], "中文")
        self.assertEqual(result.output["stdout_encoding"], "utf-8")
        self.assertEqual(result.output["stdout_decoding"], "decoded")
        self.assertNotIn("stdout_base64", result.output)

    def test_failed_strict_decode_is_lossless_and_explicit(self) -> None:
        request = ActionRequest("call", "run_process_v1", {})
        raw = b"prefix\x80\x81"

        result = command_action_result(
            request,
            command_result(stdout=raw),
        )

        self.assertTrue(result.is_error)
        self.assertIsNone(result.output["stdout"])
        self.assertEqual(result.output["stdout_decoding"], "unknown_or_mixed")
        self.assertEqual(
            result.output["stdout_base64"],
            base64.b64encode(raw).decode("ascii"),
        )
        self.assertTrue(result.metadata["decoding_failed"])

    def test_output_limit_marks_only_an_actual_partial_tail(self) -> None:
        request = ActionRequest("call", "run_process_v1", {})
        raw = "中".encode("utf-8")[:2]

        result = command_action_result(
            request,
            command_result(
                stdout=raw,
                stderr=b"valid",
                returncode=None,
                reason=TerminationReason.OUTPUT_LIMIT,
                truncated=True,
                truncated_streams=frozenset({StreamName.STDOUT}),
            ),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["stdout_decoding"], "incomplete_tail")
        self.assertEqual(result.output["stderr_decoding"], "decoded")
        self.assertEqual(result.output["truncated"], True)

    def test_other_stream_is_not_marked_incomplete_when_stdout_hits_limit(self) -> None:
        request = ActionRequest("call", "run_process_v1", {})
        partial = "中".encode("utf-8")[:2]

        result = command_action_result(
            request,
            command_result(
                stdout=partial,
                stderr=partial,
                returncode=None,
                reason=TerminationReason.OUTPUT_LIMIT,
                truncated=True,
                truncated_streams=frozenset({StreamName.STDOUT}),
            ),
        )

        self.assertEqual(result.output["stdout_decoding"], "incomplete_tail")
        self.assertEqual(result.output["stderr_decoding"], "unknown_or_mixed")
        self.assertEqual(result.output["truncated_streams"], ("stdout",))


class ProcessActionEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_decodes_stdout_and_stderr_independently(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock(
            return_value=command_result(
                stdout="A中".encode("utf-16-le"),
                stderr="错误".encode("utf-16-be"),
            )
        )
        request = ActionRequest(
            "call",
            "run_process_v1",
            {
                "program": "tool.exe",
                "args": [],
                "stdout_encoding": "utf-16-le",
                "stderr_encoding": "utf-16-be",
            },
        )

        result = await run_process_action(
            request,
            runtime,
            CancellationToken(),
            None,
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["stdout"], "A中")
        self.assertEqual(result.output["stderr"], "错误")
        self.assertEqual(result.output["stdout_encoding"], "utf-16-le")
        self.assertEqual(result.output["stderr_encoding"], "utf-16-be")

    async def test_powershell_output_is_always_strict_utf8(self) -> None:
        runtime = Mock()
        runtime.run = AsyncMock(return_value=command_result(stdout=b"\xe9"))
        request = ActionRequest("call", "run_command", {"command": "tool"})

        result = await run_powershell_action(
            request,
            runtime,
            CancellationToken(),
            None,
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output["stdout_encoding"], "utf-8")
        self.assertEqual(result.output["stdout_decoding"], "unknown_or_mixed")
        self.assertEqual(result.output["stdout_base64"], "6Q==")

    def test_process_schema_accepts_only_declared_encoding_names(self) -> None:
        supported = (
            "utf-8",
            "windows-ansi",
            "windows-oem",
            "utf-16-le",
            "utf-16-be",
        )
        for encoding in supported:
            with self.subTest(encoding=encoding):
                arguments = {
                    "program": "tool.exe",
                    "args": [],
                    "stdout_encoding": encoding,
                    "stderr_encoding": encoding,
                }
                self.assertIsNone(
                    validate_tool_arguments("run_process_v1", arguments)
                )

        self.assertIsNotNone(
            validate_tool_arguments(
                "run_process_v1",
                {
                    "program": "tool.exe",
                    "args": [],
                    "stdout_encoding": "cp936",
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
