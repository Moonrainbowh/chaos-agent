from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.context.budget import PromptBudget
from code_agent.context.builder import WorkspaceContextBuilder, _render_tools
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import (
    Message,
    ModelEvent,
    ModelEventKind,
    ToolDefinition,
    Usage,
)
from code_agent.core.task_state import TaskState
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.thread_intelligence.models import SummaryRequest, anchor_message
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from chaos_agent.application_context import _profile_prompt_budget
from chaos_agent.runtime_extensions import ModelSemanticSummarizer


class _RecordingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Message, ...]]] = []

    def stream(self, system: str, messages: tuple[Message, ...], tools: object):
        self.calls.append((system, messages))

        async def events():
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="bounded summary")
            yield ModelEvent(ModelEventKind.USAGE, usage=Usage(20, 4))
            yield ModelEvent(ModelEventKind.COMPLETED)

        return events()


class _Sessions:
    async def load_task_for_thread(self, thread_id: str) -> None:
        return None

    async def load_thread_relation(self, thread_id: str) -> object:
        return SimpleNamespace(parent_thread_id=None)


def _profile(context_window: int = 8_000) -> ModelProfile:
    provider = ProviderConfig(
        "https://api.example.test",
        "budget-model",
        ApiProtocol.RESPONSES,
        "KEY",
    )
    return ModelProfile("budget", provider, context_window, 2_000)


def _workspace_builder(
    root: Path, budget: PromptBudget, system_prompt: str = "System prompt"
) -> WorkspaceContextBuilder:
    config = ContextConfig(
        root,
        root,
        system_prompt,
        prompt_budget=budget,
        repo_map_enabled=False,
    )
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    return WorkspaceContextBuilder(
        config,
        RuleLoader(guard, files, config),
        RepoMapBuilder(files, config),
        DeterministicCompactor(config),
    )


class SemanticSummaryInputBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_input_is_bounded_before_the_model_call(self) -> None:
        sources = tuple(
            anchor_message(
                "thread-budget",
                index,
                Message("user", f"source {index} " + "detail " * 600),
            )
            for index in range(24)
        )
        request = SummaryRequest(sources, 128, 700)
        model = _RecordingModel()
        summarizer = ModelSemanticSummarizer(model, "budget-model", _Sessions())

        await summarizer.summarize(request, CancellationToken())

        system, messages = model.calls[0]
        reserved_total = (
            estimate_tokens(system)
            + estimate_tokens(messages[0].content)
            + 32
            + request.max_output_tokens
        )
        raw_source_tokens = sum(
            estimate_tokens(source.message.content) for source in sources
        )
        self.assertLessEqual(reserved_total, request.max_total_tokens)
        self.assertGreater(raw_source_tokens, request.max_total_tokens)
        self.assertIn("thread-budget:message:0", messages[0].content)

    async def test_impossible_input_budget_falls_back_before_provider_io(self) -> None:
        source = anchor_message(
            "thread-budget", 1, Message("user", "bounded source")
        )
        model = _RecordingModel()
        summarizer = ModelSemanticSummarizer(model, "budget-model", _Sessions())

        with self.assertRaisesRegex(RuntimeError, "input budget"):
            await summarizer.summarize(
                SummaryRequest((source,), 10, 10), CancellationToken()
            )

        self.assertEqual(model.calls, [])


class ProfilePromptBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_system_prompt_and_valid_rules_fit_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "AGENTS.md").write_text("rules " * 1_900, encoding="utf-8")
            budget = _profile_prompt_budget(_profile(context_window=20_000))
            builder = _workspace_builder(
                root, budget, "System instruction " * 300
            )
            rendered_rules = builder.rules.render(builder.rules.load())
            fixed_tokens = estimate_tokens(
                builder.config.system_prompt + rendered_rules
            )
            self.assertLessEqual(estimate_tokens(rendered_rules), budget.max_rule_tokens)
            self.assertGreater(fixed_tokens, budget.max_rule_tokens)
            self.assertLessEqual(
                fixed_tokens, budget.max_rule_tokens + budget.max_system_tokens
            )

            bundle = await builder.build(
                ContextRequest(
                    "thread-budget", 1, (Message("user", "hello"),), "", (),
                    TaskState.empty(), CancellationToken(),
                )
            )

            self.assertEqual(bundle.messages[-1].content, "hello")
            self.assertLessEqual(
                bundle.measurements["prompt_estimated_tokens"],
                budget.max_prompt_tokens - budget.safety_tokens,
            )

    async def test_complete_prompt_is_capped_by_small_profile_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "AGENTS.md").write_text(
                "# Rules\n" + "bounded-rule " * 800,
                encoding="utf-8",
            )
            profile = _profile()
            budget = _profile_prompt_budget(profile)
            builder = _workspace_builder(root, budget)
            tools = tuple(
                ToolDefinition(
                    f"tool_{index}",
                    "bounded tool " * 20,
                    {"type": "object", "properties": {}},
                )
                for index in range(8)
            )
            messages = tuple(
                Message("user", f"history {index} " + "detail " * 500)
                for index in range(20)
            )
            request = ContextRequest(
                "thread-budget",
                1,
                messages,
                "",
                tools,
                TaskState.empty(),
                CancellationToken(),
            )

            bundle = await builder.build(request)

        actual_tokens = (
            estimate_tokens(bundle.system_prompt)
            + estimate_tokens(_render_tools(tools))
            + sum(estimate_tokens(item.content) + 4 for item in bundle.messages)
        )
        self.assertEqual(budget.max_prompt_tokens, profile.context_window)
        self.assertLessEqual(
            actual_tokens,
            budget.max_prompt_tokens - budget.safety_tokens,
        )

    async def test_ultra_mode_expands_prompt_budget(self) -> None:
        profile = _profile(context_window=128_000)
        mode = SimpleNamespace(definition=SimpleNamespace(mode=SimpleNamespace(value="ultra")))
        budget = _profile_prompt_budget(profile, mode)
        self.assertEqual(budget.max_prompt_tokens, 64_000)
        self.assertEqual(budget.max_message_tokens, 48_000)


if __name__ == "__main__":
    unittest.main()
