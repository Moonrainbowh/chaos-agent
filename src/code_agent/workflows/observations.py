from __future__ import annotations

from dataclasses import dataclass

from .models import WorkflowNodeStatus


@dataclass(frozen=True)
class TaskCreatedObservation:
    task_id: str
    thread_id: str
    title: str


@dataclass(frozen=True)
class ChildRunObservation:
    task_id: str
    run_id: str
    thread_id: str
    role: str
    title: str
    status: WorkflowNodeStatus


@dataclass(frozen=True)
class VerificationObservation:
    task_id: str
    node_id: str
    passed: bool
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceInvalidatedObservation:
    task_id: str
    node_id: str


@dataclass(frozen=True)
class DeliveryObservation:
    task_id: str
    node_id: str
    status: WorkflowNodeStatus


@dataclass(frozen=True)
class RecoveryObservation:
    task_id: str
    live_thread_ids: frozenset[str] = frozenset()


HostObservation = (
    TaskCreatedObservation
    | ChildRunObservation
    | VerificationObservation
    | EvidenceInvalidatedObservation
    | DeliveryObservation
    | RecoveryObservation
)
