import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import Message, ToolCall
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.context_windows.history import source_digest
from code_agent.context_windows.source_history import WindowHistory
from code_agent.context_windows.persistent_history import history_action, item_id
from code_agent.context_windows.tools import WindowToolService
from code_agent.core.models import ActionRequest
from code_agent.core.cancellation import CancellationToken


class BoundedWindowHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "history.db"
        assert self.path.resolve().is_relative_to(self.root)
        self.repo = SQLiteSessionRepository(self.path)
        self.thread = await self.repo.create_thread()

    async def asyncTearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    async def window(self, source, key="window"):
        return await self.repo.append_context_record(self.thread, "window", key,
            {"number": 1, "strategy": "persistent", "source_start": source[0].sequence,
             "source_end": source[-1].sequence, "source_digest": source_digest(source),
             "start_sequence": source[-1].sequence+1, "carry": "", "request_id": None})

    async def test_window_reopen_hot_append_and_mutation_are_source_verified(self):
        for i in range(80):
            await self.repo.append_message(self.thread, Message("user" if i == 0 else "assistant", f"source {i}"))
        rows = await self.repo.load_message_records(self.thread)
        await self.window(rows[:-2])
        history = WindowHistory(self.repo)
        records, active, _ = await history.build_state(self.thread)
        self.assertEqual(len(active), 2)
        self.assertEqual(records[0].message.content, "source 0")
        await self.repo.append_message(self.thread, Message("assistant", "new evidence"))
        self.assertEqual(len((await history.build_state(self.thread))[1]), 3)
        assert self.path.resolve().is_relative_to(self.root)
        reopened = SQLiteSessionRepository(self.path)
        try:
            self.assertEqual(len((await WindowHistory(reopened).build_state(self.thread))[1]), 3)
        finally:
            reopened.close()
        assert self.path.resolve().is_relative_to(self.root)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE messages SET payload=? WHERE thread_id=? AND sequence=?",
                (json.dumps(Message("user", "corruption").to_dict()), self.thread, rows[0].sequence))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "stale"):
            await history.build_state(self.thread)

    async def test_valid_window_digest_cannot_hide_partial_tool_group(self):
        await self.repo.append_message(self.thread, Message("user", "original"))
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall("c", "read", {}),)))
        await self.repo.append_message(self.thread, Message("tool", "result", tool_call_id="c", name="read"))
        rows = await self.repo.load_message_records(self.thread)
        await self.window(rows[:2])
        with self.assertRaisesRegex(ValueError, "unfinished"):
            await WindowHistory(self.repo).build_state(self.thread)
        other = await self.repo.create_thread()
        await self.repo.append_message(other, Message("tool", "orphan", tool_call_id="c", name="read"))
        orphan = await self.repo.read_history_page(other)
        await self.repo.append_context_record(other, "window", "orphan", {
            "number": 1, "strategy": "persistent", "source_start": orphan[0].sequence,
            "source_end": orphan[0].sequence, "source_digest": source_digest(orphan),
            "start_sequence": orphan[0].sequence+1, "carry": "", "request_id": None})
        with self.assertRaisesRegex(ValueError, "orphan"):
            await WindowHistory(self.repo).build_state(other)

    async def test_history_fragment_unicode_offsets_and_argument_search_stay_exact(self):
        message = Message("assistant", "原始🙂", tool_calls=(ToolCall("c", "read", {"path": "认证/界.py"}),))
        await self.repo.append_message(self.thread, message)
        record = (await self.repo.read_history_page(self.thread))[0]
        display = json.dumps(message.to_dict(), ensure_ascii=False)
        fragment = await history_action(self.repo, self.thread, "read_item",
            {"item_id": item_id(record), "offset": 5, "max_chars": 7})
        self.assertEqual(fragment["text"], display[5:12])
        self.assertEqual(fragment["next_offset"], 12)
        hits = await history_action(self.repo, self.thread, "search_contents", {"query": "认证/界.py"})
        self.assertEqual(hits["items"][0]["item_id"], item_id(record))
        # Compatible context_history continues to search content, not argument JSON.
        service = WindowToolService(self.repo, lambda: self.thread)
        reply = await service.dispatch(ActionRequest("q", "context_history", {"operation": "search", "query": "认证"}), CancellationToken())
        self.assertFalse(reply.output["items"])

    async def test_required_group_over_bound_is_explicit_failure(self):
        await self.repo.append_message(self.thread, Message("user", "goal"))
        await self.repo.append_message(self.thread, Message("assistant", tool_calls=(ToolCall("c", "read", {}),)))
        await self.repo.append_message(self.thread, Message("tool", "large" * 10000, tool_call_id="c", name="read"))
        with self.assertRaisesRegex(ValueError, "byte budget"):
            await WindowHistory(self.repo, max_bytes=1000).build_state(self.thread)
        self.assertEqual((await self.repo.history_stats(self.thread))["message_count"], 3)

    async def test_129_old_windows_stream_without_materializing_all_metadata(self):
        for i in range(129):
            await self.repo.append_message(self.thread, Message("user" if i == 0 else "assistant", str(i)))
            record = (await self.repo.read_history_page(self.thread, newest=True, limit=1))[0]
            await self.repo.append_context_record(self.thread, "window", str(i), {
                "number": i+1, "strategy": "persistent", "source_start": record.sequence,
                "source_end": record.sequence, "source_digest": source_digest((record,)),
                "start_sequence": record.sequence+1, "carry": "", "request_id": None})
        history = WindowHistory(self.repo)
        records, active, windows = await history.build_state(self.thread, "persistent")
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[-1]["number"], 129)
        self.assertEqual(active, ())
        self.assertEqual(records[0].message.content, "0")
        # Cumulative metadata fingerprint reuses all verified source ranges beyond LRU size.
        original = self.repo.read_history_page
        calls = []
        async def page(*args, **kwargs):
            calls.append(kwargs)
            return await original(*args, **kwargs)
        self.repo.read_history_page = page
        await history.build_state(self.thread, "persistent")
        self.assertEqual(len(calls), 2)  # empty suffix and original current user

    async def test_wrong_name_or_missing_name_cannot_close_a_window_source(self):
        for name in ("write_file", None):
            thread = await self.repo.create_thread()
            await self.repo.append_message(thread, Message("assistant", tool_calls=(ToolCall("c", "read_file", {}),)))
            await self.repo.append_message(thread, Message("tool", "unknown outcome", tool_call_id="c", name=name))
            rows = await self.repo.read_history_page(thread)
            await self.repo.append_context_record(thread, "window", str(name), {
                "number": 1, "strategy": "persistent", "source_start": rows[0].sequence,
                "source_end": rows[-1].sequence, "source_digest": source_digest(rows),
                "start_sequence": rows[-1].sequence+1, "carry": "", "request_id": None})
            self.assertEqual(len(await self.repo.pending_action_records(thread)), 1)
            with self.assertRaisesRegex(ValueError, "name does not match"):
                await WindowHistory(self.repo).build_state(thread)
            fragment = await history_action(self.repo, thread, "read_item", {"item_id": item_id(rows[-1])})
            self.assertTrue(fragment["text"])  # legacy original remains readable
