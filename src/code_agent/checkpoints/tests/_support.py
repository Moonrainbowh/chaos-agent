from __future__ import annotations

import uuid
from dataclasses import replace

from code_agent.core.limits import EngineLimits, TaskBudget
from code_agent.core.task import TaskAuthorization, TaskContract, TaskRecord, TaskStatus
from code_agent.core.task_state import TaskState
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import (
    CheckpointCursor,
    RewindOperationRecord,
    RewindOperationStatus,
    WorkspaceLineageRecord,
    WorkspaceLineageStatus,
    WorkspaceSnapshotRecord,
)
from code_agent.workspace._snapshot_manifest import MaterializedSnapshot
from code_agent.workspace.edits import SnapshotEntry, WorkspaceSnapshot
from code_agent.workspace.inventory import InventoryEntry, WorkspaceInventory


def identifier() -> str:
    return uuid.uuid4().hex


class ImmediateLease:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeLocks:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def for_lineage(self, lineage_id: str) -> ImmediateLease:
        self.requested.append(lineage_id)
        return ImmediateLease()


class FakeWorkspace:
    def __init__(self) -> None:
        self.files = {"app.py": b"current"}
        self.snapshots: dict[str, WorkspaceSnapshotRecord] = {}
        self.restore_calls: list[str] = []
        self.fail_restore_at: int | None = None
        self.corrupt: set[str] = set()
        self.order: list[str] = []
        self.content_by_manifest: dict[str, WorkspaceSnapshot] = {}
        self.inventory_failure: Exception | None = None

    async def inventory(self) -> WorkspaceInventory:
        import hashlib

        self.order.append("inventory")
        if self.inventory_failure is not None:
            raise self.inventory_failure
        entries = tuple(
            InventoryEntry(path, len(data), hashlib.sha256(data).hexdigest(), 0o644)
            for path, data in sorted(self.files.items())
        )
        from code_agent.workspace.inventory import _manifest_digest

        return WorkspaceInventory(entries, _manifest_digest(entries))

    async def snapshot(self, paths: tuple[str, ...]) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            tuple(SnapshotEntry(path, self.files[path], True) for path in paths)
        )

    async def store(self, snapshot: WorkspaceSnapshot, modes: dict[str, int]):
        from code_agent.workspace._snapshot_manifest import prepare_manifest

        manifest = prepare_manifest(snapshot, modes, 100, 10_000, 10_000)[0]
        self.content_by_manifest[manifest.inventory_digest] = snapshot
        return manifest

    async def materialize(self, record: WorkspaceSnapshotRecord) -> MaterializedSnapshot:
        if record.id in self.corrupt:
            raise OSError("corrupt blob")
        return MaterializedSnapshot(
            self.content_by_manifest[record.inventory_digest],
            {entry.relative_path: entry.mode for entry in record.entries if entry.existed},
        )

    async def restore(
        self, snapshot_id: str, materialized: MaterializedSnapshot, current_paths: tuple[str, ...]
    ) -> None:
        self.restore_calls.append(snapshot_id)
        if self.fail_restore_at == len(self.restore_calls):
            raise OSError("restore failed")
        self.files = {
            entry.relative_path: entry.content
            for entry in materialized.snapshot.entries
            if entry.existed and entry.content is not None
        }


