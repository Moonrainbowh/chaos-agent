from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.errors import (  # noqa: E402
    SessionCorruptionError,
    SessionNotFound,
    SessionStorageError,
)
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceSnapshotRecord,
    WorkspaceSnapshotStatus,
)
from code_agent.workspace._snapshot_manifest import (  # noqa: E402
    SnapshotManifest,
    SnapshotManifestEntry,
    manifest_digest,
)


def manifest() -> SnapshotManifest:
    entries = (
        SnapshotManifestEntry("deleted.py", False, None, 0, None),
        SnapshotManifestEntry("src/app.py", True, "a" * 64, 4, 0o644),
    )
    return SnapshotManifest(entries, manifest_digest(entries), 4)


def cursor(status: WorkspaceSnapshotStatus = WorkspaceSnapshotStatus.AVAILABLE) -> CheckpointCursor:
    return CheckpointCursor(
        0,
        0,
        ({"objective": "ship", "status": "active", "metadata": {"p": 1}},),
        {"objective": "repair", "verified_facts": ["one"]},
        {"model_name": "model", "tool_calls": 0},
        status,
    )


class WorkspaceSnapshotRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def create_lineage(self) -> WorkspaceLineageRecord:
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/task-one",
            head_commit="a" * 40,
        )
        await self.repository.create_lineage(lineage)
        return lineage

    async def test_snapshot_checkpoint_and_cursor_publish_atomically_and_round_trip(self) -> None:
        thread_id = await self.repository.create_thread()
        lineage = await self.create_lineage()
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, manifest())

        checkpoint_id = await self.repository.publish_workspace_checkpoint(
            thread_id, "paused", {"task_id": uuid.uuid4().hex}, snapshot, cursor()
        )
        reopened = SQLiteSessionRepository(self.database)

        self.assertEqual(await reopened.load_workspace_snapshot(checkpoint_id), snapshot)
        restored_cursor = await reopened.load_checkpoint_cursor(checkpoint_id)
        self.assertEqual(restored_cursor, cursor())
        checkpoints = await reopened.list_checkpoints(thread_id)
        self.assertEqual(checkpoints[0].metadata["snapshot_id"], snapshot.id)

    async def test_blob_is_metadata_only_and_need_not_exist(self) -> None:
        thread_id = await self.repository.create_thread()
        lineage = await self.create_lineage()
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, manifest())

        checkpoint_id = await self.repository.publish_workspace_checkpoint(
            thread_id, "captured", {}, snapshot, cursor()
        )

        self.assertEqual(
            (await self.repository.load_workspace_snapshot(checkpoint_id)).entries[1].blob_sha256,
            "a" * 64,
        )

    async def test_unavailable_checkpoint_has_no_snapshot(self) -> None:
        thread_id = await self.repository.create_thread()
        checkpoint_id = await self.repository.publish_workspace_checkpoint(
            thread_id,
            "limited",
            {"reason": "limit"},
            None,
            cursor(WorkspaceSnapshotStatus.UNAVAILABLE),
        )

        self.assertIsNone(await self.repository.load_workspace_snapshot(checkpoint_id))

    async def test_any_insert_failure_rolls_back_snapshot_checkpoint_and_cursor(self) -> None:
        thread_id = await self.repository.create_thread()
        lineage = await self.create_lineage()
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, manifest())
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TRIGGER reject_checkpoint_state
                BEFORE INSERT ON checkpoint_workspace_state
                BEGIN
                    SELECT RAISE(ABORT, 'reject state');
                END;
                """
            )

        with self.assertRaises(SessionStorageError):
            await self.repository.publish_workspace_checkpoint(
                thread_id, "paused", {}, snapshot, cursor()
            )

        with sqlite3.connect(self.database) as connection:
            counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "workspace_snapshots",
                    "workspace_snapshot_entries",
                    "checkpoints",
                    "checkpoint_workspace_state",
                )
            )
        self.assertEqual(counts, (0, 0, 0, 0))

    async def test_missing_and_corrupt_snapshot_fail_closed(self) -> None:
        with self.assertRaises(SessionNotFound):
            await self.repository.load_workspace_snapshot(uuid.uuid4().hex)

        thread_id = await self.repository.create_thread()
        lineage = await self.create_lineage()
        checkpoint_id = await self.repository.publish_workspace_checkpoint(
            thread_id,
            "paused",
            {},
            WorkspaceSnapshotRecord.create(lineage.id, manifest()),
            cursor(),
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE workspace_snapshot_entries SET blob_sha256 = 'broken'"
            )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.load_workspace_snapshot(checkpoint_id)

    async def test_foreign_keys_reject_cross_lineage_snapshot_reference(self) -> None:
        thread_id = await self.repository.create_thread()
        lineage = await self.create_lineage()
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, manifest())
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO workspace_snapshots VALUES (?, ?, ?, ?, ?)",
                    (snapshot.id, uuid.uuid4().hex, snapshot.inventory_digest, 4, "2026-01-01T00:00:00Z"),
                )
        self.assertEqual(await self.repository.list_checkpoints(thread_id), ())

    async def test_worktree_root_is_unique_and_owner_must_exist(self) -> None:
        lineage = await self.create_lineage()
        duplicate = WorkspaceLineageRecord.create(
            repository_id="repo-two",
            source_root="C:/source-two",
            worktree_root=lineage.worktree_root.swapcase(),
            branch_name="codex/task-two",
            head_commit="b" * 40,
        )
        with self.assertRaises(SessionStorageError):
            await self.repository.create_lineage(duplicate)
        missing_owner = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root="C:/managed/missing-owner",
            branch_name="codex/task-owner",
            head_commit="a" * 40,
            owner_task_id=uuid.uuid4().hex,
        )
        with self.assertRaises(SessionNotFound):
            await self.repository.create_lineage(missing_owner)

    async def test_concurrent_publish_writes_are_serialized_without_partial_rows(self) -> None:
        thread_id = await self.repository.create_thread()
        first = await self.create_lineage()
        second = await self.create_lineage()
        snapshots = (
            WorkspaceSnapshotRecord.create(first.id, manifest()),
            WorkspaceSnapshotRecord.create(second.id, manifest()),
        )

        checkpoint_ids = await asyncio.gather(
            *(
                self.repository.publish_workspace_checkpoint(
                    thread_id, f"checkpoint-{index}", {}, snapshot, cursor()
                )
                for index, snapshot in enumerate(snapshots)
            )
        )

        self.assertEqual(len(set(checkpoint_ids)), 2)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM workspace_snapshots").fetchone()[0],
                2,
            )

    async def test_corrupt_cursor_payload_fails_closed(self) -> None:
        thread_id = await self.repository.create_thread()
        checkpoint_id = await self.repository.publish_workspace_checkpoint(
            thread_id,
            "cursor",
            {},
            None,
            cursor(WorkspaceSnapshotStatus.UNAVAILABLE),
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE checkpoint_workspace_state SET goals_payload = '{broken'"
            )
        with self.assertRaises(SessionCorruptionError):
            await self.repository.load_checkpoint_cursor(checkpoint_id)


if __name__ == "__main__":
    unittest.main()
