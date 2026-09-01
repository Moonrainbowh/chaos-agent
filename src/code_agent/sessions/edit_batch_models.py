from __future__ import annotations

import ntpath
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ._edit_batch_validation import validate_prepare_path_facts
from ._rewind_model_base import (
    MAX_REWIND_ACTION_PATHS,
    bounded_int,
    canonical_path,
    optional_text,
    required_text,
    sha256,
    utc_datetime,
)
from ._rewind_model_records import RewindMutationRecord
from .rewind_models import RewindMutationPrepare, RewindMutationStatus


class EditBatchOperationKind(str, Enum):
    WRITE = "write"
    CREATE = "create"
    DELETE = "delete"
    MOVE = "move"


class EditBatchOperationProgress(str, Enum):
    PENDING = "pending"
    COMMITTED = "committed"


class EditBatchState(str, Enum):
    PREPARED = "prepared"
    APPLYING = "applying"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"
    CONFLICTED = "conflicted"


UNRESOLVED_EDIT_BATCH_STATES = frozenset(
    {
        EditBatchState.PREPARED,
        EditBatchState.APPLYING,
        EditBatchState.ROLLING_BACK,
        EditBatchState.CONFLICTED,
    }
)


@dataclass(frozen=True)
class EditBatchPath:
    path: str
    before_existed: bool
    before_sha256: str | None
    before_size: int
    after_existed: bool
    after_sha256: str | None
    after_size: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonical_path(self.path))
        for name in ("before_existed", "after_existed"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        before = sha256(self.before_sha256, "before_sha256", optional=True)
        after = sha256(self.after_sha256, "after_sha256", optional=True)
        before_size = bounded_int(self.before_size, "before_size")
        after_size = bounded_int(self.after_size, "after_size")
        if self.before_existed != (before is not None) or (
            not self.before_existed and before_size != 0
        ):
            raise ValueError("before existence, hash, and size must agree")
        if self.after_existed != (after is not None) or (
            not self.after_existed and after_size != 0
        ):
            raise ValueError("after existence, hash, and size must agree")


@dataclass(frozen=True)
class EditBatchOperation:
    kind: EditBatchOperationKind
    source: EditBatchPath | None
    target: EditBatchPath
    case_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EditBatchOperationKind):
            raise TypeError("kind must be an EditBatchOperationKind")
        if self.source is not None and not isinstance(self.source, EditBatchPath):
            raise TypeError("source must be an EditBatchPath or None")
        if not isinstance(self.target, EditBatchPath):
            raise TypeError("target must be an EditBatchPath")
        if not isinstance(self.case_only, bool):
            raise TypeError("case_only must be a bool")
        if self.kind is EditBatchOperationKind.MOVE:
            self._validate_move()
        else:
            self._validate_single_path()

    def _validate_move(self) -> None:
        source = self.source
        if source is None:
            raise ValueError("move requires a source endpoint")
        if source.path == self.target.path:
            raise ValueError("move endpoints must be distinct")
        same_identity = ntpath.normcase(source.path) == ntpath.normcase(
            self.target.path
        )
        if self.case_only != same_identity:
            raise ValueError("case_only must match endpoint identity")
        if not source.before_existed or source.after_existed:
            raise ValueError("move source must change from existing to absent")
        if self.target.before_existed or not self.target.after_existed:
            raise ValueError("move target must change from absent to existing")
        if (
            source.before_sha256 != self.target.after_sha256
            or source.before_size != self.target.after_size
        ):
            raise ValueError("move must preserve source bytes and size")

    def _validate_single_path(self) -> None:
        if self.source is not None:
            raise ValueError("non-move operations cannot have a source")
        if self.case_only:
            raise ValueError("case_only is supported only for moves")
        before, after = self.target.before_existed, self.target.after_existed
        expected = {
            EditBatchOperationKind.WRITE: (True, True),
            EditBatchOperationKind.CREATE: (False, True),
            EditBatchOperationKind.DELETE: (True, False),
        }[self.kind]
        if (before, after) != expected:
            raise ValueError(f"{self.kind.value} endpoint states are invalid")


