from __future__ import annotations

from typing import Mapping

from code_agent.core._json import JSONValue
from code_agent.core.events import AgentEvent, EventKind
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import (
    CheckpointCursor,
    WorkspaceLineageRecord,
    WorkspaceLineageStatus,
    WorkspaceSnapshotRecord,
    WorkspaceSnapshotStatus,
)
from code_agent.workspace.errors import (
    FileTooLargeError,
    SearchTimeoutError,
    WorkspaceScanLimitError,
)

from .models import CheckpointError
from .locks import LineageLockPool
from .ports import (
    CheckpointSessionsPort,
    CheckpointWorkspacePort,
    LineageLocks,
    Quiesce,
)


_CAPTURE_LIMITS = (FileTooLargeError, SearchTimeoutError, WorkspaceScanLimitError)


class CheckpointService:
    """Capture one quiescent owned lineage and atomically publish its facts."""

    def __init__(
        self,
        sessions: CheckpointSessionsPort,
        workspace: CheckpointWorkspacePort,
        quiesce: Quiesce,
        locks: LineageLocks | None = None,
    ) -> None:
        self.sessions = sessions
        self.workspace = workspace
        self.quiesce = quiesce
        self.locks = locks or LineageLockPool()

    async def capture(self, task_id: str, label: str) -> CheckpointRecord:
        lineage = await self.sessions.load_lineage_for_task(task_id)
        await self.quiesce(task_id)
        async with self.locks.for_lineage(lineage.id):
            return await self._capture_locked(task_id, label, lineage.id)

    async def _capture_locked(
        self, task_id: str, label: str, lineage_id: str
    ) -> CheckpointRecord:
        """Capture while the caller already owns the lineage lock."""
        task = await self.sessions.load_task(task_id)
        lineage = await self.sessions.load_lineage_for_task(task_id)
        _require_owned_lineage(lineage, lineage_id, task_id)
        snapshot, status, digest, count, total = await self._workspace_snapshot(lineage)
        cursor = await self._cursor(task_id, task.thread_id, lineage.id, status)
        metadata: dict[str, JSONValue] = {
            "task_id": task_id,
            "lineage_id": lineage.id,
            "inventory_digest": digest,
            "file_count": count,
            "total_bytes": total,
        }
        checkpoint_id = await self.sessions.publish_workspace_checkpoint(
            task.thread_id, label, metadata, snapshot, cursor
        )
        await self._publish_event(task.thread_id, checkpoint_id, label, status)
        return await self._checkpoint(task.thread_id, checkpoint_id)

    async def _workspace_snapshot(
        self, lineage: WorkspaceLineageRecord
    ) -> tuple[WorkspaceSnapshotRecord | None, WorkspaceSnapshotStatus, str, int, int]:
        try:
            inventory = await self.workspace.inventory()
            raw = await self.workspace.snapshot(inventory.paths)
            modes: Mapping[str, int] = {
                entry.relative_path: entry.mode for entry in inventory.entries
            }
            manifest = await self.workspace.store(raw, modes)
        except _CAPTURE_LIMITS:
            return None, WorkspaceSnapshotStatus.UNAVAILABLE, "", 0, 0
        record = WorkspaceSnapshotRecord.create(lineage.id, manifest)
        return (
            record,
            WorkspaceSnapshotStatus.AVAILABLE,
            inventory.digest,
            len(inventory.entries),
            manifest.total_bytes,
        )

    async def _cursor(
        self,
        task_id: str,
        thread_id: str,
        lineage_id: str,
        status: WorkspaceSnapshotStatus,
    ) -> CheckpointCursor:
        messages = await self.sessions.load_message_records(thread_id)
        message_sequence = 0 if not messages else messages[-1].sequence
        return CheckpointCursor.from_records(
            message_sequence,
            await self.sessions.latest_event_sequence(thread_id),
            await self.sessions.list_goals(thread_id),
            await self.sessions.load_task_state(thread_id),
            await self.sessions.load_task_budget(task_id),
            status,
            lineage_id,
        )

    async def _publish_event(
        self,
        thread_id: str,
        checkpoint_id: str,
        label: str,
        status: WorkspaceSnapshotStatus,
    ) -> None:
        append = getattr(self.sessions, "append_event", None)
        if append is None:
            return
        event = AgentEvent(
            EventKind.TASK_CHECKPOINT_CREATED,
            {
                "checkpoint_id": checkpoint_id,
                "label": label,
                "snapshot_status": status.value,
            },
        )
        await append(thread_id, event)

    async def _checkpoint(self, thread_id: str, checkpoint_id: str) -> CheckpointRecord:
        records = await self.sessions.list_checkpoints(thread_id)
        for record in records:
            if record.id == checkpoint_id:
                return record
        raise CheckpointError("published checkpoint is not readable")


def _require_owned_lineage(
    lineage: WorkspaceLineageRecord, expected_lineage_id: str, task_id: str
) -> None:
    if lineage.id != expected_lineage_id:
        raise CheckpointError("task workspace lineage changed")
    if lineage.status is not WorkspaceLineageStatus.ACTIVE:
        raise CheckpointError("workspace lineage is recovery guarded")
    if lineage.owner_task_id != task_id:
        raise CheckpointError("task does not own the workspace lineage")
