"""Parent review failure text is atomically retained outside small snapshots."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from code_agent.core.models import Message
from code_agent.sessions.repository import SQLiteSessionRepository
import code_agent.sessions._context_journal as journal


class ParentReviewAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SQLiteSessionRepository(Path(self.temp.name) / "sessions.sqlite3")
        self.thread = await self.repo.create_thread()
        self.snapshot = {"task_id": "parent-task", "phase": "repair_needed"}
        self.attempt = {"task_id": "parent-task", "phase": "independent",
                        "raw_output": "failed raw", "errors": ["missing evidence"]}

    async def asyncTearDown(self):
        self.repo.close()
        self.temp.cleanup()

    async def append(self, key="step", **kwargs):
        return await self.repo.append_context_record(self.thread, "parent_review", key,
            self.snapshot, review_attempt=kwargs.pop("review_attempt", self.attempt), **kwargs)

    async def test_large_failed_raw_survives_snapshot_cap_and_stays_hidden(self):
        attempt = {**self.attempt, "raw_output": "\0" * 1_000_000}
        await self.append(review_attempt=attempt, expected_tail=None)
        stored = await self.repo.context_records(self.thread, "parent_review_attempt")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["raw_output"], attempt["raw_output"])
        self.assertEqual(await self.repo.load_messages(self.thread), ())
        self.assertEqual(len(await self.repo.context_records(self.thread, "parent_review")), 1)

    async def test_idempotent_replay_checks_attempt_and_presence(self):
        first = await self.append(expected_tail=None)
        self.assertEqual(await self.append(expected_tail=None), first)
        with self.assertRaises(ValueError):
            await self.append(review_attempt={**self.attempt, "raw_output": "different"})
        with self.assertRaises(ValueError):
            await self.append(review_attempt=None)
        self.assertEqual(len(await self.repo.context_records(self.thread, "parent_review_attempt")), 1)
        await self.append("without", review_attempt=None)
        with self.assertRaises(ValueError):
            await self.append("without")

    async def test_stale_cas_leaves_neither_snapshot_nor_attempt(self):
        first = await self.append(expected_tail=None)
        with self.assertRaises(ValueError):
            await self.append("next", expected_tail=None)
        records = await self.repo.context_records(self.thread, "parent_review")
        self.assertEqual([r["id"] for r in records], [first])
        self.assertEqual(len(await self.repo.context_records(self.thread, "parent_review_attempt")), 1)

    async def test_audit_insert_failure_rolls_back_snapshot(self):
        original = journal._insert
        def reject_attempt(connection, thread_id, kind, identifier, payload):
            if kind == "parent_review_attempt":
                raise ValueError("scripted audit insert failure")
            return original(connection, thread_id, kind, identifier, payload)
        with patch.object(journal, "_insert", side_effect=reject_attempt):
            with self.assertRaises(ValueError):
                await self.append()
        self.assertEqual(await self.repo.context_records(self.thread, "parent_review"), ())
        self.assertEqual(await self.repo.context_records(self.thread, "parent_review_attempt"), ())

    async def test_delivery_and_attempt_are_one_transaction(self):
        await self.append(delivery_message=Message("assistant", "effective delivery"))
        await self.append(delivery_message=Message("assistant", "effective delivery"))
        messages = await self.repo.load_messages(self.thread)
        self.assertEqual([m.content for m in messages], ["effective delivery"])
        self.assertEqual(len(await self.repo.context_records(self.thread, "parent_review_attempt")), 1)

    async def test_output_and_error_limits_reject_without_truncation(self):
        for attempt in (
            {**self.attempt, "raw_output": "x" * 1_000_001},
            {**self.attempt, "effective_output": "x" * 1_000_001},
            {**self.attempt, "errors": ["error"] * 129},
            {**self.attempt, "errors": ["e" * 4097]},
            {**self.attempt, "errors": [{"path": "p" * 513, "message": "error"}]},
            {**self.attempt, "errors": [{"path": "/", "message": "m" * 4097}]},
        ):
            with self.assertRaises(ValueError):
                await self.append(review_attempt=attempt)
        self.assertEqual(await self.repo.context_records(self.thread, "parent_review_attempt"), ())

    async def test_attempt_only_parent_review_and_task_binding(self):
        with self.assertRaises(ValueError):
            await self.repo.append_context_record(self.thread, "note", "bad", {}, review_attempt=self.attempt)
        with self.assertRaises(ValueError):
            await self.append(review_attempt={**self.attempt, "task_id": "other"})
        for malformed in ({}, {**self.attempt, "raw_output": None},
                          {**self.attempt, "errors": "one error"}):
            with self.assertRaises(ValueError):
                await self.append(review_attempt=malformed)

    async def test_structured_errors_and_effective_output_preserved(self):
        attempt = {**self.attempt, "errors": ["legacy error", {"path": "/findings/0/evidence", "message": "missing source"}],
                   "effective_output": "rewritten valid output"}
        await self.append(review_attempt=attempt)
        stored = (await self.repo.context_records(self.thread, "parent_review_attempt"))[0]
        self.assertEqual(stored["errors"], attempt["errors"])
        self.assertEqual(stored["effective_output"], attempt["effective_output"])
        with self.assertRaises(ValueError):
            await self.append(review_attempt={**attempt, "effective_output": "changed"})

    async def test_other_context_kinds_keep_independent_key_namespace(self):
        await self.append()
        await self.repo.append_context_record(self.thread, "note", "step", {"text": "independent note"})
        self.assertEqual(len(await self.repo.context_records(self.thread, "note")), 1)

    async def test_distinct_large_raw_and_effective_jointly_preserved(self):
        attempt = {**self.attempt, "raw_output": "\0" * 700_000,
                   "effective_output": "\x01" * 700_000}
        await self.append(review_attempt=attempt)
        stored = (await self.repo.context_records(self.thread, "parent_review_attempt"))[0]
        self.assertEqual(stored["raw_output"], attempt["raw_output"])
        self.assertEqual(stored["effective_output"], attempt["effective_output"])
        self.assertNotEqual(stored["raw_output"], stored["effective_output"])

    async def test_error_metadata_boundary_preserved(self):
        attempt = {**self.attempt, "errors": [{"path": "p" * 512, "message": "m" * 4096}] * 128}
        await self.append(review_attempt=attempt)
        stored = (await self.repo.context_records(self.thread, "parent_review_attempt"))[0]
        self.assertEqual(stored["errors"], attempt["errors"])


if __name__ == "__main__":
    unittest.main()
