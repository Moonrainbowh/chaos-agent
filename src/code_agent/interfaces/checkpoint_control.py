from __future__ import annotations

from typing import Protocol

from code_agent.checkpoints.models import (
    RewindConfirmationRequired,
    RewindPreview,
    RewindResult,
)
from code_agent.core._json import JSONValue, plain
from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import RewindMode


class CheckpointServicePort(Protocol):
    async def list(self, task_id: str) -> tuple[CheckpointRecord, ...]: ...

    async def capture(self, task_id: str, label: str) -> CheckpointRecord: ...


class RewindPort(Protocol):
    async def preview(
        self, task_id: str, checkpoint_id: str, mode: RewindMode
    ) -> RewindPreview: ...

    async def execute(
        self, preview: RewindPreview, *, confirmed: bool
    ) -> RewindResult: ...


class CheckpointControl:
    """Expose checkpoint orchestration without leaking concrete services."""

    def __init__(
        self, checkpoints: CheckpointServicePort, rewind: RewindPort
    ) -> None:
        self._checkpoints = checkpoints
        self._rewind = rewind

    async def list(self, task_id: str) -> tuple[CheckpointRecord, ...]:
        return await self._checkpoints.list(task_id)

    async def create(self, task_id: str, label: str) -> CheckpointRecord:
        return await self._checkpoints.capture(task_id, label)

    async def preview_rewind(
        self,
        task_id: str,
        checkpoint_id: str,
        mode: str = "code",
    ) -> RewindPreview:
        return await self._rewind.preview(
            task_id, checkpoint_id, RewindMode(mode)
        )

    async def execute_rewind(
        self, preview: RewindPreview, *, confirmed: bool
    ) -> RewindResult:
        if confirmed is not True:
            raise RewindConfirmationRequired("rewind requires confirmation")
        return await self._rewind.execute(preview, confirmed=True)


def checkpoint_record_to_json(record: CheckpointRecord) -> dict[str, JSONValue]:
    return {
        "id": record.id,
        "thread_id": record.thread_id,
        "label": record.label,
        "metadata": plain(record.metadata),
        "created_at": record.created_at.isoformat(),
    }


def rewind_preview_to_json(preview: RewindPreview) -> dict[str, JSONValue]:
    return {
        "operation_id": preview.operation_id,
        "task_id": preview.task_id,
        "lineage_id": preview.lineage_id,
        "checkpoint_id": preview.checkpoint_id,
        "mode": preview.mode.value,
        "fingerprint": preview.fingerprint,
        "restore_count": preview.restore_count,
        "delete_count": preview.delete_count,
        "total_bytes": preview.total_bytes,
        "paths": list(preview.paths),
        "code_available": preview.code_available,
    }


def rewind_result_to_json(result: RewindResult) -> dict[str, JSONValue]:
    return {
        "operation_id": result.operation_id,
        "task_id": result.task_id,
        "replacement_task_id": result.replacement_task_id,
        "status": result.status.value,
    }
