from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ._batch_models import (
    BatchApplyResult,
    BatchEditPlan,
    BatchOperation,
    DeletePlan,
    MovePlan,
    RecoveryOperation,
)
from ._batch_plan import plan_batch, plan_delete, plan_move
from .paths import PathInput

if TYPE_CHECKING:
    from ._batch_apply import PreparedBatchEdit
    from .edits import WorkspaceSnapshot


class BatchWorkspaceEditorMixin:
    """Thin public facade for exact multi-file planning and application."""

    def plan_delete(
        self,
        path: PathInput,
        *,
        expected_sha256: str | None = None,
    ) -> DeletePlan:
        return plan_delete(self, path, expected_sha256=expected_sha256)

    def plan_move(
        self,
        source: PathInput,
        destination: PathInput,
        *,
        expected_source_sha256: str | None = None,
    ) -> MovePlan:
        return plan_move(
            self,
            source,
            destination,
            expected_source_sha256=expected_source_sha256,
        )

    def plan_batch(
        self, operations: Iterable[BatchOperation]
    ) -> BatchEditPlan:
        return plan_batch(self, operations)

    def preflight_batch(self, plan: BatchEditPlan) -> PreparedBatchEdit:
        from ._batch_apply import preflight_batch

        return preflight_batch(self, plan)

    def apply_batch(self, plan: BatchEditPlan) -> BatchApplyResult:
        from ._batch_apply import apply_batch

        return apply_batch(self, plan)

    def snapshot_from_prepared(
        self, prepared: PreparedBatchEdit
    ) -> WorkspaceSnapshot:
        from ._batch_recovery import snapshot_from_prepared

        return snapshot_from_prepared(prepared)

    def recovery_operations_from_prepared(
        self, prepared: PreparedBatchEdit
    ) -> tuple[RecoveryOperation, ...]:
        from ._batch_recovery import recovery_operations_from_prepared

        return recovery_operations_from_prepared(prepared)

    def recover_batch(
        self,
        operations: tuple[RecoveryOperation, ...],
        snapshot: WorkspaceSnapshot,
    ) -> BatchApplyResult:
        from ._batch_recovery import recover_batch

        return recover_batch(self, operations, snapshot)
