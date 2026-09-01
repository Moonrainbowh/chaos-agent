from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.runtime.models import (  # noqa: E402
    CommandResult,
    CommandSpec,
    OutputChunk,
    RuntimeKind,
    StreamName,
    TerminationReason,
)
from code_agent.runtime.errors import (  # noqa: E402
    RuntimeErrorBase,
    RuntimeStartError,
    RuntimeUnavailable,
)
from code_agent.runtime import errors as runtime_errors  # noqa: E402


class CommandSpecTests(unittest.TestCase):
    def test_requires_exactly_one_command_form(self) -> None:
        for values in ({}, {"argv": ("python",), "powershell_script": "pwd"}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    CommandSpec(cwd=".", **values)

    def test_rejects_invalid_command_limits_and_environment(self) -> None:
        invalid = (
            {"argv": (), "timeout_s": 1, "max_output_bytes": 1},
            {"argv": ("python", 3), "timeout_s": 1, "max_output_bytes": 1},
            {"argv": ("python",), "timeout_s": 0, "max_output_bytes": 1},
            {"argv": ("python",), "timeout_s": 1, "max_output_bytes": 0},
            {"argv": ("python",), "timeout_s": 1, "max_output_bytes": True},
            {
                "argv": ("python",),
                "timeout_s": 1,
                "max_output_bytes": 1,
                "explicit_env": {"NAME": 2},
            },
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    CommandSpec(cwd=".", **values)

    def test_defensively_copies_environment_and_is_frozen(self) -> None:
        environment = {"FLAG": "before"}
        spec = CommandSpec(cwd=".", argv=("python", "-V"), explicit_env=environment)

        environment["FLAG"] = "after"

        self.assertEqual(spec.cwd, Path("."))
        self.assertEqual(spec.argv, ("python", "-V"))
        self.assertEqual(dict(spec.explicit_env), {"FLAG": "before"})
        with self.assertRaises(TypeError):
            spec.explicit_env["FLAG"] = "changed"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            spec.timeout_s = 2  # type: ignore[misc]

class ResultModelTests(unittest.TestCase):
    def test_runtime_errors_share_a_dedicated_base(self) -> None:
        self.assertTrue(issubclass(RuntimeErrorBase, RuntimeError))
        self.assertTrue(issubclass(RuntimeUnavailable, RuntimeErrorBase))
        self.assertTrue(issubclass(RuntimeStartError, RuntimeErrorBase))
        process_tree_error = getattr(runtime_errors, "ProcessTreeTerminationError", None)
        self.assertIsNotNone(process_tree_error)
        self.assertTrue(issubclass(process_tree_error, RuntimeErrorBase))

    def test_enums_expose_stable_wire_values(self) -> None:
        self.assertEqual([item.value for item in RuntimeKind], ["local", "docker"])
        self.assertEqual([item.value for item in StreamName], ["stdout", "stderr"])
        self.assertEqual(
            [item.value for item in TerminationReason],
            ["exited", "timeout", "cancelled", "output_limit"],
        )

    def test_chunks_and_results_copy_mutable_byte_inputs(self) -> None:
        chunk_data = bytearray(b"chunk")
        stdout = bytearray(b"out")
        result = CommandResult(
            argv=("python", "-V"),
            display_command="python -V",
            returncode=0,
            reason=TerminationReason.EXITED,
            stdout=stdout,
            stderr=b"",
            duration_s=0.1,
            truncated=False,
            cwd=".",
            cancellation_reason=None,
        )
        chunk = OutputChunk(StreamName.STDOUT, chunk_data)

        chunk_data[:] = b"other"
        stdout[:] = b"new"

        self.assertEqual(chunk.data, b"chunk")
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.cwd, ".")
        self.assertIsNone(result.cancellation_reason)
        self.assertIsInstance(chunk.data, bytes)
        with self.assertRaises(FrozenInstanceError):
            result.returncode = 1  # type: ignore[misc]

    def test_result_rejects_invalid_field_types_and_duration(self) -> None:
        valid = {
            "argv": ("python",),
            "display_command": "python",
            "returncode": 0,
            "reason": TerminationReason.EXITED,
            "stdout": b"",
            "stderr": b"",
            "duration_s": 0.0,
            "truncated": False,
            "cwd": "nested/path",
            "cancellation_reason": None,
        }
        invalid = (
            {"argv": ["python"]},
            {"returncode": True},
            {"reason": "exited"},
            {"stdout": "text"},
            {"duration_s": -1},
            {"duration_s": False},
            {"truncated": 1},
            {"truncated_streams": {StreamName.STDOUT}},
            {"truncated_streams": frozenset({"stdout"})},
            {"cwd": 1},
            {"cwd": ""},
            {"cwd": "/outside"},
            {"cwd": "../outside"},
            {"cwd": "nested\\child"},
            {"cancellation_reason": 1},
            {"cancellation_reason": " "},
        )
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises((TypeError, ValueError)):
                    CommandResult(**(valid | override))

        cancelled = CommandResult(
            **(
                valid
                | {
                    "reason": TerminationReason.CANCELLED,
                    "returncode": None,
                    "cancellation_reason": "user stop",
                }
            )
        )
        self.assertEqual(cancelled.cancellation_reason, "user stop")

    def test_result_rejects_contradictory_termination_fields(self) -> None:
        valid = {
            "argv": ("python",),
            "display_command": "python",
            "returncode": 0,
            "reason": TerminationReason.EXITED,
            "stdout": b"",
            "stderr": b"",
            "duration_s": 0.0,
            "truncated": False,
            "cwd": ".",
            "cancellation_reason": None,
        }
        contradictory = (
            {"returncode": None},
            {"cancellation_reason": "not cancelled"},
            {"truncated": True},
            {"truncated_streams": frozenset({StreamName.STDOUT})},
            {"reason": TerminationReason.TIMEOUT, "returncode": 0},
            {
                "reason": TerminationReason.TIMEOUT,
                "returncode": None,
                "cancellation_reason": "wrong detail",
            },
            {
                "reason": TerminationReason.TIMEOUT,
                "returncode": None,
                "truncated": True,
                "truncated_streams": frozenset({StreamName.STDOUT}),
            },
            {"reason": TerminationReason.CANCELLED, "returncode": None},
            {
                "reason": TerminationReason.CANCELLED,
                "returncode": 0,
                "cancellation_reason": "user stop",
            },
            {
                "reason": TerminationReason.CANCELLED,
                "returncode": None,
                "cancellation_reason": "user stop",
                "truncated": True,
            },
            {
                "reason": TerminationReason.OUTPUT_LIMIT,
                "returncode": None,
                "truncated": False,
            },
            {
                "reason": TerminationReason.OUTPUT_LIMIT,
                "returncode": None,
                "truncated": True,
                "cancellation_reason": "wrong detail",
            },
        )
        for override in contradictory:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    CommandResult(**(valid | override))

        for override in (
            {"reason": TerminationReason.TIMEOUT, "returncode": None},
            {
                "reason": TerminationReason.CANCELLED,
                "returncode": None,
                "cancellation_reason": "user stop",
            },
            {
                "reason": TerminationReason.OUTPUT_LIMIT,
                "returncode": None,
                "truncated": True,
                "truncated_streams": frozenset({StreamName.STDOUT}),
            },
        ):
            CommandResult(**(valid | override))


if __name__ == "__main__":
    unittest.main()
