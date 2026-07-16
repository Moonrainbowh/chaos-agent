from __future__ import annotations

import asyncio
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
from code_agent.core.cancellation import (  # noqa: E402
    CancellationError,
    CancellationToken,
)
from code_agent.core.context_request import ContextRequest  # noqa: E402
from code_agent.core.models import Message, Usage  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.thread_intelligence.compaction import (  # noqa: E402
    SemanticCompactionResult,
)
from code_agent.thread_intelligence.models import (  # noqa: E402
    SummaryResponse,
    SemanticCheckpoint,
    anchor_message,
)
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class RecordingSemanticCompactor:
    def __init__(
        self,
        result: SemanticCompactionResult | None = None,
        *,
        cancel_reason: str | None = None,
        raise_cancelled: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.calls = []
        self.result = result
        self.cancel_reason = cancel_reason
        self.raise_cancelled = raise_cancelled
        self.error = error

    async def compact(self, thread_id, revision, messages, **kwargs):
        self.calls.append((thread_id, revision, tuple(messages), kwargs))
        if self.error is not None:
            raise self.error
        if self.cancel_reason is not None:
            kwargs["cancellation"].cancel(self.cancel_reason)
            if self.raise_cancelled:
                kwargs["cancellation"].raise_if_cancelled()
        return self.result or SemanticCompactionResult(
            tuple(messages), None, False, False
        )


class RecordingDeterministicCompactor(DeterministicCompactor):
    def __init__(self, config: ContextConfig) -> None:
        super().__init__(config)
        self.calls = []

    def compact(self, messages, token_budget=None):
        self.calls.append((tuple(messages), token_budget))
        return super().compact(messages, token_budget)


class SemanticWorkspaceContextBuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cwd = self.root / "src"
        self.cwd.mkdir()
        (self.root / "AGENTS.md").write_text("Root constraint.", encoding="utf-8")
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
        self.files = WorkspaceFiles(guard, IgnoreRules.from_workspace(self.root))
        self.rules = RuleLoader(guard, self.files, self.config)
        self.repo_map = RepoMapBuilder(self.files, self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def builder(
        self,
        semantic_compactor: RecordingSemanticCompactor | None = None,
        deterministic: DeterministicCompactor | None = None,
    ) -> WorkspaceContextBuilder:
        return WorkspaceContextBuilder(
            self.config,
            self.rules,
            self.repo_map,
            deterministic or DeterministicCompactor(self.config),
            semantic_compactor=semantic_compactor,
        )

    def request(self, **updates: object) -> ContextRequest:
        values = {
            "thread_id": "thread-a",
            "revision": 2,
            "messages": (),
            "user_input": "",
            "tools": (),
            "task_state": TaskState.empty(),
            "cancellation": CancellationToken(),
        }
        values.update(updates)
        return ContextRequest(**values)  # type: ignore[arg-type]

    def test_constructor_accepts_structured_semantic_compactor(self) -> None:
        WorkspaceContextBuilder(
            self.config,
            self.rules,
            self.repo_map,
            DeterministicCompactor(self.config),
            semantic_compactor=RecordingSemanticCompactor(),
        )

    def test_constructor_rejects_semantic_compactor_without_compact(self) -> None:
        with self.assertRaisesRegex(TypeError, "semantic_compactor must provide compact"):
            WorkspaceContextBuilder(
                self.config,
                self.rules,
                self.repo_map,
                DeterministicCompactor(self.config),
                semantic_compactor=object(),
            )

    async def test_build_passes_identity_pressure_and_working_messages(self) -> None:
        semantic = RecordingSemanticCompactor()
        cancellation = CancellationToken()
        history = (Message(role="assistant", content="Earlier answer."),)

        bundle = await self.builder(semantic).build(
            self.request(
                messages=history,
                user_input="inspect tool",
                context_pressure=0.95,
                cancellation=cancellation,
            )
        )

        thread_id, revision, working, options = semantic.calls[0]
        self.assertEqual(thread_id, "thread-a")
        self.assertEqual(revision, 2)
        self.assertIs(options["cancellation"], cancellation)
        self.assertEqual(
            working, history + (Message(role="user", content="inspect tool"),)
        )
        self.assertGreater(options["context_tokens"], 0)
        self.assertGreater(options["context_limit"], 0)
        self.assertGreaterEqual(
            options["context_tokens"] / options["context_limit"], 0.95
        )
        self.assertEqual(options["target_tokens"], bundle.measurements["message_tokens"])
        self.assertEqual(bundle.measurements["semantic_triggered"], 0)
        self.assertEqual(bundle.measurements["semantic_fallback"], 0)
        self.assertEqual(bundle.measurements["semantic_source_count"], 0)

    async def test_triggered_checkpoint_is_measured_then_hard_bounded(self) -> None:
        history = (
            Message(role="user", content="old request"),
            Message(role="assistant", content="old answer"),
        )
        sources = tuple(
            anchor_message("thread-a", index + 2, message)
            for index, message in enumerate(history)
        )
        checkpoint = SemanticCheckpoint.create(
            sources,
            SummaryResponse("private summary", "summary-model", Usage(10, 3)),
        )
        semantic_messages = (
            Message(role="developer", content="Anchored checkpoint prompt."),
            Message(role="user", content="latest tail"),
        )
        semantic = RecordingSemanticCompactor(
            SemanticCompactionResult(
                semantic_messages, checkpoint, True, False
            )
        )
        deterministic = RecordingDeterministicCompactor(self.config)

        bundle = await self.builder(semantic, deterministic).build(
            self.request(messages=history, user_input="latest tail")
        )

        self.assertEqual(deterministic.calls, [(semantic_messages, 30)])
        self.assertEqual(bundle.messages, semantic_messages)
        self.assertEqual(bundle.measurements["semantic_triggered"], 1)
        self.assertEqual(bundle.measurements["semantic_fallback"], 0)
        self.assertEqual(bundle.measurements["semantic_source_count"], 2)
        self.assertTrue(all(type(value) is int for value in bundle.measurements.values()))
        self.assertNotIn("private summary", repr(dict(bundle.measurements)))
        self.assertTrue(
            set(bundle.measurements).isdisjoint(
                {"summary", "source_text", "thread_id", "digest"}
            )
        )

    async def test_fallback_and_absent_semantic_use_numeric_zero_source_metrics(self) -> None:
        fallback = SemanticCompactionResult(
            (Message(role="user", content="fallback tail"),), None, True, True
        )

        fallback_bundle = await self.builder(
            RecordingSemanticCompactor(fallback)
        ).build(self.request(user_input="latest request"))
        plain_bundle = await self.builder().build(
            self.request(user_input="latest request")
        )

        self.assertEqual(
            tuple(fallback_bundle.measurements[key] for key in (
                "semantic_triggered", "semantic_fallback", "semantic_source_count"
            )),
            (1, 1, 0),
        )
        self.assertEqual(
            tuple(plain_bundle.measurements[key] for key in (
                "semantic_triggered", "semantic_fallback", "semantic_source_count"
            )),
            (0, 0, 0),
        )

    async def test_invalid_semantic_result_is_rejected(self) -> None:
        semantic = RecordingSemanticCompactor(object())  # type: ignore[arg-type]

        with self.assertRaisesRegex(
            TypeError, "semantic_compactor returned an invalid result"
        ):
            await self.builder(semantic).build(
                self.request(user_input="latest request")
            )

    async def test_cancellation_during_or_after_semantic_never_returns_bundle(self) -> None:
        for raise_inside in (True, False):
            with self.subTest(raise_inside=raise_inside):
                deterministic = RecordingDeterministicCompactor(self.config)
                semantic = RecordingSemanticCompactor(
                    cancel_reason="stop semantic", raise_cancelled=raise_inside
                )
                with self.assertRaises(CancellationError) as raised:
                    await self.builder(semantic, deterministic).build(
                        self.request(user_input="latest request")
                    )
                self.assertEqual(raised.exception.reason, "stop semantic")
                self.assertEqual(deterministic.calls, [])

    async def test_semantic_errors_propagate_without_context_fallback(self) -> None:
        errors = (RuntimeError("semantic failed"), asyncio.CancelledError())
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as raised:
                    await self.builder(
                        RecordingSemanticCompactor(error=error)
                    ).build(self.request(user_input="latest request"))
                self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
