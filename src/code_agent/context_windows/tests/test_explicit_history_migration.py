import json
import tempfile
import unittest
from pathlib import Path

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import Message, Usage, ModelEvent, ModelEventKind
from code_agent.core.task_state import TaskState
from code_agent.providers.prepared import PreparedProviderRequest
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.context_windows.builder import WindowContextBuilder
from code_agent.context_windows.persistent_builder import PersistentContextBuilder
from code_agent.context_windows.handoff import HandoffWriter
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import WindowPolicy, ApiContextLimits, RequestBudgetConstraints
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.thread_intelligence.tests.test_explicit_history_migration import seed, raw_digest, Prefix


class PreparedFixtureModel:
    """Immutable test request body, no transport; estimates are not Provider usage."""
    def __init__(self, strategy):
        self.strategy, self.sent = strategy, 0
    async def prepare_request(self, system, messages, tools):
        body = json.dumps({"system": system, "messages": [m.to_dict() for m in messages],
                           "tools": []}, ensure_ascii=False).encode("utf-8")
        return PreparedProviderRequest(body, "mock://no-transport", (), 128,
            diagnostics=("isolated fixture serialization, no real Provider",))
    async def stream_prepared(self, prepared):
        self.sent += 1
        text = "Older source records remain retrievable."
        if self.strategy == "boundary":
            text = json.dumps({key: "check source" for key in (
                "objective", "constraints", "confirmed_state", "evidence_anchors", "rejected_attempts",
                "open_errors", "pending_work", "next_action")})
        yield ModelEvent(ModelEventKind.TEXT_DELTA, text=text)
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(10, 10))
        yield ModelEvent(ModelEventKind.COMPLETED)


class ExplicitWindowMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_6000_all_window_strategies_explicit_migrate_reopen_original_sources(self):
        for strategy in ("summary", "boundary", "persistent"):
            with self.subTest(strategy=strategy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                path = root / "history.db"
                assert path.resolve().is_relative_to(root)
                repository = SQLiteSessionRepository(path)
                reopened = None
                try:
                    thread = await repository.create_thread()
                    seed(path, root, thread)
                    original_digest = raw_digest(path, root, thread)
                    policy = WindowPolicy(strategy=strategy, work_tokens=6000, safety_tokens=20, handoff_tokens=512)
                    limits, counter = ApiContextLimits(10_000, 128), PromptTokenCounter()
                    model = PreparedFixtureModel(strategy)
                    def builder(repo):
                        client = BudgetedWindowClient(model, repo, lambda: thread, policy, limits, counter,
                            constraints=RequestBudgetConstraints(host_prompt_tokens=2000))
                        writer = HandoffWriter(client, counter, policy, limits)
                        factory = PersistentContextBuilder if strategy == "persistent" else WindowContextBuilder
                        return factory(Prefix(), repo, policy, limits, counter, writer), client
                    current, client = builder(repository)
                    request = ContextRequest(thread, 6000, (), "", (), TaskState.empty(), CancellationToken())
                    with self.assertRaisesRegex(ValueError, "explicit migration"):
                        await current.build(request)
                    token = CancellationToken()
                    original = repository.append_context_record
                    async def append(thread_id, kind, key, payload, **kwargs):
                        result = await original(thread_id, kind, key, payload, **kwargs)
                        if kind == "window":
                            token.cancel()
                        return result
                    repository.append_context_record = append
                    with self.assertRaises(CancellationError):
                        await current.compact_context(thread, token)
                    first = await repository.context_record_page(thread, "window", newest=True, limit=1)
                    first_end = first[0]["source_end"]
                    repository.close()
                    assert path.resolve().is_relative_to(root)
                    reopened = SQLiteSessionRepository(path)
                    restored, guard = builder(reopened)
                    report = await restored.compact_context(thread)
                    self.assertEqual(report.status, "migrated")
                    bundle = await restored.build(request)
                    self.assertIn("legacy request 1999", str(bundle.messages))
                    # The resumed actual assembled request also fits the same public checker.
                    await guard.preflight_request(bundle.system_prompt, bundle.messages, ())
                    first_again = await reopened.context_record_page(thread, "window", limit=1)
                    self.assertEqual(first_again[0]["source_end"], first_end)
                    self.assertEqual((await reopened.history_stats(thread))["message_count"], 6000)
                    self.assertEqual(raw_digest(path, root, thread), original_digest)
                    item = (await reopened.search_history_page(thread, "evidence 0", limit=1))["items"][0]
                    self.assertIn("evidence 0", (await reopened.history_item_fragment(thread, item["item_id"]))["text"])
                    if strategy == "persistent":
                        self.assertEqual(model.sent, 0)
                    else:
                        self.assertGreater(model.sent, 1)
                        # >128 genuine windows must reopen without a historical total-count cap.
                        if strategy == "boundary":
                            windows = await reopened.context_window_page(thread, offset=128, limit=1)
                            self.assertEqual(len(windows["items"]), 1)
                finally:
                    repository.close()
                    if reopened is not None:
                        reopened.close()


    async def test_immutable_persistent_prefix_cannot_fit_fails_without_fake_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "history.db"
            assert path.resolve().is_relative_to(root)
            repo = SQLiteSessionRepository(path)
            try:
                thread = await repo.create_thread()
                seed(path, root, thread, 1002)
                original = raw_digest(path, root, thread)
                policy = WindowPolicy(strategy="persistent", work_tokens=6000, safety_tokens=20)
                limits, counter = ApiContextLimits(10_000, 128), PromptTokenCounter()
                model = PreparedFixtureModel("persistent")
                guard = BudgetedWindowClient(model, repo, lambda: thread, policy, limits, counter,
                    constraints=RequestBudgetConstraints(host_prompt_tokens=1000))
                builder = PersistentContextBuilder(Prefix(), repo, policy, limits, counter,
                    HandoffWriter(guard, counter, policy, limits))
                with self.assertRaisesRegex(ValueError, "effective context cap"):
                    await builder.compact_context(thread)
                self.assertEqual(raw_digest(path, root, thread), original)
                self.assertEqual(model.sent, 0)
            finally:
                repo.close()

    async def test_source_revision_change_prevents_window_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "history.db"
            assert path.resolve().is_relative_to(root)
            repo = SQLiteSessionRepository(path)
            try:
                thread = await repo.create_thread()
                seed(path, root, thread, 1002)
                policy = WindowPolicy(strategy="summary", work_tokens=6000, safety_tokens=20, handoff_tokens=512)
                limits, counter = ApiContextLimits(10000, 128), PromptTokenCounter()
                guard = BudgetedWindowClient(PreparedFixtureModel("summary"), repo, lambda: thread, policy, limits, counter)
                writer = HandoffWriter(guard, counter, policy, limits)
                old_write = writer.write
                async def changed(*args):
                    text = await old_write(*args)
                    await repo.append_message(thread, Message("user", "concurrent new source"))
                    return text
                writer.write = changed
                builder = WindowContextBuilder(Prefix(), repo, policy, limits, counter, writer)
                with self.assertRaisesRegex(ValueError, "history changed"):
                    await builder.compact_context(thread)
                self.assertEqual(await repo.context_record_page(thread, "window"), ())
            finally:
                repo.close()

    async def test_bounded_row_count_large_sources_use_actual_host_cap_and_remain_retrievable(self):
        for strategy in ("summary", "boundary", "persistent"):
            with self.subTest(strategy=strategy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                path = root / "history.db"
                assert path.resolve().is_relative_to(root)
                repo = SQLiteSessionRepository(path)
                try:
                    thread = await repo.create_thread()
                    seed(path, root, thread, 300, payload_bytes=2000)
                    original = raw_digest(path, root, thread)
                    policy = WindowPolicy(strategy=strategy, work_tokens=100000, safety_tokens=20, handoff_tokens=512)
                    limits, counter = ApiContextLimits(120000, 128), PromptTokenCounter()
                    guard = BudgetedWindowClient(PreparedFixtureModel(strategy), repo, lambda: thread, policy, limits, counter,
                        constraints=RequestBudgetConstraints(host_prompt_tokens=20000))
                    factory = PersistentContextBuilder if strategy == "persistent" else WindowContextBuilder
                    builder = factory(Prefix(), repo, policy, limits, counter, HandoffWriter(guard, counter, policy, limits))
                    request = ContextRequest(thread, 300, (), "", (), TaskState.empty(), CancellationToken())
                    if strategy != "persistent":
                        with self.assertRaisesRegex(ValueError, "input cannot fit"):
                            await builder.build(request)
                        first = (await repo.context_record_page(thread, "window", limit=1))[0]
                        self.assertLess(first["source_end"], 299)
                    report = await builder.compact_context(thread)
                    self.assertEqual(report.status, "migrated")
                    bundle = await builder.build(request)
                    _, estimated, _ = await guard.preflight_request(bundle.system_prompt, bundle.messages, ())
                    self.assertLessEqual(estimated, 19980)
                    self.assertEqual(bundle.measurements["window_input_cap"], 19980)
                    if strategy == "persistent":
                        self.assertLessEqual(bundle.measurements["context_tokens_remaining"], 19980)
                    self.assertEqual(raw_digest(path, root, thread), original)
                    item = (await repo.search_history_page(thread, "evidence 0", limit=1))["items"][0]
                    self.assertIn("evidence 0", (await repo.history_item_fragment(thread, item["item_id"]))["text"])
                finally:
                    repo.close()
