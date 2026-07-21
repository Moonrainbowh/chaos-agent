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

from code_agent.core.task import TaskAuthorization, TaskContract  # noqa: E402
from code_agent.sessions.errors import SessionNotFound  # noqa: E402
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


def snapshot_manifest(seed: str = "a") -> SnapshotManifest:
    entries = (SnapshotManifestEntry("app.py", True, seed * 64, 1, 0o644),)
    return SnapshotManifest(entries, manifest_digest(entries), 1)


class WorkspaceCheckpointInvariantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "sessions.sqlite3"
        self.repository = SQLiteSessionRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def owned_lineage(self) -> tuple[str, str, WorkspaceLineageRecord]:
        thread_id = await self.repository.create_thread()
        task = await self.repository.create_task(
            thread_id,
            TaskContract("repair", TaskAuthorization.local_workspace("C:/managed")),
        )
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name=f"codex/{uuid.uuid4().hex}",
            head_commit="a" * 40,
            owner_task_id=task.id,
        )
        await self.repository.create_lineage(lineage)
        return thread_id, task.id, lineage

    @staticmethod
    def cursor(lineage_id: str, available: bool = False) -> CheckpointCursor:
        status = (
            WorkspaceSnapshotStatus.AVAILABLE
            if available
            else WorkspaceSnapshotStatus.UNAVAILABLE
        )
        return CheckpointCursor(lineage_id=lineage_id, snapshot_status=status)

    def counts(self) -> tuple[int, int, int, int]:
        with sqlite3.connect(self.database) as connection:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "workspace_snapshots",
                    "workspace_snapshot_entries",
                    "checkpoints",
                    "checkpoint_workspace_state",
                )
            )

    async def assert_publish_rolls_back(
        self,
        thread_id: str,
        cursor: CheckpointCursor,
        snapshot: WorkspaceSnapshotRecord | None = None,
    ) -> None:
        before = self.counts()
        with self.assertRaises((SessionNotFound, ValueError)):
            await self.repository.publish_workspace_checkpoint(
                thread_id, "invalid", {}, snapshot, cursor
            )
        self.assertEqual(self.counts(), before)

    async def test_available_and_unavailable_require_current_active_owner(self) -> None:
        thread_id, _, lineage = await self.owned_lineage()
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, snapshot_manifest())

        available = await self.repository.publish_workspace_checkpoint(
            thread_id, "available", {}, snapshot, self.cursor(lineage.id, True)
        )
        unavailable = await self.repository.publish_workspace_checkpoint(
            thread_id, "unavailable", {}, None, self.cursor(lineage.id)
        )

        self.assertEqual((await self.repository.load_workspace_snapshot(available)).id, snapshot.id)
        self.assertIsNone(await self.repository.load_workspace_snapshot(unavailable))

    async def test_missing_unbound_recovery_and_stale_owner_all_roll_back(self) -> None:
        no_task = await self.repository.create_thread()
        unowned = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root=f"C:/managed/{uuid.uuid4().hex}",
            branch_name="codex/unowned",
            head_commit="a" * 40,
        )
        await self.repository.create_lineage(unowned)
        await self.assert_publish_rolls_back(no_task, self.cursor(unowned.id))

        unbound_thread = await self.repository.create_thread()
        await self.repository.create_task(
            unbound_thread,
            TaskContract("unbound", TaskAuthorization.local_workspace("C:/unbound")),
        )
        await self.assert_publish_rolls_back(unbound_thread, self.cursor(unowned.id))

        owner_thread, _, owned = await self.owned_lineage()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE workspace_lineages SET status = 'recovery_required' WHERE id = ?",
                (owned.id,),
            )
        await self.assert_publish_rolls_back(owner_thread, self.cursor(owned.id))

        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE workspace_lineages SET status = 'active' WHERE id = ?", (owned.id,)
            )
        replacement_thread = await self.repository.create_thread()
        replacement = await self.repository.create_task(
            replacement_thread,
            TaskContract("replacement", TaskAuthorization.local_workspace("C:/managed")),
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE tasks SET workspace_lineage_id = ? WHERE id = ?",
                (owned.id, replacement.id),
            )
        await self.assert_publish_rolls_back(replacement_thread, self.cursor(owned.id))

    async def test_cross_lineage_snapshot_rolls_back(self) -> None:
        thread_id, _, first = await self.owned_lineage()
        _, _, second = await self.owned_lineage()
        snapshot = WorkspaceSnapshotRecord.create(second.id, snapshot_manifest("b"))

        await self.assert_publish_rolls_back(
            thread_id, self.cursor(first.id, True), snapshot
        )

    async def test_same_owner_concurrent_publishes_are_serialized(self) -> None:
        thread_id, _, lineage = await self.owned_lineage()
        snapshots = tuple(
            WorkspaceSnapshotRecord.create(lineage.id, snapshot_manifest(seed))
            for seed in ("a", "b")
        )

        identifiers = await asyncio.gather(
            *(
                self.repository.publish_workspace_checkpoint(
                    thread_id, f"cp-{index}", {}, snapshot, self.cursor(lineage.id, True)
                )
                for index, snapshot in enumerate(snapshots)
            )
        )

        self.assertEqual(len(set(identifiers)), 2)
        self.assertEqual(self.counts(), (2, 2, 2, 2))


if __name__ == "__main__":
    unittest.main()
