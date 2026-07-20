from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from pathlib import Path

from code_agent.interfaces.tui_rewind_commands import handle_rewind_command


MODULE = Path(__file__).parents[1] / "tui_rewind_commands.py"


class ControlFlowSignal(BaseException):
    pass


class RaisingSource:
    def __init__(self, error_type: type[BaseException]) -> None:
        self.error_type = error_type

    async def list_candidates(
        self, thread_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> object:
        raise self.error_type

    async def preview(
        self, thread_id: str, checkpoint_id: str, kind: object
    ) -> object:
        raise self.error_type


class RewindCommandBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_handler_signature_is_exact(self) -> None:
        parameters = inspect.signature(handle_rewind_command).parameters
        self.assertEqual(tuple(parameters), ("source", "thread_id", "instruction"))

    def test_imports_stay_within_read_only_interface_allowlist(self) -> None:
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        direct = set()
        relative = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                direct.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    relative.add(node.module)
                else:
                    direct.add(node.module or "")
        self.assertEqual(direct, {"__future__", "dataclasses", "shlex"})
        self.assertLessEqual(
            relative, {"rewind_models", "rewind_view", "terminal_display"}
        )

    async def test_cancellation_propagates_for_each_source_method(self) -> None:
        for instruction in ("list", "preview cp-1 both"):
            with self.subTest(instruction=instruction):
                with self.assertRaises(asyncio.CancelledError):
                    await handle_rewind_command(
                        RaisingSource(asyncio.CancelledError),
                        "thread-1",
                        instruction,
                    )

    async def test_other_base_exceptions_propagate(self) -> None:
        for instruction in ("list", "preview cp-1 both"):
            with self.subTest(instruction=instruction):
                with self.assertRaises(ControlFlowSignal):
                    await handle_rewind_command(
                        RaisingSource(ControlFlowSignal),
                        "thread-1",
                        instruction,
                    )


if __name__ == "__main__":
    unittest.main()