@dataclass(frozen=True)
class EditBatchPrepare:
    mutation: RewindMutationPrepare
    plan_id: str
    plan_digest: str
    operations: tuple[EditBatchOperation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.mutation, RewindMutationPrepare):
            raise TypeError("mutation must be a RewindMutationPrepare")
        object.__setattr__(self, "plan_id", required_text(self.plan_id, "plan_id"))
        object.__setattr__(
            self, "plan_digest", sha256(self.plan_digest, "plan_digest")
        )
        if type(self.operations) is not tuple:
            raise TypeError("operations must be a tuple")
        if not self.operations or len(self.operations) > MAX_REWIND_ACTION_PATHS:
            raise ValueError("operations count is outside its supported range")
        if any(not isinstance(item, EditBatchOperation) for item in self.operations):
            raise TypeError("operations must contain EditBatchOperation values")
        validate_prepare_path_facts(self.mutation, self.operations)


@dataclass(frozen=True)
class EditBatchOperationRecord:
    ordinal: int
    operation: EditBatchOperation
    progress: EditBatchOperationProgress
    committed_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        if not isinstance(self.operation, EditBatchOperation):
            raise TypeError("operation must be an EditBatchOperation")
        if not isinstance(self.progress, EditBatchOperationProgress):
            raise TypeError("progress must be an EditBatchOperationProgress")
        committed = (
            None
            if self.committed_at is None
            else utc_datetime(self.committed_at, "committed_at")
        )
        if (self.progress is EditBatchOperationProgress.COMMITTED) != (
            committed is not None
        ):
            raise ValueError("operation progress and committed_at must agree")
        object.__setattr__(self, "committed_at", committed)


@dataclass(frozen=True)
class EditBatchRecord:
    mutation: RewindMutationRecord
    plan_id: str
    plan_digest: str
    state: EditBatchState
    conflict_code: str | None
    operations: tuple[EditBatchOperationRecord, ...]
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.mutation, RewindMutationRecord):
            raise TypeError("mutation must be a RewindMutationRecord")
        object.__setattr__(self, "plan_id", required_text(self.plan_id, "plan_id"))
        object.__setattr__(
            self, "plan_digest", sha256(self.plan_digest, "plan_digest")
        )
        if not isinstance(self.state, EditBatchState):
            raise TypeError("state must be an EditBatchState")
        conflict = optional_text(self.conflict_code, "conflict_code")
        created = utc_datetime(self.created_at, "created_at")
        updated = utc_datetime(self.updated_at, "updated_at")
        settled = (
            None
            if self.settled_at is None
            else utc_datetime(self.settled_at, "settled_at")
        )
        if updated < created or (settled is not None and settled < created):
            raise ValueError("batch timestamps are out of order")
        self._validate_operations()
        self._validate_state(conflict, settled)
        object.__setattr__(self, "conflict_code", conflict)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "settled_at", settled)

    def _validate_operations(self) -> None:
        if type(self.operations) is not tuple or not self.operations:
            raise TypeError("operations must be a non-empty tuple")
        if any(not isinstance(item, EditBatchOperationRecord) for item in self.operations):
            raise TypeError("operations must contain EditBatchOperationRecord values")
        if tuple(item.ordinal for item in self.operations) != tuple(
            range(len(self.operations))
        ):
            raise ValueError("operation ordinals must be contiguous")
        pending_seen = False
        for item in self.operations:
            pending_seen = pending_seen or item.progress is EditBatchOperationProgress.PENDING
            if pending_seen and item.progress is EditBatchOperationProgress.COMMITTED:
                raise ValueError("operation progress must be committed in order")

    def _validate_state(self, conflict: str | None, settled: datetime | None) -> None:
        terminal = self.state in {
            EditBatchState.COMPLETED,
            EditBatchState.ROLLED_BACK,
            EditBatchState.CONFLICTED,
        }
        if terminal != (settled is not None):
            raise ValueError("batch state and settled_at must agree")
        if (self.state is EditBatchState.CONFLICTED) != (conflict is not None):
            raise ValueError("conflict state and code must agree")
        expected = (
            RewindMutationStatus.COMPLETED
            if self.state is EditBatchState.COMPLETED
            else RewindMutationStatus.ABORTED
            if terminal
            else RewindMutationStatus.PREPARED
        )
        if self.mutation.status is not expected:
            raise ValueError("batch state and parent mutation status must agree")
        if self.state is EditBatchState.COMPLETED and any(
            item.progress is not EditBatchOperationProgress.COMMITTED
            for item in self.operations
        ):
            raise ValueError("completed batch requires committed operations")


__all__ = [
    "EditBatchOperation",
    "EditBatchOperationKind",
    "EditBatchOperationProgress",
    "EditBatchOperationRecord",
    "EditBatchPath",
    "EditBatchPrepare",
    "EditBatchRecord",
    "EditBatchState",
    "UNRESOLVED_EDIT_BATCH_STATES",
]
