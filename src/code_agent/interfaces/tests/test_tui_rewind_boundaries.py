from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from pathlib import Path

from code_agent.interfaces.tui_rewind_commands import handle_rewind_command


MODULE = Path(__file__).parents[1] / "tui_rewind_commands.py"


class CancellingSource:
    async def list_candidates(
        self, thread_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> object:
        raise asyncio.CancelledError

    async def preview(
        self, thread_id: str, checkpoint_id: str, kind: object
    ) -> object:
        raise asyncio.CancelledError


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

    async def test_cancellation_propagates(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await handle_rewind_command(CancellingSource(), "thread-1", "list")


if __name__ == "__main__":
    unittest.main()
