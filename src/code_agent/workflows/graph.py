from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .models import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
)


class WorkflowGraphError(ValueError):
    pass


class WorkflowCycleError(WorkflowGraphError):
    pass


class WorkflowTransitionError(WorkflowGraphError):
    pass


_TRANSITIONS = {
    WorkflowNodeStatus.PLANNED: {
        WorkflowNodeStatus.QUEUED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.QUEUED: {
        WorkflowNodeStatus.RUNNING,
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.RUNNING: {
        WorkflowNodeStatus.BLOCKED,
        WorkflowNodeStatus.WAITING_DECISION,
        WorkflowNodeStatus.VERIFYING,
        WorkflowNodeStatus.COMPLETED,
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.BLOCKED: {
        WorkflowNodeStatus.QUEUED,
        WorkflowNodeStatus.RUNNING,
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.WAITING_DECISION: {
        WorkflowNodeStatus.RUNNING,
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.VERIFYING: {
        WorkflowNodeStatus.RUNNING,
        WorkflowNodeStatus.WAITING_DECISION,
        WorkflowNodeStatus.COMPLETED,
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.CANCELLED,
    },
    WorkflowNodeStatus.COMPLETED: {WorkflowNodeStatus.VERIFYING},
    WorkflowNodeStatus.FAILED: set(),
    WorkflowNodeStatus.CANCELLED: set(),
}


class WorkflowGraph:
    def __init__(
        self,
        workflow: Workflow,
        nodes: tuple[WorkflowNode, ...] = (),
        edges: tuple[WorkflowEdge, ...] = (),
    ) -> None:
        if not isinstance(workflow, Workflow):
            raise TypeError("workflow must be Workflow")
        self._workflow = workflow
        self._nodes: dict[str, WorkflowNode] = {}
        self._edges: list[WorkflowEdge] = []
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)

    def add_node(self, node: WorkflowNode) -> None:
        if not isinstance(node, WorkflowNode):
            raise TypeError("node must be WorkflowNode")
        if node.workflow_id != self._workflow.id:
            raise ValueError("node belongs to another workflow")
        prior = self._nodes.get(node.id)
        if prior is not None and prior != node:
            raise ValueError("node id already has different content")
        self._nodes[node.id] = node

    def add_edge(self, edge: WorkflowEdge) -> None:
        if not isinstance(edge, WorkflowEdge):
            raise TypeError("edge must be WorkflowEdge")
        if edge.workflow_id != self._workflow.id:
            raise ValueError("edge belongs to another workflow")
        if edge.source_node_id not in self._nodes or edge.target_node_id not in self._nodes:
            raise KeyError("workflow edge endpoint is missing")
        if edge.source_node_id == edge.target_node_id:
            raise WorkflowCycleError("workflow edge cannot point to itself")
        if self._reachable(edge.target_node_id, edge.source_node_id):
            raise WorkflowCycleError("workflow edge would create a cycle")
        if edge not in self._edges:
            self._edges.append(edge)

    def transition(
        self,
        node_id: str,
        status: WorkflowNodeStatus,
        *,
        at: datetime | None = None,
    ) -> WorkflowNode:
        if not isinstance(status, WorkflowNodeStatus):
            raise TypeError("status must be WorkflowNodeStatus")
        current = self._nodes.get(node_id)
        if current is None:
            raise KeyError("workflow node not found")
        if status == current.status:
            return current
        if status not in _TRANSITIONS[current.status]:
            raise WorkflowTransitionError(
                f"cannot transition {current.status.value} to {status.value}"
            )
        timestamp = _utc_now(at)
        started = current.started_at
        if status is WorkflowNodeStatus.RUNNING and started is None:
            started = timestamp
        terminal = status in {
            WorkflowNodeStatus.COMPLETED,
            WorkflowNodeStatus.FAILED,
            WorkflowNodeStatus.CANCELLED,
        }
        updated = replace(
            current,
            status=status,
            started_at=started,
            completed_at=timestamp if terminal else None,
        )
        self._nodes[node_id] = updated
        return updated

    def update_node(self, node: WorkflowNode) -> None:
        if not isinstance(node, WorkflowNode):
            raise TypeError("node must be WorkflowNode")
        current = self._nodes.get(node.id)
        if current is None:
            raise KeyError("workflow node not found")
        if (
            node.workflow_id != current.workflow_id
            or node.status != current.status
            or node.created_at != current.created_at
            or node.started_at != current.started_at
            or node.completed_at != current.completed_at
        ):
            raise ValueError("metadata update cannot change node identity or state")
        self._nodes[node.id] = node

    def snapshot(self) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            self._workflow, tuple(self._nodes.values()), tuple(self._edges)
        )

    def _reachable(self, start: str, target: str) -> bool:
        pending = [start]
        visited: set[str] = set()
        while pending:
            node_id = pending.pop()
            if node_id == target:
                return True
            if node_id in visited:
                continue
            visited.add(node_id)
            pending.extend(
                edge.target_node_id
                for edge in self._edges
                if edge.source_node_id == node_id
            )
        return False


def _utc_now(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("transition time must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
