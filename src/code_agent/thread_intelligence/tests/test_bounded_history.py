import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import Message, ToolCall, Usage
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.thread_intelligence.history_context import SemanticHistory
from code_agent.thread_intelligence.models import SemanticCheckpoint, SummaryResponse, anchor_message


class MeteredStore:
    """Public protocol fixture: all-load paths deliberately fail."""
    def __init__(self, repository):
        self.repository, self.rows, self.bytes, self.calls = repository, 0, 0, 0

    def __getattr__(self, name):
        return getattr(self.repository, name)

    async def read_history_page(self, *args, **kwargs):
        rows = await self.repository.read_history_page(*args, **kwargs)
        self.calls += 1
        self.rows += len(rows)
        self.bytes += sum(len(json.dumps(r.message.to_dict(), ensure_ascii=False).encode()) for r in rows)
        return rows

    async def load_message_records(self, *args, **kwargs):
        raise AssertionError("consumer must not fall back to all history")

    async def load_semantic_checkpoints(self, *args, **kwargs):
        raise AssertionError("consumer must use bounded checkpoint metadata")

    def reset(self):
        self.rows = self.bytes = self.calls = 0


class BoundedSemanticHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "history.db"
        assert self.path.resolve().is_relative_to(self.root)
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()
        self.store = MeteredStore(self.repo)

    async def asyncTearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    async def prepare(self, count=160):
        for i in range(count):
            await self.repo.append_message(self.thread, Message("user" if i == 0 else "assistant", f"历史 source {i}"))
        # Fixture preparation may inspect its own explicitly temporary records.
        records = await self.repo.load_message_records(self.thread)
        checkpoint = SemanticCheckpoint.create(
            tuple(anchor_message(self.thread, r.sequence, r.message) for r in records[:-4]),
            SummaryResponse("Preserved old history", "fixture-model", Usage(4, 2)))
        await self.repo.publish_semantic_checkpoint(checkpoint, ())
        return checkpoint

    async def test_source_first_streamed_then_append_uses_bounded_suffix(self):
        await self.prepare()
        history = SemanticHistory(self.store)
        records, messages, _, _ = await history.load(self.thread)
        self.assertEqual(len(records), 4)
        self.assertEqual(self.store.rows, 161)  # source + suffix + original latest user
        self.assertIn("历史 source 0", messages[-1].content)
        self.store.reset()
        await self.repo.append_message(self.thread, Message("assistant", "new suffix evidence"))
        records, _, _, _ = await history.load(self.thread)
        self.assertEqual(len(records), 5)
        self.assertEqual(self.store.rows, 6)
        self.assertLessEqual(self.store.calls, 4)

    async def test_reopen_verifies_again_and_same_sequence_corruption_is_rejected(self):
        checkpoint = await self.prepare(20)
        history = SemanticHistory(self.store)
        await history.load(self.thread)
        assert self.path.resolve().is_relative_to(self.root)
        reopened = SQLiteSessionRepository(self.path)
        try:
            cold = MeteredStore(reopened)
            await SemanticHistory(cold).load(self.thread)
            self.assertEqual(cold.rows, 21)
        finally:
            reopened.close()
        assert self.path.resolve().is_relative_to(self.root)
        with closing(sqlite3.connect(self.path)) as connection:
            payload = json.dumps(Message("user", "mutated original").to_dict(), ensure_ascii=False)
            connection.execute("UPDATE messages SET payload=? WHERE thread_id=? AND sequence=?",
                               (payload, self.thread, checkpoint.source_start.sequence))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "stale"):
            await history.load(self.thread)

    async def test_short_legacy_cold_build_and_uncovered_long_history_fail_explicitly(self):
        await self.repo.append_message(self.thread, Message("user", "original request"))
        _, messages, _, _ = await SemanticHistory(self.store).load(self.thread)
        self.assertEqual(messages, (Message("user", "original request"),))
        for i in range(4):
            await self.repo.append_message(self.thread, Message("assistant", str(i)))
        with self.assertRaisesRegex(ValueError, "explicit migration required"):
            await SemanticHistory(self.store, limit=3).load(self.thread)
        self.assertEqual((await self.repo.history_stats(self.thread))["message_count"], 5)

    async def test_required_blob_fails_before_decode_and_no_missing_api_fallback(self):
        await self.repo.append_message(self.thread, Message("user", "界" * 10000))
        with self.assertRaisesRegex(ValueError, "byte budget"):
            await SemanticHistory(self.store, max_bytes=100).load(self.thread)
        self.assertEqual(self.store.rows, 0)
        with self.assertRaises(AttributeError):
            await SemanticHistory(object()).load(self.thread)

    async def test_valid_digest_cannot_hide_partial_group_at_either_source_boundary(self):
        await self.repo.append_message(self.thread, Message("user", "original"))
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall("call", "read", {}),)))
        await self.repo.append_message(self.thread, Message("tool", "evidence", tool_call_id="call", name="read"))
        records = await self.repo.load_message_records(self.thread)
        for thread, selected, error in ((self.thread, records[:2], "unfinished"),):
            checkpoint = SemanticCheckpoint.create(
                tuple(anchor_message(thread, r.sequence, r.message) for r in selected),
                SummaryResponse("Do not hide pending call", "fixture-model", Usage(1, 1)))
            await self.repo.publish_semantic_checkpoint(checkpoint, ())
            with self.assertRaisesRegex(ValueError, error):
                await SemanticHistory(self.store).load(thread)
        other = await self.repo.create_thread()
        await self.repo.append_message(other, Message("tool", "orphan", tool_call_id="call", name="read"))
        orphan = (await self.repo.read_history_page(other))[0]
        checkpoint = SemanticCheckpoint.create((anchor_message(other, orphan.sequence, orphan.message),),
            SummaryResponse("Orphan digest is structurally valid", "fixture-model", Usage(1, 1)))
        await self.repo.publish_semantic_checkpoint(checkpoint, ())
        with self.assertRaisesRegex(ValueError, "orphan"):
            await SemanticHistory(self.store).load(other)

    async def test_fork_does_not_reuse_parent_sources_and_deleted_prefix_invalidates_cache(self):
        checkpoint = await self.prepare(20)
        history = SemanticHistory(self.store)
        await history.load(self.thread)
        tree = await self.repo.load_conversation_tree(self.thread)
        fork = await self.repo.fork_conversation(self.thread, tree.nodes[9].id)
        records, messages, _, _ = await history.load(fork)
        self.assertEqual(len(records), 10)
        self.assertFalse(any("Untrusted semantic checkpoint" in m.content for m in messages))
        self.assertTrue(all(r.thread_id == fork for r in records))
        assert self.path.resolve().is_relative_to(self.root)
        with closing(sqlite3.connect(self.path)) as connection:
            # Deliberate journal corruption, not an application rewind operation.
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DELETE FROM messages WHERE thread_id=? AND sequence=?",
                               (self.thread, checkpoint.source_end.sequence))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "stale"):
            await history.load(self.thread)

    async def test_129_obsolete_checkpoints_are_skipped_but_129_required_summaries_fail(self):
        for i in range(6):
            await self.repo.append_message(self.thread, Message("user" if i == 0 else "assistant", str(i)))
        rows = await self.repo.read_history_page(self.thread)
        for i in range(129):
            source = rows[:2] if i < 128 else rows[:4]
            checkpoint = SemanticCheckpoint.create(tuple(anchor_message(self.thread, r.sequence, r.message) for r in source),
                SummaryResponse("Old overlapping summary", f"fixture-{i}", Usage(1, 1)))
            await self.repo.publish_semantic_checkpoint(checkpoint, ())
        records, messages, _, _ = await SemanticHistory(self.store).load(self.thread)
        self.assertEqual(len(records), 2)
        self.assertEqual(sum("Untrusted semantic checkpoint" in m.content for m in messages), 1)
        other = await self.repo.create_thread()
        for i in range(129):
            await self.repo.append_message(other, Message("assistant", f"independent source {i}"))
            record = (await self.repo.read_history_page(other, newest=True, limit=1))[0]
            checkpoint = SemanticCheckpoint.create((anchor_message(other, record.sequence, record.message),),
                SummaryResponse("Necessary disjoint summary", "fixture", Usage(1, 1)))
            await self.repo.publish_semantic_checkpoint(checkpoint, ())
        with self.assertRaisesRegex(ValueError, "required semantic summaries"):
            await SemanticHistory(self.store).load(other)

    async def test_wrong_name_and_legacy_nameless_results_remain_pending_not_covered(self):
        for name in ("write_file", None):
            thread = await self.repo.create_thread()
            await self.repo.append_message(thread, Message("assistant", tool_calls=(ToolCall("same-id", "read_file", {}),)))
            await self.repo.append_message(thread, Message("tool", "unmatched", tool_call_id="same-id", name=name))
            rows = await self.repo.read_history_page(thread)
            checkpoint = SemanticCheckpoint.create(tuple(anchor_message(thread, r.sequence, r.message) for r in rows),
                SummaryResponse("Digest alone cannot close a group", "fixture", Usage(1, 1)))
            await self.repo.publish_semantic_checkpoint(checkpoint, ())
            self.assertEqual(len(await self.repo.pending_action_records(thread)), 1)
            with self.assertRaisesRegex(ValueError, "name does not match"):
                await SemanticHistory(self.store).load(thread)

    async def test_selected_summary_bytes_have_a_cumulative_bound(self):
        for i in range(3):
            await self.repo.append_message(self.thread, Message("assistant", str(i)))
            record = (await self.repo.read_history_page(self.thread, newest=True, limit=1))[0]
            checkpoint = SemanticCheckpoint.create((anchor_message(self.thread, record.sequence, record.message),),
                SummaryResponse("界" * 1000, "fixture", Usage(1, 1)))
            await self.repo.publish_semantic_checkpoint(checkpoint, ())
        with self.assertRaisesRegex(ValueError, "byte capacity"):
            await SemanticHistory(self.store, max_bytes=7000).load(self.thread)
        self.assertEqual((await self.repo.history_stats(self.thread))["message_count"], 3)
