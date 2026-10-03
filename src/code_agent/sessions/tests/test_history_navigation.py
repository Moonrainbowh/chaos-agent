from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.events import AgentEvent, EventKind  # noqa: E402
from code_agent.core.limits import EngineLimits  # noqa: E402
from code_agent.core.models import Message, ToolCall, Usage  # noqa: E402
from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.core.task_state import TaskState  # noqa: E402
from code_agent.sessions.errors import SessionNotFound, SessionStorageError  # noqa: E402
from code_agent.sessions.models import ThreadStatus  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.verification.evidence import (  # noqa: E402
    EvidenceOutcome, EvidenceProvenance, EvidenceRecord,
)


class HistoryNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary.cleanup()

    async def test_thread_pages_pass_one_thousand_and_keep_stable_order(self) -> None:
        ids = [f"thread-{index:04d}" for index in range(1107)]
        created = "2026-10-01T00:00:00+00:00"

        def seed(connection: sqlite3.Connection) -> None:
            connection.executemany(
                "INSERT INTO threads(id, created_at, updated_at, status) VALUES (?, ?, ?, ?)",
                [(item, created, created, "archived" if item == ids[4] else "active")
                 for item in reversed(ids)],
            )
            connection.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                ("2026-10-02T00:00:00+00:00", ids[-1]),
            )

        await self.repository._database.write(seed)
        expected = [ids[-1]] + [item for item in ids[:-1] if item != ids[4]]
        first = await self.repository.list_threads(limit=1000)
        second = await self.repository.list_threads(limit=1000, offset=1000)

        self.assertEqual([item.id for item in first + second], expected)
        self.assertEqual(len(first), 1000)
        self.assertEqual(await self.repository.list_threads(offset=1106), ())
        archived_page = await self.repository.list_threads(
            include_archived=True, limit=10, offset=1,
        )
        self.assertEqual([item.id for item in archived_page], ids[:10])
        self.assertEqual(len(await self.repository.list_threads()), 100)

    async def test_thread_paging_validates_offset_and_preserves_limit_contract(self) -> None:
        for value in (True, 1.5, "1", None):
            with self.subTest(offset=value), self.assertRaises(TypeError):
                await self.repository.list_threads(offset=value)
        with self.assertRaises(ValueError):
            await self.repository.list_threads(offset=-1)
        for value in (0, -1, 1001):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                await self.repository.list_threads(limit=value)

    async def test_message_pages_use_exclusive_cursor_and_return_chronological_order(self) -> None:
        thread = await self.repository.create_thread()
        other = await self.repository.create_thread()
        for index in range(5):
            await self.repository.append_message(thread, Message("user", str(index)))
            await self.repository.append_message(other, Message("user", "unrelated"))
        records = await self.repository.load_message_records(thread)
        latest = await self.repository.load_message_records(thread, limit=2)
        older = await self.repository.load_message_records(
            thread, before_sequence=latest[0].sequence, limit=2,
        )
        earliest = await self.repository.load_message_records(
            thread, before_sequence=older[0].sequence, limit=2,
        )

        self.assertEqual(latest, records[3:])
        self.assertEqual(older, records[1:3])
        self.assertEqual(earliest, records[:1])
        self.assertEqual(
            await self.repository.load_message_records(thread, before_sequence=records[2].sequence),
            records[:2],
        )
        self.assertEqual(await self.repository.load_message_records(thread, before_sequence=0), ())
        self.assertEqual(await self.repository.load_message_records(thread, limit=20), records)
        self.assertEqual(await self.repository.load_messages(thread), tuple(r.message for r in records))

    async def test_message_page_arguments_and_missing_thread_fail_closed(self) -> None:
        thread = await self.repository.create_thread()
        for field in ("before_sequence", "limit"):
            for value in (True, 1.5, "1"):
                with self.subTest(field=field, value=value), self.assertRaises(TypeError):
                    await self.repository.load_message_records(thread, **{field: value})
        for field, value in (("before_sequence", -1), ("limit", 0), ("limit", -1)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                await self.repository.load_message_records(thread, **{field: value})
        with self.assertRaises(SessionNotFound):
            await self.repository.load_message_records("missing", limit=1)

    async def test_history_continuation_preserves_messages_without_execution_state(self) -> None:
        source = await self.repository.create_thread("Original")
        attachment = AttachmentRef("a" * 64, "image/png", 3, "screen.png", 1, 1)
        messages = (
            Message("user", "inspect", attachments=(attachment,)),
            Message("assistant", tool_calls=(ToolCall("call-1", "read_file", {"path": "a.py"}),)),
            Message("tool", "contents", tool_call_id="call-1"),
            Message("assistant", "done"),
        )
        for message in messages:
            await self.repository.append_message(source, message)
        task = await self.repository.create_task(
            source, TaskContract("inspect", TaskAuthorization.local_workspace(self.temporary.name)),
        )
        await self.repository.append_event(source, AgentEvent(EventKind.ACTION_COMPLETED, {"ok": True}))
        await self.repository.create_goal(source, "original goal")
        await self.repository.save_task_state(source, TaskState(verified_facts=("verified",)))
        await self.repository.get_or_create_task_budget(source, "model-a", EngineLimits())
        await self.repository.consume_task_usage(task.id, Usage(4, 3))
        await self.repository.create_checkpoint(source, "original checkpoint")
        await self.repository.begin_verification_run("run-1", task.id, 1, "subject")
        evidence = EvidenceRecord.from_output(
            "evidence-1", "tests", EvidenceOutcome.PASS, EvidenceProvenance.SYSTEM_VERIFIER,
            1, "subject", "ok", "passed", verifier_identity="test-identity",
        )
        await self.repository.append_verification_evidence("run-1", task.id, evidence)
        source_records = await self.repository.load_message_records(source)
        source_summary = (await self.repository.list_threads())[0]

        target = await self.repository.create_thread_from_history(source)
        reopened = SQLiteSessionRepository(self.database)
        self.addCleanup(reopened.close)
        copied = await reopened.load_message_records(target)

        self.assertEqual(tuple(record.message for record in copied), messages)
        self.assertEqual([record.created_at for record in copied], [r.created_at for r in source_records])
        self.assertTrue(all(record.thread_id == target for record in copied))
        self.assertEqual(await reopened.load_message_records(source), source_records)
        summaries = {item.id: item for item in await reopened.list_threads()}
        self.assertEqual(summaries[source], source_summary)
        self.assertEqual(summaries[target].title, "Original")
        self.assertEqual((await reopened.load_thread_relation(target)).parent_thread_id, source)
        self.assertIsNone(await reopened.load_task_for_thread(target))
        self.assertEqual(await reopened.load_events(target), ())
        self.assertEqual(await reopened.list_goals(target), ())
        self.assertEqual(await reopened.list_checkpoints(target), ())
        self.assertEqual(await reopened.load_task_state(target), TaskState.empty())

        def execution_rows(connection: sqlite3.Connection) -> tuple[int, ...]:
            return tuple(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", (target,),
            ).fetchone()[0] for table in ("tasks", "task_budgets", "task_states"))

        self.assertEqual(await reopened._database.read(execution_rows), (0, 0, 0))
        self.assertEqual(await reopened.list_verification_evidence(task.id), (evidence,))

    async def test_child_continuation_is_a_sibling_and_accepts_explicit_title(self) -> None:
        root = await self.repository.create_thread("Root")
        child = await self.repository.create_thread("Child", parent_thread_id=root)
        await self.repository.append_message(child, Message("user", "child context"))

        target = await self.repository.create_thread_from_history(child, title="Continue child")

        self.assertEqual((await self.repository.load_thread_relation(target)).parent_thread_id, root)
        self.assertEqual((await self.repository.load_thread_relation(child)).parent_thread_id, root)
        self.assertEqual(set((await self.repository.load_thread_relation(root)).child_thread_ids), {child, target})
        summaries = {item.id: item for item in await self.repository.list_threads()}
        self.assertEqual(summaries[target].title, "Continue child")

    async def test_archived_and_empty_history_create_active_continuations(self) -> None:
        source = await self.repository.create_thread()
        await self.repository.archive_thread(source)

        target = await self.repository.create_thread_from_history(source)

        self.assertEqual(await self.repository.load_message_records(target), ())
        summaries = {item.id: item for item in await self.repository.list_threads(include_archived=True)}
        self.assertIs(summaries[source].status, ThreadStatus.ARCHIVED)
        self.assertIs(summaries[target].status, ThreadStatus.ACTIVE)
        self.assertIsNone(summaries[target].title)

    async def test_history_copy_failure_rolls_back_new_thread_and_messages(self) -> None:
        source = await self.repository.create_thread("Original")
        await self.repository.append_message(source, Message("user", "one"))
        await self.repository.append_message(source, Message("assistant", "two"))

        def fail_copy(connection: sqlite3.Connection) -> None:
            connection.execute(
                "CREATE TRIGGER reject_history_copy BEFORE INSERT ON messages "
                "WHEN NEW.payload LIKE '%two%' BEGIN SELECT RAISE(ABORT, 'copy failed'); END"
            )

        await self.repository._database.write(fail_copy)
        before = await self.repository.list_threads()
        with self.assertRaises(SessionStorageError):
            await self.repository.create_thread_from_history(source)
        self.assertEqual(await self.repository.list_threads(), before)
        self.assertEqual((await self.repository.load_thread_relation(source)).child_thread_ids, ())
        self.assertEqual(len(await self.repository.load_messages(source)), 2)

    async def test_missing_history_and_invalid_title_do_not_create_threads(self) -> None:
        source = await self.repository.create_thread()
        before = await self.repository.list_threads()
        with self.assertRaises(SessionNotFound):
            await self.repository.create_thread_from_history("missing")
        with self.assertRaises(ValueError):
            await self.repository.create_thread_from_history(source, title=" ")
        with self.assertRaises(TypeError):
            await self.repository.create_thread_from_history(source, title=1)
        self.assertEqual(await self.repository.list_threads(), before)


if __name__ == "__main__":
    unittest.main()
