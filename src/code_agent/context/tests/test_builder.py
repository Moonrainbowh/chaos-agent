from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.budget import PromptBudget  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.errors import RuleLimitError  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.context.tokens import estimate_tokens  # noqa: E402
from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.core.context_request import ContextRequest  # noqa: E402
from code_agent.core.models import Message, ToolDefinition  # noqa: E402
from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


def _context_request(**updates: object) -> ContextRequest:
    values = {
        "thread_id": "thread-1",
        "revision": 1,
        "messages": (),
        "user_input": "",
        "tools": (),
        "task_state": TaskState.empty(),
        "cancellation": CancellationToken(),
    }
    values.update(updates)
    return ContextRequest(**values)  # type: ignore[arg-type]


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

        bundle = await self.builder.build(
            "thread-1", history, "inspect tool", (), TaskState.empty(), CancellationToken()
        )

        self.assertEqual(bundle.messages, history + (Message(role="user", content="inspect tool"),))
        self.assertTrue(bundle.system_prompt.startswith("Stable system prefix."))
        self.assertIn("Root constraint.", bundle.system_prompt)
        self.assertIn("Local constraint.", bundle.system_prompt)
        self.assertIn("src/tool.py", bundle.system_prompt)
        self.assertIn("inspect_file", bundle.system_prompt)
        self.assertEqual(bundle.measurements["prompt_tokens"], 20_000)
        self.assertEqual(
            bundle.measurements["repo_map_tokens"],
            self.config.prompt_budget.max_repo_map_tokens,
        )
        self.assertEqual(bundle.measurements["cache_misses"], 1)
        self.assertEqual(bundle.measurements["cache_hits"], 0)
        self.assertEqual(bundle.measurements["removed_message_count"], 0)
        again = await self.builder.build(
            "thread-1", history, "inspect tool", (), TaskState.empty(), CancellationToken()
        )
        self.assertEqual(bundle, again)
        self.assertEqual(
            again.measurements["cache_hits"],
            bundle.measurements["cache_misses"],
        )

    async def test_build_rejects_non_context_requests(self) -> None:
        with self.assertRaisesRegex(TypeError, "request must be a ContextRequest"):
            await self.builder.build(object())  # type: ignore[arg-type]

    async def test_build_propagates_pre_cancelled_request_before_background_work(self) -> None:
        cancellation = CancellationToken()
        cancellation.cancel("stop context build")

        with self.assertRaises(CancellationError) as raised:
            await self.builder.build(_context_request(cancellation=cancellation))

        self.assertEqual(raised.exception.reason, "stop context build")
        self.assertEqual(self.builder.repo_map.cache.counters(), (0, 0))

    async def test_build_propagates_cancellation_during_background_work(self) -> None:
        entered = Event()
        release = Event()

        class BlockingRepoMapBuilder(RepoMapBuilder):
            def render_with_metrics(
                self, query: str, touched_files: tuple[str, ...], token_budget: int
            ) -> tuple[str, int, int]:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release repo map render")
                return super().render_with_metrics(
                    query, touched_files, token_budget
                )

        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        builder = WorkspaceContextBuilder(
            self.config,
            RuleLoader(guard, files, self.config),
            BlockingRepoMapBuilder(files, self.config),
            DeterministicCompactor(self.config),
        )
        cancellation = CancellationToken()
        request = _context_request(cancellation=cancellation)
        build_task = asyncio.create_task(builder.build(request))

        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            cancellation.cancel("cancel during context build")
            release.set()
            with self.assertRaises(CancellationError) as raised:
                await build_task
            self.assertEqual(raised.exception.reason, "cancel during context build")
        finally:
            release.set()
            await asyncio.gather(build_task, return_exceptions=True)

    async def test_empty_user_input_rebuilds_history_without_adding_empty_message(self) -> None:
        history = (
            Message(role="user", content="old " + "x" * 400),
            Message(role="assistant", content="old answer"),
            Message(role="user", content="latest request"),
        )

        bundle = await self.builder.build(
            "thread-1", history, "", (), TaskState.empty(), CancellationToken()
        )

        self.assertNotIn(Message(role="user", content=""), bundle.messages)
        self.assertEqual(bundle.messages[-1].content, "latest request")
        self.assertIn("Root constraint.", bundle.system_prompt)
        self.assertIn("src/tool.py", bundle.system_prompt)
        self.assertLessEqual(len(bundle.messages), len(history))

    async def test_disabled_repo_map_never_scans_workspace_files(self) -> None:
        config = ContextConfig(
            self.root,
            self.cwd,
            "Stable system prefix.",
            repo_scan=100,
            repo_map_enabled=False,
        )
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        repo_map = RepoMapBuilder(files, config)
        builder = WorkspaceContextBuilder(
            config,
            RuleLoader(guard, files, config),
            repo_map,
            DeterministicCompactor(config),
        )

        with patch.object(
            repo_map,
            "render_with_metrics",
            side_effect=AssertionError("repo map must stay disabled"),
        ):
            bundle = await builder.build(
                "thread-1", (), "explain this directory", (), TaskState.empty(),
                CancellationToken(),
            )

        self.assertNotIn("src/tool.py", bundle.system_prompt)
        self.assertEqual(bundle.measurements["cache_hits"], 0)
        self.assertEqual(bundle.measurements["cache_misses"], 0)

    async def test_greeting_skips_repo_map_in_a_project_context(self) -> None:
        with patch.object(
            self.builder.repo_map,
            "render_with_metrics",
            side_effect=AssertionError("greetings must not scan the repository"),
        ):
            bundle = await self.builder.build(
                "thread-1", (), "你好！", (), TaskState.empty(), CancellationToken()
            )

        self.assertNotIn("src/tool.py", bundle.system_prompt)
        self.assertEqual(bundle.measurements["cache_hits"], 0)
        self.assertEqual(bundle.measurements["cache_misses"], 0)

    async def test_build_passes_changed_then_read_paths_to_turn_view(self) -> None:
        task_state = TaskState(
            files_read=("src/read.py", "src/shared.py"),
            files_changed=("src/changed.py", "src/shared.py"),
        )

        with patch.object(
            self.builder.repo_map,
            "render_with_metrics",
            wraps=self.builder.repo_map.render_with_metrics,
        ) as render:
            await self.builder.build(
                "thread-1", (), "repair", (), task_state, CancellationToken()
            )

        self.assertEqual(render.call_count, 1)
        self.assertEqual(
            render.call_args.args[1],
            ("src/changed.py", "src/shared.py", "src/read.py"),
        )

    async def test_context_request_rejects_invalid_message_sequences(self) -> None:
        with self.assertRaises(TypeError):
            await self.builder.build(
                "thread-1", ("not a message",), "request", (), TaskState.empty(),
                CancellationToken(),
            )  # type: ignore[arg-type]

    async def test_build_reserves_tools_before_dynamically_capping_messages(self) -> None:
        config = ContextConfig(
            self.root, self.cwd, "Stable system prefix.", repo_scan=100,
            prompt_budget=PromptBudget(
                max_prompt_tokens=4_400, max_rule_tokens=3_000,
                max_tool_tokens=1_500, max_task_state_tokens=1_000,
                max_repo_map_tokens=2_000, max_message_tokens=4_000,
                min_message_tokens=2_000, safety_tokens=500,
            ),
        )
        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        builder = WorkspaceContextBuilder(
            config, RuleLoader(guard, files, config), RepoMapBuilder(files, config),
            DeterministicCompactor(config),
        )
        tool = ToolDefinition("inspect", "x" * 3_600, {"type": "object"})
        history = (Message(role="user", content="y" * 12_000),)

        without_tools = await builder.build(
            "thread-1", history, "", (), TaskState.empty(), CancellationToken()
        )
        with_tools = await builder.build(
            "thread-1", history, "", (tool,), TaskState.empty(), CancellationToken()
        )

        self.assertLess(
            estimate_tokens(with_tools.messages[-1].content),
            estimate_tokens(without_tools.messages[-1].content),
        )

    async def test_build_keeps_real_prompt_within_budget_before_safety(self) -> None:
        tools = (ToolDefinition("inspect", "Read workspace information.", {"type": "object"}),)
        bundle = await self.builder.build(
            "thread-1", (Message(role="user", content="history " * 4_000),),
            "inspect tool", tools, TaskState.empty(), CancellationToken(),
        )
        rendered_messages = sum(estimate_tokens(message.content) + 1 for message in bundle.messages)
        rendered_tools = sum(estimate_tokens(str(tool.to_dict())) for tool in tools)
        self.assertLessEqual(
            estimate_tokens(bundle.system_prompt) + rendered_messages + rendered_tools,
            self.config.prompt_budget.max_prompt_tokens - self.config.prompt_budget.safety_tokens,
        )

    async def test_build_rejects_rules_over_the_token_cap(self) -> None:
        (self.root / "AGENTS.md").write_text("x" * 12_100, encoding="utf-8")

        with self.assertRaisesRegex(RuleLimitError, "3,000"):
            await self.builder.build(
                "thread-1", (), "request", (), TaskState.empty(), CancellationToken()
            )

    async def test_concurrent_builds_attribute_cache_counts_to_their_own_render(self) -> None:
        class SlowRepoMapBuilder(RepoMapBuilder):
            def render_with_metrics(
                self, query: str, touched_files: tuple[str, ...], token_budget: int
            ) -> tuple[str, int, int]:
                time.sleep(0.05)
                return super().render_with_metrics(
                    query, touched_files, token_budget
                )

        guard = WorkspacePathGuard(self.root)
        files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        builder = WorkspaceContextBuilder(
            self.config,
            RuleLoader(guard, files, self.config),
            SlowRepoMapBuilder(files, self.config),
            DeterministicCompactor(self.config),
        )

        first, second = await asyncio.gather(
            builder.build(
                "thread-1", (), "same", (), TaskState.empty(), CancellationToken()
            ),
            builder.build(
                "thread-1", (), "same", (), TaskState.empty(), CancellationToken()
            ),
        )

        counts = sorted(
            (
                (bundle.measurements["cache_hits"], bundle.measurements["cache_misses"])
                for bundle in (first, second)
            )
        )
        self.assertEqual(counts, [(0, 1), (1, 0)])


if __name__ == "__main__":
    unittest.main()
