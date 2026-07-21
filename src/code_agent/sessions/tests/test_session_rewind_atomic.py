from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from code_agent.core.limits import EngineLimits
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.sessions.errors import SessionNotFound, SessionStorageError
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent.sessions.workspace_models import (
    CheckpointCursor,
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceSnapshotStatus,
)


async def prepare(repository: SQLiteSessionRepository):
    thread_id = await repository.create_thread("source")
    source = await repository.create_task(
        thread_id,
        TaskContract("repair", TaskAuthorization.local_workspace("C:/managed")),
    )
    await repository.transition_task(source.id, TaskStatus.PAUSED)
    await repository.get_or_create_task_budget(thread_id, "model", EngineLimits())
    lineage = WorkspaceLineageRecord.create(
        repository_id="repo",
        source_root="C:/source",
        worktree_root=f"C:/managed/{uuid.uuid4().hex}",
        branch_name=f"codex/{uuid.uuid4().hex}",
        head_commit="a" * 40,
        owner_task_id=source.id,
    )
    await repository.create_lineage(lineage)
    cursor = CheckpointCursor(
        snapshot_status=WorkspaceSnapshotStatus.UNAVAILABLE,
        lineage_id=lineage.id,
    )
    checkpoint = await repository.publish_workspace_checkpoint(
        thread_id, "source", {}, None, cursor
    )
    rollback = await repository.publish_workspace_checkpoint(
        thread_id, "rollback", {}, None, cursor
    )
    operation = RewindOperationRecord.create(
        lineage.id, checkpoint, rollback, RewindMode.SESSION, "b" * 64
    )
    await repository.begin_rewind(operation)
    return source, lineage, operation


class AtomicSessionRewindTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_rewind_forks_transfers_supersedes_and_completes_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteSessionRepository(Path(temporary) / "sessions.sqlite3")
            source, lineage, operation = await prepare(repository)
            replacement_id = uuid.uuid4().hex

            completed = await repository.complete_session_rewind(
                operation.id, source.id, replacement_id
            )
            repeated = await repository.complete_session_rewind(
                operation.id, source.id, replacement_id
            )

            self.assertEqual(completed.status, RewindOperationStatus.COMPLETED)
            self.assertEqual(repeated, completed)
            self.assertEqual(completed.replacement_task_id, replacement_id)
            self.assertEqual((await repository.load_task(source.id)).status, TaskStatus.SUPERSEDED)
            self.assertEqual((await repository.load_task(replacement_id)).status, TaskStatus.PAUSED)
            self.assertEqual((await repository.load_lineage(lineage.id)).owner_task_id, replacement_id)
            with self.assertRaises(ValueError):
                await repository.complete_session_rewind(
                    operation.id, uuid.uuid4().hex, replacement_id
                )

    async def test_every_session_rewind_write_window_rolls_back_to_pending(self):
        triggers = {
            "after_fork": "BEFORE UPDATE ON workspace_lineages",
            "after_transfer": "BEFORE UPDATE ON tasks WHEN NEW.status = 'superseded'",
            "after_supersede": "BEFORE UPDATE ON rewind_operations WHEN NEW.status = 'completed'",
        }
        for label, timing in triggers.items():
            with self.subTest(window=label), tempfile.TemporaryDirectory() as temporary:
                database = Path(temporary) / "sessions.sqlite3"
                repository = SQLiteSessionRepository(database)
                source, lineage, operation = await prepare(repository)
                connection = sqlite3.connect(database)
                try:
                    connection.execute(
                        f"CREATE TRIGGER fail_{label} {timing} BEGIN "
                        "SELECT RAISE(ABORT, 'injected'); END"
                    )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(SessionStorageError):
                    replacement_id = uuid.uuid4().hex
                    await repository.complete_session_rewind(
                        operation.id, source.id, replacement_id
                    )
                tasks = await repository.list_tasks(include_terminal=True)
                self.assertEqual([(task.id, task.status) for task in tasks], [(source.id, TaskStatus.PAUSED)])
                with self.assertRaises(SessionNotFound):
                    await repository.load_task(replacement_id)
                self.assertEqual((await repository.load_lineage(lineage.id)).owner_task_id, source.id)
                pending = await repository.pending_rewinds(lineage.id)
                self.assertEqual([(item.id, item.status) for item in pending], [(operation.id, RewindOperationStatus.PENDING)])
                check = sqlite3.connect(database)
                try:
                    counts = tuple(
                        check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        for table in ("threads", "tasks", "checkpoint_workspace_state")
                    )
                finally:
                    check.close()
                self.assertEqual(counts, (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