class FakeSessions:
    def __init__(self, workspace: FakeWorkspace) -> None:
        self.workspace = workspace
        self.task = TaskRecord(identifier(), identifier(), TaskContract(
            "repair", TaskAuthorization.local_workspace("C:/managed")
        )).transition(TaskStatus.PAUSED)
        self.lineage = WorkspaceLineageRecord.create(
            repository_id="repo", source_root="C:/source", worktree_root="C:/managed",
            branch_name="codex/task", head_commit="a" * 40, owner_task_id=self.task.id,
        )
        self.checkpoints: dict[str, CheckpointRecord] = {}
        self.cursors: dict[str, CheckpointCursor] = {}
        self.operations: dict[str, RewindOperationRecord] = {}
        self.transitions: list[tuple[str, TaskStatus]] = []
        self.fail_fork = False
        self.fail_complete = False
        self.replacements: dict[str, TaskRecord] = {}

    async def load_task(self, task_id: str) -> TaskRecord:
        if task_id == self.task.id:
            return self.task
        return self.replacements[task_id]

    async def load_lineage_for_task(self, task_id: str) -> WorkspaceLineageRecord:
        return self.lineage

    async def load_lineage(self, lineage_id: str) -> WorkspaceLineageRecord:
        return self.lineage

    async def load_message_records(self, thread_id: str) -> tuple[()]:
        return ()

    async def latest_event_sequence(self, thread_id: str) -> int:
        return 0

    async def list_goals(self, thread_id: str) -> tuple[()]:
        return ()

    async def load_task_state(self, thread_id: str) -> TaskState:
        return TaskState.empty()

    async def load_task_budget(self, task_id: str) -> TaskBudget:
        return TaskBudget("test", EngineLimits())

    async def publish_workspace_checkpoint(self, thread_id, label, metadata, snapshot, cursor):
        checkpoint_id = identifier()
        record = CheckpointRecord(checkpoint_id, thread_id, label, metadata)
        self.checkpoints[checkpoint_id] = record
        self.cursors[checkpoint_id] = cursor
        if snapshot is not None:
            self.workspace.snapshots[checkpoint_id] = snapshot
        return checkpoint_id

    async def list_checkpoints(self, thread_id: str):
        return tuple(cp for cp in self.checkpoints.values() if cp.thread_id == thread_id)

    async def load_checkpoint_cursor(self, checkpoint_id: str) -> CheckpointCursor:
        return self.cursors[checkpoint_id]

    async def load_workspace_snapshot(self, checkpoint_id: str):
        return self.workspace.snapshots.get(checkpoint_id)

    async def begin_rewind(self, preview, rollback_checkpoint_id):
        record = RewindOperationRecord.create(
            preview.lineage_id, preview.checkpoint_id, rollback_checkpoint_id,
            preview.mode, preview.fingerprint, operation_id=preview.operation_id,
        )
        self.operations[record.id] = record
        return record

    async def fork_task_from_checkpoint(self, checkpoint_id: str) -> TaskRecord:
        if self.fail_fork:
            raise RuntimeError("fork failed")
        replacement = TaskRecord(identifier(), identifier(), self.task.contract)
        self.replacements[replacement.id] = replacement
        return replacement

    async def transfer_lineage_owner(self, lineage_id: str, expected: str, new: str):
        if self.lineage.owner_task_id != expected:
            raise ValueError("owner changed")
        self.lineage = replace(self.lineage, owner_task_id=new)
        return self.lineage

    async def transition_task(self, task_id: str, status: TaskStatus, reason=None):
        self.transitions.append((task_id, status))
        if task_id == self.task.id:
            self.task = self.task.transition(status, reason)
            return self.task
        self.replacements[task_id] = self.replacements[task_id].transition(status, reason)
        return self.replacements[task_id]

    async def complete_rewind(self, operation_id: str, replacement_task_id=None):
        if self.fail_complete:
            raise RuntimeError("complete failed")
        record = replace(
            self.operations[operation_id], status=RewindOperationStatus.COMPLETED,
            replacement_task_id=replacement_task_id,
        )
        self.operations[operation_id] = record
        return record

    async def fail_rewind(self, operation_id: str, error_code: str, *, recovery_required=False):
        status = (RewindOperationStatus.RECOVERY_REQUIRED if recovery_required
                  else RewindOperationStatus.ROLLED_BACK)
        record = replace(self.operations[operation_id], status=status, error_code=error_code)
        self.operations[operation_id] = record
        if recovery_required:
            self.lineage = replace(self.lineage, status=WorkspaceLineageStatus.RECOVERY_REQUIRED)
        return record

    async def pending_rewinds(self, lineage_id: str | None = None):
        return tuple(
            op for op in self.operations.values()
            if op.status is RewindOperationStatus.PENDING
            and (lineage_id is None or op.lineage_id == lineage_id)
        )

    async def list_tasks(self, *, include_terminal: bool = False):
        tasks = (self.task, *self.replacements.values())
        return tasks if include_terminal else tuple(
            task for task in tasks if task.status not in {
                TaskStatus.COMPLETED, TaskStatus.ACCEPTED_PARTIAL,
                TaskStatus.FAILED, TaskStatus.SUPERSEDED,
            }
        )
