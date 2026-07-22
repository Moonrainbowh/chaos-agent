from __future__ import annotations

import sys
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions.workspace_models import (  # noqa: E402
    CheckpointCursor,
    RewindMode,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceLineageStatus,
    WorkspaceSnapshotRecord,
    WorkspaceSnapshotStatus,
)
from code_agent.workspace._snapshot_manifest import (  # noqa: E402
    SnapshotManifest,
    SnapshotManifestEntry,
    manifest_digest,
)


def identifier() -> str:
    return uuid.uuid4().hex


def manifest() -> SnapshotManifest:
    entries = (
        SnapshotManifestEntry("gone.py", False, None, 0, None),
        SnapshotManifestEntry("src/app.py", True, "a" * 64, 3, 0o644),
    )
    return SnapshotManifest(entries, manifest_digest(entries), 3)


class WorkspaceRecordValidationTests(unittest.TestCase):
    def test_records_normalize_ids_and_utc_time(self) -> None:
        aware = datetime(2026, 7, 22, 8, tzinfo=timezone.utc)
        owner = identifier()
        lineage = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root="C:/managed",
            branch_name="codex/task-one",
            head_commit="a" * 40,
            owner_task_id=owner,
            now=aware,
        )
        snapshot = WorkspaceSnapshotRecord.create(lineage.id, manifest(), now=aware)
        cursor = CheckpointCursor(
            0,
            0,
            ({"objective": "goal", "status": "active", "metadata": {}},),
            {"objective": "repair"},
            {"model_name": "gpt", "tool_calls": 2},
            WorkspaceSnapshotStatus.AVAILABLE,
        )
        operation = RewindOperationRecord.create(
            lineage.id,
            identifier(),
            identifier(),
            RewindMode.CODE,
            "b" * 64,
            now=aware,
        )

        self.assertEqual(uuid.UUID(lineage.id).hex, lineage.id)
        self.assertEqual(snapshot.manifest, manifest())
        self.assertEqual(operation.status, RewindOperationStatus.PENDING)
        self.assertEqual(cursor.task_state_payload["objective"], "repair")
        self.assertEqual(lineage.status, WorkspaceLineageStatus.ACTIVE)

    def test_invalid_uuid_enum_time_path_digest_and_json_are_rejected(self) -> None:
        valid = WorkspaceLineageRecord.create(
            repository_id="repo",
            source_root="C:/source",
            worktree_root="C:/managed",
            branch_name="codex/task-one",
            head_commit="a" * 40,
        )
        cases = (
            lambda: replace(valid, id="not-a-uuid"),
            lambda: replace(valid, status="active"),
            lambda: replace(valid, created_at=datetime(2026, 1, 1)),
            lambda: replace(valid, source_root="relative/path"),
            lambda: replace(valid, head_commit="xyz"),
            lambda: WorkspaceSnapshotRecord.create(
                valid.id, replace(manifest(), inventory_digest="0" * 64)
            ),
            lambda: CheckpointCursor(
                0, 0, (), {"bad": object()}, {}, WorkspaceSnapshotStatus.UNAVAILABLE
            ),
        )
        for create in cases:
            with self.subTest(create=create):
                with self.assertRaises((TypeError, ValueError)):
                    create()

    def test_cursor_sequences_and_payload_limits_are_enforced(self) -> None:
        with self.assertRaises(ValueError):
            CheckpointCursor(-1, 0)
        with self.assertRaises(ValueError):
            CheckpointCursor(0, 0, tuple({"id": str(index)} for index in range(10_001)))


if __name__ == "__main__":
    unittest.main()
