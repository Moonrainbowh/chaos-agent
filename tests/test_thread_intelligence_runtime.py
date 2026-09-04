from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.repo_index import RepoIndexService
from code_agent.context.repo_map import RepoMapViewCache
from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, ModelEvent
from code_agent.core.models import ModelEventKind, Usage
from code_agent.core.task_state import TaskState
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.compaction import SemanticCompactor
from code_agent.thread_intelligence.context_builder import ThreadAwareContextBuilder
from code_agent.thread_intelligence.models import SourceKind, SummaryRequest
from code_agent.thread_intelligence.models import anchor_message
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.application_context import RuntimeContextFactory
from code_agent_win.application_context import _ThreadRootContextBuilder
from code_agent_win.runtime_extensions import BoundSkillContextBuilder
from code_agent_win.runtime_extensions import ModelSemanticSummarizer, ThreadRuntimeBinding


class _SummaryModel:
    def __init__(self, summary: str = "PRODUCTION_INDEX_NEEDLE") -> None:
        self.summary = summary
        self.calls: list[tuple[object, ...]] = []
        self.usage = Usage(12, 4)

    def stream(self, system: str, messages: object, tools: object) -> object:
        self.calls.append((system, messages, tools))

        async def events():
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text=self.summary)
            yield ModelEvent(ModelEventKind.USAGE, usage=self.usage)
            yield ModelEvent(ModelEventKind.COMPLETED)

        return events()


class _Activation:
    def render(self) -> str:
        return "SKILL_INJECTION_MARKER"


class _Skills:
    def __init__(self) -> None:
        self.restored: list[str] = []
        self._activation = _Activation()

    async def restore(self, thread_id: str) -> None:
        self.restored.append(thread_id)

    def activation(self, thread_id: str) -> _Activation:
        return self._activation


def _profile() -> ModelProfile:
    provider = ProviderConfig(
        "https://api.example.test",
        "frozen-summary-model",
        ApiProtocol.RESPONSES,
        "KEY",
    )
    return ModelProfile("medium", provider, 32_000, 2_000)


def _mode(profile: ModelProfile):
    profiles = {mode: profile.name for mode in AgentMode}
    registry = ModeRegistry(standard_mode_definitions(profiles))
    return registry.freeze(AgentMode.MEDIUM, {profile.name: profile})


class ProductionThreadContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)
        self.files = WorkspaceFiles(
            self.guard, IgnoreRules.from_workspace(self.root)
        )
        self.repo_index = RepoIndexService(self.files)
        self.sessions = SQLiteSessionRepository(self.root / "sessions.sqlite3")
        self.skills = _Skills()
        self.binding = ThreadRuntimeBinding()
        self.profile = _profile()
        self.model = _SummaryModel()
        self.factory = RuntimeContextFactory(
            self.root,
            git_available=False,
            repo_map_enabled=False,
            guard=self.guard,
            files=self.files,
            repo_index=self.repo_index,
            repo_view_cache=RepoMapViewCache(),
            sessions=self.sessions,
            thread_binding=self.binding,
            skills=self.skills,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_factory_uses_frozen_model_thread_pipeline(self) -> None:
        context = self.factory(_mode(self.profile), self.model, self.profile)

        self.assertIsInstance(context, BoundSkillContextBuilder)
        thread_context = context._semantic
        self.assertIsInstance(thread_context, ThreadAwareContextBuilder)
        self.assertEqual(thread_context._context_limit, 12_000)
        self.assertEqual(thread_context._target_tokens, 9_000)
        semantic = thread_context._compactor
        self.assertIsInstance(semantic, SemanticCompactor)
        self.assertIsInstance(semantic._summarizer, ModelSemanticSummarizer)
        self.assertIs(semantic._summarizer._model, self.model)
        self.assertEqual(
            semantic._summarizer._model_name, self.profile.provider.model
        )
        workspace = thread_context._inner
        self.assertIsInstance(workspace, WorkspaceContextBuilder)
        self.assertIsNone(workspace.semantic_compactor)
        self.assertIs(semantic._fallback, workspace.compactor)
        self.assertIs(context._inner, workspace)

    async def test_checkpoint_is_published_and_searchable_with_one_skill_copy(
        self,
    ) -> None:
        thread_id = await self.sessions.create_thread()
        durable: list[Message] = []
        for index in range(16):
            role = "user" if index % 2 == 0 else "assistant"
            message = Message(role, f"history {index} " + "detail " * 385)
            durable.append(message)
            await self.sessions.append_message(
                thread_id,
                message,
            )
        context = self.factory(_mode(self.profile), self.model, self.profile)
        thread_context = context._semantic
        pressure_tokens = sum(
            estimate_tokens(message.content) + 4 for message in durable
        )
        self.assertGreaterEqual(
            pressure_tokens / thread_context._context_limit, 0.9
        )
        self.assertLess(pressure_tokens / thread_context._context_limit, 1.0)
        request = ContextRequest(
            thread_id=thread_id,
            revision=3,
            messages=(Message("user", "stale caller history"),),
            user_input="already persisted input",
            tools=(),
            task_state=TaskState.empty(),
            cancellation=CancellationToken(),
            mode_snapshot={"mode": "medium"},
            permission_snapshot={"network": False},
            budget_lease={"model_turns": 2},
        )

        bundle = await context.build(request)

        checkpoints = await self.sessions.load_semantic_checkpoints(thread_id)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].model, self.profile.provider.model)
        hits = await self.sessions.search_thread_index(
            (thread_id,), "PRODUCTION_INDEX_NEEDLE"
        )
        self.assertTrue(hits)
        self.assertIs(hits[0].entry.anchor.kind, SourceKind.CHECKPOINT)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(bundle.system_prompt.count("SKILL_INJECTION_MARKER"), 1)


class StructuredHostContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_skills_passes_the_complete_request_once(self) -> None:
        class Inner:
            def __init__(self) -> None:
                self.requests: list[ContextRequest] = []

            async def build(self, request: ContextRequest) -> ContextBundle:
                self.requests.append(request)
                return ContextBundle("system", request.messages)

        inner = Inner()
        skills = _Skills()
        binding = ThreadRuntimeBinding()
        context = BoundSkillContextBuilder(inner, binding, skills)  # type: ignore[arg-type]
        request = ContextRequest(
            "thread-a",
            9,
            (Message("user", "history"),),
            "input",
            (),
            TaskState.empty(),
            CancellationToken(),
            mode_snapshot={"mode": "high", "interaction_mode": "plan"},
            permission_snapshot={"write": True},
            budget_lease={"model_turns": 8},
        )

        bundle = await context.build(request)

        self.assertEqual(inner.requests, [request])
        self.assertEqual(binding.current(), "thread-a")
        self.assertEqual(skills.restored, ["thread-a"])
        self.assertEqual(bundle.system_prompt.count("SKILL_INJECTION_MARKER"), 1)
        self.assertIn("Interaction mode: plan", bundle.system_prompt)
        self.assertIn("Do not modify files", bundle.system_prompt)

    async def test_thread_root_passes_the_complete_request(self) -> None:
        class RootRuntime:
            def __init__(self, root: Path) -> None:
                self.root = root
                self.thread_ids: list[str] = []

            def root_for_thread(self, thread_id: str) -> Path:
                self.thread_ids.append(thread_id)
                return self.root

            async def hydrate_bindings(self) -> None:
                raise AssertionError("binding should already exist")

        class Builder:
            def __init__(self) -> None:
                self._inner = object()
                self.requests: list[ContextRequest] = []

            async def build(self, request: ContextRequest) -> ContextBundle:
                self.requests.append(request)
                return ContextBundle("system", request.messages)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            builder = Builder()
            runtime = RootRuntime(root)
            factory = SimpleNamespace(
                _root=root,
                _workspace_runtime=runtime,
                _build=lambda *args: builder,
            )
            context = _ThreadRootContextBuilder(
                factory, object(), object(), object()  # type: ignore[arg-type]
            )
            request = ContextRequest(
                "thread-root",
                4,
                (Message("user", "history"),),
                "input",
                (),
                TaskState.empty(),
                CancellationToken(),
                mode_snapshot={"mode": "ultra"},
                permission_snapshot={"outside": False},
                budget_lease={"model_turns": 3},
            )
            await context.build(request)

        self.assertEqual(builder.requests, [request])
        self.assertEqual(runtime.thread_ids, ["thread-root"])


class ModelSemanticSummarizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_usage_is_charged_to_the_thread_task(self) -> None:
        class Sessions:
            def __init__(self) -> None:
                self.task = SimpleNamespace(id="task-1")
                self.charges: list[tuple[str, Usage]] = []

            async def load_task_for_thread(self, thread_id: str) -> object:
                return self.task

            async def consume_task_usage(self, task_id: str, usage: Usage) -> None:
                self.charges.append((task_id, usage))

        model = _SummaryModel("charged summary")
        sessions = Sessions()
        summarizer = ModelSemanticSummarizer(model, "frozen-model", sessions)
        source = anchor_message("thread-a", 1, Message("user", "source"))

        response = await summarizer.summarize(
            SummaryRequest((source,), 100, 1_000), CancellationToken()
        )

        self.assertEqual(response.summary, "charged summary")
        self.assertEqual(response.model, "frozen-model")
        self.assertEqual(response.usage, model.usage)
        self.assertEqual(sessions.charges, [("task-1", model.usage)])


if __name__ == "__main__":
    unittest.main()
