from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from ._edit_plan import EditPlan


@dataclass(frozen=True)
class PlannedPathState:
    """Record exact-name and filesystem-lookup facts at planning time."""

    relative_path: str
    existed: bool
    sha256: str | None
    size: int
    lookup_existed: bool
    lookup_sha256: str | None
    lookup_size: int
    aliases_source: bool = False

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("relative_path must be non-empty")
        if self.size < 0 or self.lookup_size < 0:
            raise ValueError("path sizes cannot be negative")
        if self.existed != (self.sha256 is not None):
            raise ValueError("exact existence and SHA-256 must agree")
        if self.lookup_existed != (self.lookup_sha256 is not None):
            raise ValueError("lookup existence and SHA-256 must agree")
        if not self.existed and self.size != 0:
            raise ValueError("missing exact paths must have zero size")
        if not self.lookup_existed and self.lookup_size != 0:
            raise ValueError("missing lookup paths must have zero size")
        if self.existed and not self.lookup_existed:
            raise ValueError("an exact path must also exist by lookup")


@dataclass(frozen=True)
class DeletePlan:
    source: PlannedPathState
    diff: str

    def __post_init__(self) -> None:
        if not self.source.existed:
            raise ValueError("delete source must exist")


@dataclass(frozen=True)
class MovePlan:
    source: PlannedPathState
    destination: PlannedPathState
    diff: str
    case_only: bool = False

    def __post_init__(self) -> None:
        if not self.source.existed:
            raise ValueError("move source must exist")
        if self.destination.existed:
            raise ValueError("move destination must be absent")
        if self.destination.lookup_existed and not self.destination.aliases_source:
            raise ValueError("move destination lookup is occupied")
        if self.destination.aliases_source and not self.case_only:
            raise ValueError("only case-only moves may alias their source")


BatchOperation: TypeAlias = EditPlan | DeletePlan | MovePlan


@dataclass(frozen=True)
class BatchEditPlan:
    schema_version: int
    plan_id: str
    workspace_identity: tuple[int, int]
    operations: tuple[BatchOperation, ...]
    paths: tuple[PlannedPathState, ...]
    diff: str


class BatchApplyStatus(str, Enum):
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    PARTIAL_CONFLICT = "partial_conflict"


@dataclass(frozen=True)
class BatchConflict:
    relative_path: str
    reason: str


@dataclass(frozen=True)
class BatchApplyResult:
    status: BatchApplyStatus
    applied_operations: tuple[int, ...] = ()
    rolled_back_operations: tuple[int, ...] = ()
    conflicts: tuple[BatchConflict, ...] = ()
    error: str | None = None


class RecoveryOperationKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass(frozen=True)
class RecoveryPathState:
    relative_path: str
    existed: bool
    sha256: str | None
    size: int

    def __post_init__(self) -> None:
        if not self.relative_path or self.size < 0:
            raise ValueError("invalid recovery path state")
        if self.existed != (self.sha256 is not None):
            raise ValueError("recovery existence and SHA-256 must agree")
        if not self.existed and self.size != 0:
            raise ValueError("missing recovery paths must have zero size")


@dataclass(frozen=True)
class PathTransition:
    before: RecoveryPathState
    after: RecoveryPathState

    def __post_init__(self) -> None:
        if self.before.relative_path != self.after.relative_path:
            raise ValueError("transition paths must match")


@dataclass(frozen=True)
class RecoveryOperation:
    kind: RecoveryOperationKind
    source: PathTransition
    destination: PathTransition | None = None
    case_only: bool = False

    def __post_init__(self) -> None:
        if type(self.kind) is not RecoveryOperationKind:
            raise TypeError("kind must be a RecoveryOperationKind")
        before = self.source.before
        after = self.source.after
        if self.kind is RecoveryOperationKind.CREATE:
            valid = not before.existed and after.existed
        elif self.kind is RecoveryOperationKind.UPDATE:
            valid = before.existed and after.existed
        elif self.kind is RecoveryOperationKind.DELETE:
            valid = before.existed and not after.existed
        else:
            valid = before.existed and not after.existed
        if not valid:
            raise ValueError("recovery source transition does not match its kind")
        if self.kind is not RecoveryOperationKind.MOVE:
            if self.destination is not None or self.case_only:
                raise ValueError("only move recovery may have a destination")
            return
        if self.destination is None:
            raise ValueError("move recovery requires a destination")
        target = self.destination
        if target.before.existed or not target.after.existed:
            raise ValueError("move destination transition must be absent to existing")
        if (
            before.sha256 != target.after.sha256
            or before.size != target.after.size
        ):
            raise ValueError("move recovery hashes and sizes must agree")
