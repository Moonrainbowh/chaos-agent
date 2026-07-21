from __future__ import annotations

import inspect
from dataclasses import dataclass

from code_agent.sessions.models import CheckpointRecord
from code_agent.sessions.workspace_models import (
    RewindOperationRecord,
    RewindOperationStatus,
)

from .models import (
    MAX_PREVIEW_PATHS,
    RewindError,
    RewindPreview,
    RewindRecoveryRequired,
    RewindResult,
)
from .ports import (
    BlockLineage,
    CheckpointSessionsPort,
    CheckpointWorkspacePort,
    InvalidateCache,
    InvalidateVerification,
    LineageLocks,
    Quiesce,
)


@dataclass
class RewindEffects:
    code_attempted: bool = False
    replacement_task_id: str | None = None
    owner_transferred: bool = False


class RewindRecovery:
    """Compensate failed operations and reconcile persisted pending intents."""

    def __init__(
        self,
        sessions: CheckpointSessionsPort,
        workspace: CheckpointWorkspacePort,
        locks: LineageLocks,
        quiesce: Quiesce,
        invalidate_cache: InvalidateCache,
        invalidate_verification: InvalidateVerification,
        block_lineage: BlockLineage,
    ) -> None:
        self.sessions = sessions
        self.workspace = workspace
        self.locks = locks
        self.quiesce = quiesce
        self.invalidate_cache = invalidate_cache
        self.invalidate_verification = invalidate_verification
        self.block_lineage = block_lineage

    async def compensate(
        self,
        preview: RewindPreview,
        operation: RewindOperationRecord,
        rollback: CheckpointRecord,
        effects: RewindEffects,
        error: Exception,
    ) -> None:
        paths: tuple[str, ...] = ()
        try:
            await self._restore_owner(preview, effects)
            if effects.code_attempted:
                paths = await self.restore_checkpoint(rollback.id)
            await self.sessions.fail_rewind(operation.id, error_code(error))
            if effects.code_attempted:
                await self.invalidate(preview.task_id, None)
        except Exception as rollback_error:
            paths = (
                paths
                or getattr(rollback_error, "paths", ())
                or preview.paths
            )
            await self.mark_required(
                operation.id, preview.lineage_id, paths, rollback_error
            )
            raise RewindRecoveryRequired(paths) from error

    async def _restore_owner(
        self, preview: RewindPreview, effects: RewindEffects
    ) -> None:
        if effects.owner_transferred and effects.replacement_task_id is not None:
            await self.sessions.transfer_lineage_owner(
                preview.lineage_id, effects.replacement_task_id, preview.task_id
            )

    async def recover_pending(self) -> tuple[RewindResult, ...]:
        results: list[RewindResult] = []
        for operation in await self.sessions.pending_rewinds():
            if operation.status is not RewindOperationStatus.PENDING:
                continue
            async with self.locks.for_lineage(operation.lineage_id):
                results.append(await self._recover_one(operation))
        return tuple(results)

    async def _recover_one(self, operation: RewindOperationRecord) -> RewindResult:
        task_id = await self._task_for_checkpoint(operation.source_checkpoint_id)
        paths: tuple[str, ...] = ()
        try:
            await self.quiesce(task_id)
            lineage = await self.sessions.load_lineage(operation.lineage_id)
            if lineage.owner_task_id != task_id:
                raise RewindError("pending rewind changed lineage owner")
            if operation.rollback_checkpoint_id is None:
                raise RewindError("pending rewind has no rollback checkpoint")
            paths = await self.restore_checkpoint(operation.rollback_checkpoint_id)
            await self.sessions.fail_rewind(operation.id, "crash_recovery")
            await self.invalidate(task_id, None)
            return RewindResult(
                operation.id, task_id, None, RewindOperationStatus.ROLLED_BACK
            )
        except Exception as error:
            paths = paths or getattr(error, "paths", ())
            await self.mark_required(
                operation.id, operation.lineage_id, paths, error
            )
            raise RewindRecoveryRequired(paths) from error

    async def restore_checkpoint(self, checkpoint_id: str) -> tuple[str, ...]:
        snapshot = await self.sessions.load_workspace_snapshot(checkpoint_id)
        if snapshot is None:
            raise RewindError("rollback checkpoint code is unavailable")
        paths = tuple(entry.relative_path for entry in snapshot.entries)[
            :MAX_PREVIEW_PATHS
        ]
        try:
            materialized = await self.workspace.materialize(snapshot)
            current = await self.workspace.inventory()
            await self.workspace.restore(checkpoint_id, materialized, current.paths)
            restored = await self.workspace.inventory()
            if restored.digest != snapshot.inventory_digest:
                raise RewindError("rollback workspace digest does not match checkpoint")
            return paths
        except Exception as error:
            raise RewindRecoveryRequired(paths) from error

    async def mark_required(
        self, operation_id: str, lineage_id: str, paths: tuple[str, ...], error: Exception
    ) -> None:
        persistence_error: Exception | None = None
        try:
            await self.sessions.fail_rewind(
                operation_id, error_code(error), recovery_required=True
            )
        except Exception as failure:
            persistence_error = failure
        await call(self.block_lineage, lineage_id, paths, error_code(error))
        if persistence_error is not None:
            raise persistence_error

    async def _task_for_checkpoint(self, checkpoint_id: str) -> str:
        list_tasks = getattr(self.sessions, "list_tasks", None)
        if list_tasks is None:
            raise RewindError("cannot resolve pending rewind owner")
        for task in await list_tasks(include_terminal=True):
            checkpoints = await self.sessions.list_checkpoints(task.thread_id)
            if any(checkpoint.id == checkpoint_id for checkpoint in checkpoints):
                return task.id
        raise RewindError("pending rewind source task is missing")

    async def invalidate(self, task_id: str, replacement: str | None) -> None:
        await call(self.invalidate_cache, ())
        await call(self.invalidate_verification, task_id, replacement)


def error_code(error: Exception) -> str:
    return error.__class__.__name__[:128]


async def call(callback, *args: object) -> None:
    result = callback(*args)
    if inspect.isawaitable(result):
        await result
