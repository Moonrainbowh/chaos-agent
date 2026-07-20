from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import load_runtime_config
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message, Usage
from code_agent.skills.registry import SkillActivation, SkillContextBuilder, SkillRegistry
from code_agent.thread_intelligence.compaction import (
    SemanticCompactionResult,
    SemanticCompactor,
)
from code_agent.thread_intelligence.deterministic_summary import (
    DeterministicSummaryService,
)
from code_agent.thread_intelligence.models import (
    SemanticCheckpoint,
    SummaryResponse,
    anchor_message,
    semantic_checkpoint_payload,
)
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.ignore import IgnoreRules
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent_win.app import create_application
from code_agent_win.context_runtime import (
    PersistingAnchoredCompactor,
    build_context_runtime,
)


def _checkpoint(thread_id: str = "thread-a") -> SemanticCheckpoint:
    sources = tuple(
        anchor_message(thread_id, index, Message(role="user", content=text))
        for index, text in enumerate(("SECRET_SOURCE_TEXT", "public tail"))
    )
    return SemanticCheckpoint.create(
        sources,
        SummaryResponse("SECRET_SUMMARY_TEXT", "local-summary", Usage(8, 3)),
    )


class _InnerCompactor:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def compact(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return self.result


class _Sessions:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def create_checkpoint(
        self, thread_id: str, label: str, payload: dict[str, object]
    ) -> object:
        self.calls.append((thread_id, label, payload))
        if self.error is not None:
            raise self.error
        return object()


class PersistingAnchoredCompactorTests(unittest.IsolatedAsyncioTestCase):
    def test_constructor_requires_both_collaborator_methods(self) -> None:
        with self.assertRaisesRegex(TypeError, "inner must provide compact"):
            PersistingAnchoredCompactor(object(), _Sessions())
        with self.assertRaisesRegex(TypeError, "sessions must provide create_checkpoint"):
            PersistingAnchoredCompactor(_InnerCompactor(object()), object())

    async def test_forwards_request_and_persists_only_stable_checkpoint_facts(self) -> None:
        checkpoint = _checkpoint()
        result = SemanticCompactionResult(
            (Message(role="developer", content=checkpoint.summary),),
            checkpoint,
            True,
            False,
        )
        inner = _InnerCompactor(result)
        sessions = _Sessions()
        wrapper = PersistingAnchoredCompactor(inner, sessions)
        messages = [Message(role="user", content="SECRET_SOURCE_TEXT")]
        cancellation = CancellationToken()

        actual = await wrapper.compact(
            "thread-a",
            7,
            messages,
            context_tokens=91,
            context_limit=100,
            target_tokens=60,
            cancellation=cancellation,
        )

        self.assertIs(actual, result)
        args, kwargs = inner.calls[0]
        self.assertEqual(args[:2], ("thread-a", 7))
        self.assertIs(args[2], messages)
        self.assertEqual(
            {key: kwargs[key] for key in ("context_tokens", "context_limit", "target_tokens")},
            {"context_tokens": 91, "context_limit": 100, "target_tokens": 60},
        )
        self.assertIs(kwargs["cancellation"], cancellation)
        expected = semantic_checkpoint_payload(checkpoint)
        self.assertEqual(sessions.calls, [("thread-a", "semantic-compaction", expected)])
        self.assertEqual(
            set(expected),
            {"id", "thread_id", "source_start", "source_end", "source_digest", "model", "usage", "version"},
        )
        serialized = json.dumps(expected)
        self.assertNotIn("SECRET_SUMMARY_TEXT", serialized)
        self.assertNotIn("SECRET_SOURCE_TEXT", serialized)
        self.assertNotIn("revision", serialized)
        self.assertNotIn("messages", serialized)

    async def test_result_without_checkpoint_is_not_persisted(self) -> None:
        result = SemanticCompactionResult((Message(role="user", content="tail"),), None, False, False)
        sessions = _Sessions()

        actual = await PersistingAnchoredCompactor(_InnerCompactor(result), sessions).compact(
            "thread-a", 1, result.messages,
            context_tokens=1, context_limit=10, target_tokens=5,
            cancellation=CancellationToken(),
        )

        self.assertIs(actual, result)
        self.assertEqual(sessions.calls, [])

    async def test_rejects_checkpoint_for_another_thread_without_writing(self) -> None:
        result = SemanticCompactionResult((), _checkpoint("thread-b"), True, False)
        sessions = _Sessions()

        with self.assertRaisesRegex(ValueError, "checkpoint thread mismatch"):
            await PersistingAnchoredCompactor(_InnerCompactor(result), sessions).compact(
                "thread-a", 1, (), context_tokens=9, context_limit=10,
                target_tokens=5, cancellation=CancellationToken(),
            )

        self.assertEqual(sessions.calls, [])

    async def test_rejects_invalid_inner_result_without_writing(self) -> None:
        sessions = _Sessions()

        with self.assertRaisesRegex(TypeError, "SemanticCompactionResult"):
            await PersistingAnchoredCompactor(_InnerCompactor(object()), sessions).compact(
                "thread-a", 1, (), context_tokens=9, context_limit=10,
                target_tokens=5, cancellation=CancellationToken(),
            )

        self.assertEqual(sessions.calls, [])

    async def test_persistence_errors_propagate_by_identity(self) -> None:
        result = SemanticCompactionResult((), _checkpoint(), True, False)
        for error in (RuntimeError("database failed"), asyncio.CancelledError("cancelled")):
            sessions = _Sessions(error)
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as caught:
                    await PersistingAnchoredCompactor(_InnerCompactor(result), sessions).compact(
                        "thread-a", 1, (), context_tokens=9, context_limit=10,
                        target_tokens=5, cancellation=CancellationToken(),
                    )
                self.assertIs(caught.exception, error)
                self.assertEqual(len(sessions.calls), 1)


class ContextRuntimeFactoryTests(unittest.TestCase):
    def test_factory_builds_local_semantic_pipeline_with_one_shared_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = ContextConfig(root, root, "System")
            guard = WorkspacePathGuard(root)
            files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
            rules = RuleLoader(guard, files, config)
            repo_map = RepoMapBuilder(files, config)
            skills = SkillActivation(SkillRegistry())

            context = build_context_runtime(config, rules, repo_map, skills, _Sessions())

        self.assertIsInstance(context, SkillContextBuilder)
        workspace = context._inner
        self.assertIsInstance(workspace, WorkspaceContextBuilder)
        wrapper = workspace.semantic_compactor
        self.assertIsInstance(wrapper, PersistingAnchoredCompactor)
        semantic = wrapper._inner
        self.assertIsInstance(semantic, SemanticCompactor)
        self.assertIsInstance(semantic._summarizer, DeterministicSummaryService)
        self.assertIs(semantic._fallback, workspace.compactor)

    def test_application_calls_context_runtime_factory_after_sessions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            container = Path(temporary).resolve()
            root, product = container / "workspace", container / "state"
            root.mkdir()
            runtime = load_runtime_config(env={
                "CHAOS_CONFIG": str(container / "missing.toml"), "CHAOS_API": "responses",
                "CHAOS_BASE_URL": "https://api.example.test", "CHAOS_MODEL": "test",
                "CHAOS_API_KEY_ENV": "KEY",
            })
            calls: list[tuple[object, ...]] = []

            def recording_factory(*args: object) -> object:
                calls.append(args)
                return build_context_runtime(*args)

            with patch.dict("os.environ", {
                "USERPROFILE": str(container / "profile"),
                "LOCALAPPDATA": str(container / "localappdata"),
            }, clear=True):
                with patch("code_agent_win.app._model_client", return_value=object()), patch(
                    "code_agent_win.app._session_path", return_value=product / "sessions.sqlite3"
                ), patch(
                    "code_agent_win.app._product_state_root", return_value=product
                ), patch("code_agent_win.app.load_runtime_config", return_value=runtime), patch(
                    "code_agent_win.app.build_context_runtime", side_effect=recording_factory
                ):
                    application = create_application(root)

        self.assertGreaterEqual(len(calls), 1)
        self.assertIs(calls[0][-1], application.tui.sessions)
        self.assertIsNotNone(application.controller)


if __name__ == "__main__":
    unittest.main()
