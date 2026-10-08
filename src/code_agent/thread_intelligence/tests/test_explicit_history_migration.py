import json
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import ContextBundle, Message, ToolCall, Usage
from code_agent.core.task_state import TaskState
from code_agent.sessions._codec import encode_datetime, encode_message, utc_now
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.compaction import SemanticCompactor
from code_agent.thread_intelligence.context_builder import ThreadAwareContextBuilder
from code_agent.thread_intelligence.models import SummaryResponse


def seed(path, root, thread, count=6000, payload_bytes=0):
    assert path.resolve().is_relative_to(root.resolve())
    stamp = encode_datetime(utc_now())
    with closing(sqlite3.connect(path)) as connection:
        def rows():
            for i in range(count//3):
                call = ToolCall(f"c{i}", "read_file", {"path": f"src/u{i}.py"})
                for message in (Message("user", f"legacy request {i}"), Message("assistant", tool_calls=(call,)),
                                Message("tool", f"evidence {i}" + "x" * payload_bytes, tool_call_id=call.id, name=call.name)):
                    yield thread, encode_message(message), stamp
        connection.executemany("INSERT INTO messages(thread_id,payload,created_at) VALUES (?,?,?)", rows())
        connection.commit()


def raw_digest(path, root, thread):
    assert path.resolve().is_relative_to(root.resolve())
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path)) as connection:
        for sequence, payload in connection.execute("SELECT sequence,payload FROM messages WHERE thread_id=? ORDER BY sequence", (thread,)):
            digest.update(str(sequence).encode("ascii") + b":" + payload.encode("utf-8") + b"\n")
    return digest.hexdigest()


class Prefix:
    async def build(self, request):
        return ContextBundle("fixture trusted rules", request.messages)


class Summary:
    def __init__(self):
        self.max_sources = 0
    async def summarize(self, request, cancellation):
        self.max_sources = max(self.max_sources, len(request.sources))
        return SummaryResponse("Prior source records remain retrievable", "fixture-model", Usage(1, 1))


class ExplicitSemanticMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_6000_legacy_records_cancel_after_publication_reopen_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "history.db"
            assert path.resolve().is_relative_to(root)
            repository = SQLiteSessionRepository(path)
            reopened = None
            try:
                thread = await repository.create_thread()
                seed(path, root, thread)
                original_digest = raw_digest(path, root, thread)
                summary = Summary()
                config = ContextConfig(root, root, "system")
                def builder(repo):
                    return ThreadAwareContextBuilder(repo, SemanticCompactor(summary, DeterministicCompactor(config)),
                        Prefix(), context_limit=10_000, target_tokens=4_000)
                request = ContextRequest(thread, 6000, (), "", (), TaskState.empty(), CancellationToken())
                with self.assertRaisesRegex(ValueError, "explicit migration"):
                    await builder(repository).build(request)
                token = CancellationToken()
                original = repository.publish_semantic_checkpoint
                async def publish(checkpoint, entries):
                    await original(checkpoint, entries)
                    token.cancel()
                repository.publish_semantic_checkpoint = publish
                with self.assertRaises(CancellationError):
                    await builder(repository).compact_context(thread, token)
                first = await repository.semantic_checkpoint_page(thread, newest=True, limit=1)
                self.assertEqual(len(first), 1)
                first_end = first[0].source_end.sequence
                repository.close()
                assert path.resolve().is_relative_to(root)
                reopened = SQLiteSessionRepository(path)
                report = await builder(reopened).compact_context(thread)
                self.assertTrue(report.migrated)
                self.assertGreater(report.checkpoints_published, 1)
                self.assertLessEqual(summary.max_sources, 1000)
                all_checkpoints = await reopened.semantic_checkpoint_page(thread, limit=128)
                self.assertIn(first_end, [c.source_end.sequence for c in all_checkpoints])
                bundle = await builder(reopened).build(request)
                self.assertIn("legacy request 1999", str(bundle.messages))
                self.assertEqual((await reopened.history_stats(thread))["message_count"], 6000)
                self.assertEqual(raw_digest(path, root, thread), original_digest)
                # Exact old raw payloads and stable UUID lookup survive the migration.
                original_item = await reopened.search_history_page(thread, "evidence 0", limit=1)
                fragment = await reopened.history_item_fragment(thread, original_item["items"][0]["item_id"])
                self.assertIn("evidence 0", fragment["text"])
            finally:
                repository.close()
                if reopened is not None:
                    reopened.close()
