from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class WorkspaceContextBuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cwd = self.root / "src"
        self.cwd.mkdir()
        (self.root / "AGENTS.md").write_text("Root constraint.", encoding="utf-8")
        (self.cwd / "AGENTS.md").write_text("Local constraint.", encoding="utf-8")
        (self.cwd / "tool.py").write_text(
            "def inspect_file(path):\n    return path\n", encoding="utf-8"
        )
        self.config = ContextConfig(
            self.root,
            self.cwd,
            "Stable system prefix.",
            repo_scan=100,
            repo_map_tokens=120,
            message_tokens=30,
            recent_messages=2,
        )
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.builder = WorkspaceContextBuilder(
            self.config,
            RuleLoader(guard, files, self.config),
            RepoMapBuilder(files, self.config),
            DeterministicCompactor(self.config),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_build_uses_stable_prefix_rules_map_and_one_user_message(self) -> None:
        history = (Message(role="assistant", content="Earlier answer."),)

        bundle = await self.builder.build(history, "inspect tool")

        self.assertEqual(bundle.messages, history + (Message(role="user", content="inspect tool"),))
        self.assertTrue(bundle.system_prompt.startswith("Stable system prefix."))
        self.assertIn("Root constraint.", bundle.system_prompt)
        self.assertIn("Local constraint.", bundle.system_prompt)
        self.assertIn("src/tool.py", bundle.system_prompt)
        self.assertIn("inspect_file", bundle.system_prompt)
        again = await self.builder.build(history, "inspect tool")
        self.assertEqual(bundle, again)

    async def test_empty_user_input_rebuilds_history_without_adding_empty_message(self) -> None:
        history = (
            Message(role="user", content="old " + "x" * 400),
            Message(role="assistant", content="old answer"),
            Message(role="user", content="latest request"),
        )

        bundle = await self.builder.build(history, "")

        self.assertNotIn(Message(role="user", content=""), bundle.messages)
        self.assertEqual(bundle.messages[-1].content, "latest request")
        self.assertIn("Root constraint.", bundle.system_prompt)
        self.assertIn("src/tool.py", bundle.system_prompt)
        self.assertLessEqual(len(bundle.messages), len(history))

    async def test_build_rejects_invalid_message_sequences(self) -> None:
        with self.assertRaises(TypeError):
            await self.builder.build(("not a message",), "request")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
