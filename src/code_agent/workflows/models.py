from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class WorkflowStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING_DECISION = "waiting_decision"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowEdgeKind(str, Enum):
    REQUIRES = "requires"
    PRODUCES = "produces"
    REVIEW_OF = "review_of"


@dataclass(frozen=True)
class Workflow:
    id: str
    root_thread_id: str
    task_id: str
    title: str
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("id", "root_thread_id", "task_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 256))
        object.__setattr__(self, "title", _text(self.title, "title", 512))
        if not isinstance(self.status, WorkflowStatus):
            raise TypeError("status must be WorkflowStatus")
        created = _utc(self.created_at, "created_at")
        updated = created if self.updated_at is None else _utc(self.updated_at, "updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    workflow_id: str
    kind: str
    title: str
    status: WorkflowNodeStatus
    assigned_thread_id: str | None = None
    role: str = "main"
    input_refs: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    git_checkpoint: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("id", "workflow_id", "kind", "role"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 256))
        object.__setattr__(self, "title", _text(self.title, "title", 512))
        if not isinstance(self.status, WorkflowNodeStatus):
            raise TypeError("status must be WorkflowNodeStatus")
        object.__setattr__(
            self,
            "assigned_thread_id",
            _optional_text(self.assigned_thread_id, "assigned_thread_id", 256),
        )
        object.__setattr__(
            self, "git_checkpoint", _optional_text(self.git_checkpoint, "git_checkpoint", 512)
        )
        for name in ("input_refs", "output_refs", "evidence_refs"):
            object.__setattr__(self, name, _refs(getattr(self, name), name))
        created = _utc(self.created_at, "created_at")
        started = _optional_utc(self.started_at, "started_at")
        completed = _optional_utc(self.completed_at, "completed_at")
        if started is not None and started < created:
            raise ValueError("started_at must not precede created_at")
        baseline = started or created
        if completed is not None and completed < baseline:
            raise ValueError("completed_at must not precede node activity")
        terminal = self.status in {
            WorkflowNodeStatus.COMPLETED,
            WorkflowNodeStatus.FAILED,
            WorkflowNodeStatus.CANCELLED,
        }
        if terminal != (completed is not None):
            raise ValueError("terminal status and completed_at must agree")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "completed_at", completed)


@dataclass(frozen=True)
class WorkflowEdge:
    workflow_id: str
    source_node_id: str
    target_node_id: str
    kind: WorkflowEdgeKind

    def __post_init__(self) -> None:
        for name in ("workflow_id", "source_node_id", "target_node_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 256))
        if not isinstance(self.kind, WorkflowEdgeKind):
            raise TypeError("kind must be WorkflowEdgeKind")


@dataclass(frozen=True)
class WorkflowSnapshot:
    workflow: Workflow
    nodes: tuple[WorkflowNode, ...] = ()
    edges: tuple[WorkflowEdge, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, Workflow):
            raise TypeError("workflow must be Workflow")
        nodes = tuple(self.nodes)
        edges = tuple(self.edges)
        if any(not isinstance(node, WorkflowNode) for node in nodes):
            raise TypeError("nodes must contain WorkflowNode values")
        if any(not isinstance(edge, WorkflowEdge) for edge in edges):
            raise TypeError("edges must contain WorkflowEdge values")
        if any(node.workflow_id != self.workflow.id for node in nodes):
            raise ValueError("node belongs to another workflow")
        if any(edge.workflow_id != self.workflow.id for edge in edges):
            raise ValueError("edge belongs to another workflow")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be non-blank bounded text")
    return value


def _optional_text(value: object, name: str, maximum: int) -> str | None:
    return None if value is None else _text(value, name, maximum)


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _optional_utc(value: object, name: str) -> datetime | None:
    return None if value is None else _utc(value, name)


def _refs(values: Iterable[str], name: str) -> tuple[str, ...]:
    checked = tuple(_text(value, name, 512) for value in values)
    if len(checked) > 128 or len(set(checked)) != len(checked):
        raise ValueError(f"{name} must be unique and bounded")
    return checked
