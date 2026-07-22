from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Sequence

from code_agent.core._json import JSONValue
from code_agent.core.limits import TaskBudget
from code_agent.core.task_state import TaskState
from code_agent.workspace._snapshot_manifest import (
    SnapshotManifest,
    SnapshotManifestEntry,
)

from .models import GoalRecord
from ._workspace_model_values import (
    GIT_OID,
    MAX_GOALS,
    budget_payload,
    goal_payload,
    require_absolute_path,
    require_digest,
    require_json_mapping,
    require_json_size,
    require_text,
    require_utc,
    require_uuid,
    validate_snapshot_manifest,
)


class WorkspaceLineageStatus(str, Enum):
    ACTIVE = "active"
    RECOVERY_REQUIRED = "recovery_required"
    RELEASED = "released"


class WorkspaceSnapshotStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RewindMode(str, Enum):
    CODE = "code"
    SESSION = "session"
    CODE_AND_SESSION = "code_and_session"


class RewindOperationStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class WorkspaceLineageRecord:
    id: str
    repository_id: str
    source_root: str
    worktree_root: str
    branch_name: str
    head_commit: str
    owner_task_id: str | None = None
    status: WorkspaceLineageStatus = WorkspaceLineageStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "lineage id"))
        object.__setattr__(self, "repository_id", require_text(self.repository_id, "repository_id"))
        object.__setattr__(self, "source_root", require_absolute_path(self.source_root, "source_root"))
        object.__setattr__(self, "worktree_root", require_absolute_path(self.worktree_root, "worktree_root"))
        object.__setattr__(self, "branch_name", require_text(self.branch_name, "branch_name"))
        if not isinstance(self.head_commit, str) or not GIT_OID.fullmatch(self.head_commit):
            raise ValueError("head_commit must be a lowercase Git object id")
        if self.owner_task_id is not None:
            object.__setattr__(self, "owner_task_id", require_uuid(self.owner_task_id, "owner_task_id"))
        if not isinstance(self.status, WorkspaceLineageStatus):
            raise TypeError("status must be a WorkspaceLineageStatus")
        created = require_utc(self.created_at, "created_at")
        updated = require_utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)

    @classmethod
    def create(cls, *, now: datetime | None = None, **values: object) -> WorkspaceLineageRecord:
        timestamp = datetime.now(timezone.utc) if now is None else now
        return cls(id=uuid.uuid4().hex, created_at=timestamp, updated_at=timestamp, **values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class WorkspaceSnapshotRecord:
    id: str
    lineage_id: str
    entries: tuple[SnapshotManifestEntry, ...]
    inventory_digest: str
    total_bytes: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "snapshot id"))
        object.__setattr__(self, "lineage_id", require_uuid(self.lineage_id, "lineage_id"))
        if not isinstance(self.entries, tuple):
            raise TypeError("entries must be a tuple")
        manifest = SnapshotManifest(self.entries, self.inventory_digest, self.total_bytes)
        validate_snapshot_manifest(manifest)
        object.__setattr__(self, "created_at", require_utc(self.created_at, "created_at"))

    @property
    def manifest(self) -> SnapshotManifest:
        return SnapshotManifest(self.entries, self.inventory_digest, self.total_bytes)

    @classmethod
    def create(
        cls, lineage_id: str, manifest: SnapshotManifest, *, now: datetime | None = None
    ) -> WorkspaceSnapshotRecord:
        if not isinstance(manifest, SnapshotManifest):
            raise TypeError("manifest must be a SnapshotManifest")
        return cls(
            uuid.uuid4().hex,
            lineage_id,
            manifest.entries,
            manifest.inventory_digest,
            manifest.total_bytes,
            datetime.now(timezone.utc) if now is None else now,
        )


@dataclass(frozen=True)
class CheckpointCursor:
    message_sequence: int = 0
    event_sequence: int = 0
    goals_payload: tuple[Mapping[str, JSONValue], ...] = ()
    task_state_payload: Mapping[str, JSONValue] = field(default_factory=dict)
    budget_payload: Mapping[str, JSONValue] = field(default_factory=dict)
    snapshot_status: WorkspaceSnapshotStatus = WorkspaceSnapshotStatus.UNAVAILABLE
    lineage_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("message_sequence", "event_sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        goals = tuple(self.goals_payload)
        if len(goals) > MAX_GOALS:
            raise ValueError(f"goals_payload must contain at most {MAX_GOALS} records")
        frozen_goals = tuple(require_json_mapping(item, "goals_payload") for item in goals)
        state = require_json_mapping(self.task_state_payload, "task_state_payload")
        budget = require_json_mapping(self.budget_payload, "budget_payload")
        require_json_size((frozen_goals, state, budget))
        object.__setattr__(self, "goals_payload", frozen_goals)
        object.__setattr__(self, "task_state_payload", state)
        object.__setattr__(self, "budget_payload", budget)
        if not isinstance(self.snapshot_status, WorkspaceSnapshotStatus):
            raise TypeError("snapshot_status must be a WorkspaceSnapshotStatus")
        if self.lineage_id is not None:
            object.__setattr__(
                self, "lineage_id", require_uuid(self.lineage_id, "lineage_id")
            )

    @classmethod
    def from_records(
        cls,
        message_sequence: int,
        event_sequence: int,
        goals: Sequence[GoalRecord],
        task_state: TaskState,
        budget: TaskBudget,
        snapshot_status: WorkspaceSnapshotStatus,
        lineage_id: str | None = None,
    ) -> CheckpointCursor:
        if not isinstance(task_state, TaskState) or not isinstance(budget, TaskBudget):
            raise TypeError("task_state and budget must be typed records")
        goal_values = tuple(goal_payload(goal) for goal in goals)
        return cls(
            message_sequence,
            event_sequence,
            goal_values,
            task_state.to_dict(),
            budget_payload(budget),
            snapshot_status,
            lineage_id,
        )


@dataclass(frozen=True)
class RewindOperationRecord:
    id: str
    lineage_id: str
    source_checkpoint_id: str
    rollback_checkpoint_id: str | None
    mode: RewindMode
    preview_fingerprint: str
    status: RewindOperationStatus = RewindOperationStatus.PENDING
    error_code: str | None = None
    replacement_task_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in ("id", "lineage_id", "source_checkpoint_id"):
            object.__setattr__(self, name, require_uuid(getattr(self, name), name))
        for name in ("rollback_checkpoint_id", "replacement_task_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_uuid(value, name))
        if not isinstance(self.mode, RewindMode):
            raise TypeError("mode must be a RewindMode")
        require_digest(self.preview_fingerprint, "preview_fingerprint")
        if not isinstance(self.status, RewindOperationStatus):
            raise TypeError("status must be a RewindOperationStatus")
        if self.error_code is not None:
            object.__setattr__(self, "error_code", require_text(self.error_code, "error_code"))
        created = require_utc(self.created_at, "created_at")
        updated = require_utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)

    @classmethod
    def create(
        cls,
        lineage_id: str,
        source_checkpoint_id: str,
        rollback_checkpoint_id: str | None,
        mode: RewindMode,
        preview_fingerprint: str,
        *,
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> RewindOperationRecord:
        timestamp = datetime.now(timezone.utc) if now is None else now
        return cls(
            operation_id or uuid.uuid4().hex,
            lineage_id,
            source_checkpoint_id,
            rollback_checkpoint_id,
            mode,
            preview_fingerprint,
            created_at=timestamp,
            updated_at=timestamp,
        )
