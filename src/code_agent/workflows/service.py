from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from .graph import WorkflowGraph
from .models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
)
from .observations import (
    ChildRunObservation,
    DeliveryObservation,
    EvidenceInvalidatedObservation,
    HostObservation,
    RecoveryObservation,
    TaskCreatedObservation,
    VerificationObservation,
)


class WorkflowStore(Protocol):
    async def save_workflow_snapshot(self, snapshot: WorkflowSnapshot) -> None: ...

    async def load_workflow_for_task(
        self, task_id: str
    ) -> WorkflowSnapshot | None: ...


class WorkflowService:
    """Project typed Host observations into durable Workflow snapshots."""

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._listeners: list[Callable[[WorkflowSnapshot], None]] = []

    def subscribe(self, listener: Callable[[WorkflowSnapshot], None]) -> None:
        if not callable(listener):
            raise TypeError("listener must be callable")
        self._listeners.append(listener)

    async def observe(self, observation: HostObservation) -> WorkflowSnapshot:
        if isinstance(observation, TaskCreatedObservation):
            snapshot = await self._task_created(observation)
        elif isinstance(observation, ChildRunObservation):
            snapshot = await self._child(observation)
        elif isinstance(observation, VerificationObservation):
            snapshot = await self._verification(observation)
        elif isinstance(observation, EvidenceInvalidatedObservation):
            snapshot = await self._invalidate(observation)
        elif isinstance(observation, DeliveryObservation):
            snapshot = await self._delivery(observation)
        elif isinstance(observation, RecoveryObservation):
            snapshot = await self._recover(observation)
        else:
            raise TypeError("observation must be a typed Host observation")
        await self._store.save_workflow_snapshot(snapshot)
        for listener in tuple(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                continue
        return snapshot

    async def _task_created(
        self, observation: TaskCreatedObservation
    ) -> WorkflowSnapshot:
        existing = await self._store.load_workflow_for_task(observation.task_id)
        if existing is not None:
            return existing
        workflow = Workflow(
            f"workflow:{_text(observation.task_id, 'task_id')}",
            _text(observation.thread_id, "thread_id"),
            observation.task_id,
            _text(observation.title, "title"),
        )
        graph = WorkflowGraph(workflow)
        main_id = _main_id(observation.task_id)
        graph.add_node(
            WorkflowNode(
                main_id,
                workflow.id,
                "main",
                observation.title,
                WorkflowNodeStatus.PLANNED,
                observation.thread_id,
                "main",
            )
        )
        _advance(graph, main_id, WorkflowNodeStatus.RUNNING)
        return graph.snapshot()

    async def _child(self, observation: ChildRunObservation) -> WorkflowSnapshot:
        graph = await self._load_graph(observation.task_id)
        node_id = _text(observation.run_id, "run_id")
        if not _has_node(graph, node_id):
            graph.add_node(
                WorkflowNode(
                    node_id,
                    graph.snapshot().workflow.id,
                    "subagent",
                    _text(observation.title, "title"),
                    WorkflowNodeStatus.PLANNED,
                    _text(observation.thread_id, "thread_id"),
                    _text(observation.role, "role"),
                )
            )
            graph.add_edge(
                WorkflowEdge(
                    graph.snapshot().workflow.id,
                    _main_id(observation.task_id),
                    node_id,
                    WorkflowEdgeKind.REQUIRES,
                )
            )
        _advance(graph, node_id, observation.status)
        return graph.snapshot()

    async def _verification(
        self, observation: VerificationObservation
    ) -> WorkflowSnapshot:
        graph = await self._load_graph(observation.task_id)
        if not _has_node(graph, observation.node_id):
            _add_dependent_node(
                graph,
                observation.node_id,
                "verification",
                "Verification",
                _main_id(observation.task_id),
            )
            _advance(graph, observation.node_id, WorkflowNodeStatus.VERIFYING)
        target = (
            WorkflowNodeStatus.COMPLETED
            if observation.passed
            else WorkflowNodeStatus.FAILED
        )
        _advance(graph, observation.node_id, target)
        if observation.passed:
            node = _node(graph, observation.node_id)
            graph.update_node(
                replace(node, evidence_refs=tuple(observation.evidence_refs))
            )
        return graph.snapshot()

    async def _invalidate(
        self, observation: EvidenceInvalidatedObservation
    ) -> WorkflowSnapshot:
        graph = await self._load_graph(observation.task_id)
        _advance(graph, observation.node_id, WorkflowNodeStatus.VERIFYING)
        graph.update_node(replace(_node(graph, observation.node_id), evidence_refs=()))
        return graph.snapshot()

    async def _delivery(
        self, observation: DeliveryObservation
    ) -> WorkflowSnapshot:
        graph = await self._load_graph(observation.task_id)
        verification = next(
            (
                node
                for node in graph.snapshot().nodes
                if node.kind == "verification"
                and node.status is WorkflowNodeStatus.COMPLETED
            ),
            None,
        )
        if verification is None:
            raise ValueError("delivery requires completed verification")
        if not _has_node(graph, observation.node_id):
            _add_dependent_node(
                graph,
                observation.node_id,
                "delivery",
                "Delivery",
                verification.id,
            )
        _advance(graph, observation.node_id, observation.status)
        return graph.snapshot()

    async def _recover(
        self, observation: RecoveryObservation
    ) -> WorkflowSnapshot:
        graph = await self._load_graph(observation.task_id)
        for node in graph.snapshot().nodes:
            if node.assigned_thread_id in observation.live_thread_ids:
                continue
            if node.status is WorkflowNodeStatus.RUNNING:
                graph.transition(node.id, WorkflowNodeStatus.BLOCKED)
                graph.transition(node.id, WorkflowNodeStatus.QUEUED)
            elif node.status is WorkflowNodeStatus.VERIFYING:
                graph.transition(node.id, WorkflowNodeStatus.CANCELLED)
        return graph.snapshot()

    async def _load_graph(self, task_id: str) -> WorkflowGraph:
        snapshot = await self._store.load_workflow_for_task(_text(task_id, "task_id"))
        if snapshot is None:
            raise KeyError("workflow task not found")
        return WorkflowGraph(snapshot.workflow, snapshot.nodes, snapshot.edges)


def _add_dependent_node(
    graph: WorkflowGraph, node_id: str, kind: str, title: str, source_id: str
) -> None:
    workflow_id = graph.snapshot().workflow.id
    graph.add_node(
        WorkflowNode(
            _text(node_id, "node_id"),
            workflow_id,
            kind,
            title,
            WorkflowNodeStatus.PLANNED,
        )
    )
    graph.add_edge(
        WorkflowEdge(
            workflow_id, source_id, node_id, WorkflowEdgeKind.REQUIRES
        )
    )


def _advance(
    graph: WorkflowGraph, node_id: str, target: WorkflowNodeStatus
) -> None:
    current = _node(graph, node_id).status
    if current is target:
        return
    paths = {
        WorkflowNodeStatus.QUEUED: (WorkflowNodeStatus.QUEUED,),
        WorkflowNodeStatus.RUNNING: (
            WorkflowNodeStatus.QUEUED,
            WorkflowNodeStatus.RUNNING,
        ),
        WorkflowNodeStatus.VERIFYING: (
            WorkflowNodeStatus.QUEUED,
            WorkflowNodeStatus.RUNNING,
            WorkflowNodeStatus.VERIFYING,
        ),
        WorkflowNodeStatus.COMPLETED: (
            WorkflowNodeStatus.QUEUED,
            WorkflowNodeStatus.RUNNING,
            WorkflowNodeStatus.COMPLETED,
        ),
        WorkflowNodeStatus.FAILED: (
            WorkflowNodeStatus.QUEUED,
            WorkflowNodeStatus.RUNNING,
            WorkflowNodeStatus.FAILED,
        ),
        WorkflowNodeStatus.CANCELLED: (WorkflowNodeStatus.CANCELLED,),
    }
    path = paths.get(target)
    if path is None:
        graph.transition(node_id, target)
        return
    for status in path:
        if _node(graph, node_id).status is status:
            continue
        try:
            graph.transition(node_id, status)
        except ValueError:
            if status is not target:
                continue
            raise


def _has_node(graph: WorkflowGraph, node_id: str) -> bool:
    return any(node.id == node_id for node in graph.snapshot().nodes)


def _node(graph: WorkflowGraph, node_id: str) -> WorkflowNode:
    return next(node for node in graph.snapshot().nodes if node.id == node_id)


def _main_id(task_id: str) -> str:
    return f"main:{task_id}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    return value
