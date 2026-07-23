from __future__ import annotations

import uuid
from collections import OrderedDict

from code_agent.sessions.workspace_models import (
    RewindMode,
    RewindOperationStatus,
    WorkspaceSnapshotRecord,
)
from code_agent.workspace._snapshot_manifest import MaterializedSnapshot
from code_agent.workspace.inventory import WorkspaceInventory

from .models import (
    bounded_paths,
    RewindConfirmationRequired,
    RewindConflict,
    RewindError,
    RewindPreview,
    RewindRecoveryRequired,
    RewindResult,
    RewindUnavailable,
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
from .service import CheckpointService, _require_owned_lineage
from ._rewind_recovery import RewindEffects, RewindRecovery


_CODE_MODES = {RewindMode.CODE, RewindMode.CODE_AND_SESSION}
_SESSION_MODES = {RewindMode.SESSION, RewindMode.CODE_AND_SESSION}
_MAX_ISSUED_PREVIEWS = 128


class RewindCoordinator:
    """Preview and execute recoverable Rewind operations under lineage leases."""

    def __init__(
        self,
        sessions: CheckpointSessionsPort,
        workspace: CheckpointWorkspacePort,
        checkpoints: CheckpointService,
        quiesce: Quiesce,
        locks: LineageLocks | None = None,
        invalidate_cache: InvalidateCache = lambda _: None,
        invalidate_verification: InvalidateVerification = lambda *_: None,
        block_lineage: BlockLineage | None = None,
    ) -> None:
        self.sessions = sessions
        self.workspace = workspace
        self.checkpoints = checkpoints
        self.quiesce = quiesce
        self.locks = checkpoints.locks if locks is None else locks
        self.invalidate_cache = invalidate_cache
        self.invalidate_verification = invalidate_verification
        self.block_lineage = block_lineage or (lambda *_: None)
        self._issued: OrderedDict[str, RewindPreview] = OrderedDict()
        self.recovery = RewindRecovery(
            sessions, workspace, self.locks, quiesce, invalidate_cache,
            invalidate_verification, self.block_lineage,
        )

    async def preview(
        self, task_id: str, checkpoint_id: str, mode: RewindMode = RewindMode.CODE
    ) -> RewindPreview:
        if not isinstance(mode, RewindMode):
            raise TypeError("mode must be a RewindMode")
        lineage = await self.sessions.load_lineage_for_task(task_id)
        async with self.locks.for_lineage(lineage.id):
            task, checked, checkpoint, target = await self._context(
                task_id, checkpoint_id, lineage.id, require_clear=True
            )
            inventory = await self.workspace.inventory()
            materialized = await self._try_materialize(target)
            code_available = target is not None and materialized is not None
            if mode in _CODE_MODES and not code_available:
                raise RewindUnavailable("checkpoint code is unavailable")
            counts = _preview_counts(inventory, target if code_available else None)
            preview = RewindPreview(
                uuid.uuid4().hex,
                task.id,
                checked.id,
                checkpoint.id,
                mode,
                inventory.digest,
                counts[0],
                counts[1],
                counts[2],
                counts[3],
                code_available,
            )
            self._issued[preview.operation_id] = preview
            while len(self._issued) > _MAX_ISSUED_PREVIEWS:
                self._issued.popitem(last=False)
            return preview

    async def execute(
        self, preview: RewindPreview, *, confirmed: bool = False
    ) -> RewindResult:
        if not isinstance(preview, RewindPreview):
            raise TypeError("preview must be a RewindPreview")
        if confirmed is not True:
            raise RewindConfirmationRequired("rewind requires confirmation")
        await self.quiesce(preview.task_id)
        async with self.locks.for_lineage(preview.lineage_id):
            if self._issued.pop(preview.operation_id, None) != preview:
                raise RewindConflict("preview was not issued or was tampered")
            return await self._execute_locked(preview)

    async def _execute_locked(self, preview: RewindPreview) -> RewindResult:
        await self._context(
            preview.task_id, preview.checkpoint_id, preview.lineage_id,
            require_clear=True,
        )
        rollback = await self.checkpoints._capture_locked(
            preview.task_id, "pre-rewind", preview.lineage_id
        )
        await self._require_rollback_checkpoint(rollback.id)
        operation = await self.sessions.begin_rewind(preview, rollback.id)
        effects = RewindEffects()
        try:
            target = await self._require_untampered(preview)
            replacement = await self._apply(preview, target, effects)
            await self.recovery.invalidate(preview.task_id, replacement)
            if preview.mode in _SESSION_MODES:
                assert replacement is not None
                await self.sessions.complete_session_rewind(
                    operation.id, preview.task_id, replacement
                )
            else:
                await self.sessions.complete_rewind(operation.id)
        except Exception as error:
            self.recovery.block_lineage = self.block_lineage
            await self.recovery.compensate(
                preview, operation, rollback, effects, error
            )
            if isinstance(error, RewindError):
                raise
            raise RewindError("rewind execution failed") from error
        return RewindResult(
            operation.id, preview.task_id, effects.replacement_task_id,
            RewindOperationStatus.COMPLETED,
        )

    async def _require_untampered(
        self, preview: RewindPreview
    ) -> WorkspaceSnapshotRecord | None:
        _, _, _, target = await self._context(
            preview.task_id, preview.checkpoint_id, preview.lineage_id,
            require_clear=False,
        )
        current = await self.workspace.inventory()
        if current.digest != preview.fingerprint:
            raise RewindConflict("preview is stale")
        materialized = await self._try_materialize(target)
        available = target is not None and materialized is not None
        if preview.mode in _CODE_MODES and not available:
            raise RewindUnavailable("checkpoint code is unavailable")
        counts = _preview_counts(current, target if available else None)
        actual = (counts[0], counts[1], counts[2], counts[3], available)
        shown = (
            preview.restore_count, preview.delete_count, preview.total_bytes,
            preview.paths, preview.code_available,
        )
        if actual != shown:
            raise RewindConflict("preview facts were tampered")
        return target

    async def _apply(
        self,
        preview: RewindPreview,
        target: WorkspaceSnapshotRecord | None,
        effects: RewindEffects,
    ) -> str | None:
        if preview.mode in _CODE_MODES:
            assert target is not None
            materialized = await self._materialize(target)
            current = await self.workspace.inventory()
            effects.code_attempted = True
            await self.workspace.restore(preview.checkpoint_id, materialized, current.paths)
            await self._require_digest(target.inventory_digest)
        if preview.mode in _SESSION_MODES:
            effects.replacement_task_id = uuid.uuid4().hex
        return effects.replacement_task_id

    async def _require_rollback_checkpoint(self, checkpoint_id: str) -> None:
        rollback = await self.sessions.load_workspace_snapshot(checkpoint_id)
        if rollback is None:
            raise RewindUnavailable("pre-rewind checkpoint code is unavailable")
        await self._materialize(rollback)

    async def recover_pending(self) -> tuple[RewindResult, ...]:
        return await self.recovery.recover_pending()

    async def _require_digest(self, expected: str) -> None:
        current = await self.workspace.inventory()
        if current.digest != expected:
            raise RewindConflict("restored workspace digest does not match checkpoint")

    async def _materialize(
        self, snapshot: WorkspaceSnapshotRecord
    ) -> MaterializedSnapshot:
        try:
            return await self.workspace.materialize(snapshot)
        except Exception as error:
            raise RewindUnavailable("checkpoint blob integrity check failed") from error

    async def _try_materialize(
        self, snapshot: WorkspaceSnapshotRecord | None
    ) -> MaterializedSnapshot | None:
        if snapshot is None:
            return None
        try:
            return await self.workspace.materialize(snapshot)
        except Exception:
            return None

    async def _context(self, task_id, checkpoint_id, lineage_id, *, require_clear):
        task = await self.sessions.load_task(task_id)
        lineage = await self.sessions.load_lineage_for_task(task_id)
        _require_owned_lineage(lineage, lineage_id, task_id)
        cursor = await self.sessions.load_checkpoint_cursor(checkpoint_id)
        if cursor.lineage_id != lineage.id:
            raise RewindConflict("checkpoint belongs to another workspace lineage")
        checkpoints = await self.sessions.list_checkpoints(task.thread_id)
        checkpoint = next((item for item in checkpoints if item.id == checkpoint_id), None)
        if checkpoint is None:
            raise RewindConflict("checkpoint does not belong to the task")
        if require_clear and await self.sessions.pending_rewinds(lineage.id):
            raise RewindRecoveryRequired()
        target = await self.sessions.load_workspace_snapshot(checkpoint_id)
        return task, lineage, checkpoint, target

def _preview_counts(
    current: WorkspaceInventory, target: WorkspaceSnapshotRecord | None
) -> tuple[int, int, int, tuple[str, ...]]:
    if target is None:
        return 0, 0, 0, ()
    current_by_path = {entry.relative_path: entry for entry in current.entries}
    target_by_path = {entry.relative_path: entry for entry in target.entries if entry.existed}
    restore = tuple(
        path for path, entry in target_by_path.items()
        if path not in current_by_path
        or current_by_path[path].sha256 != entry.blob_sha256
        or current_by_path[path].mode != entry.mode
    )
    delete = tuple(path for path in current_by_path if path not in target_by_path)
    total = sum(target_by_path[path].size for path in restore)
    paths = bounded_paths(tuple(sorted((*restore, *delete))))
    return len(restore), len(delete), total, paths
