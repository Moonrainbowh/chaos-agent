from __future__ import annotations

import json
import sqlite3

from code_agent.workflows.graph import WorkflowGraph
from code_agent.workflows.models import (
    Workflow,
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
    WorkflowStatus,
)

from ._codec import decode_datetime, encode_datetime
from ._database import SessionDatabase
from ._records import _require_thread, _text
from .errors import SessionCorruptionError, SessionNotFound


class WorkflowRepositoryMixin:
    _database: SessionDatabase

    async def save_workflow_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        if not isinstance(snapshot, WorkflowSnapshot):
            raise TypeError("snapshot must be WorkflowSnapshot")
        validated = WorkflowGraph(
            snapshot.workflow, snapshot.nodes, snapshot.edges
        ).snapshot()
        workflow_payload = _workflow_payload(validated.workflow)
        node_payloads = tuple(_node_payload(node) for node in validated.nodes)

        def write(connection: sqlite3.Connection) -> None:
            _write_workflow(connection, validated, workflow_payload, node_payloads)

        await self._database.write(write)

    async def load_workflow_snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        workflow_id = _text(workflow_id, "workflow_id")

        def read(connection: sqlite3.Connection) -> WorkflowSnapshot:
            row = connection.execute(
                "SELECT payload FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound("workflow not found")
            return _load_snapshot(connection, _decode_workflow(row["payload"]))

        return await self._database.read(read)

    async def load_workflow_for_task(
        self, task_id: str
    ) -> WorkflowSnapshot | None:
        task_id = _text(task_id, "task_id")

        def read(connection: sqlite3.Connection) -> WorkflowSnapshot | None:
            row = connection.execute(
                "SELECT payload FROM workflows WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            return _load_snapshot(connection, _decode_workflow(row["payload"]))

        return await self._database.read(read)


def _write_workflow(
    connection: sqlite3.Connection,
    snapshot: WorkflowSnapshot,
    workflow_payload: str,
    node_payloads: tuple[str, ...],
) -> None:
    _validate_threads(connection, snapshot)
    workflow = snapshot.workflow
    connection.execute(
        "INSERT INTO workflows(id, root_thread_id, task_id, payload, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "root_thread_id=excluded.root_thread_id, task_id=excluded.task_id, "
        "payload=excluded.payload, updated_at=excluded.updated_at",
        (
            workflow.id,
            workflow.root_thread_id,
            workflow.task_id,
            workflow_payload,
            encode_datetime(workflow.created_at),
            encode_datetime(workflow.updated_at),
        ),
    )
    connection.execute(
        "DELETE FROM workflow_edges WHERE workflow_id = ?", (workflow.id,)
    )
    _remove_stale_nodes(connection, workflow.id, snapshot.nodes)
    _write_nodes(connection, workflow.id, snapshot.nodes, node_payloads)
    _write_edges(connection, workflow.id, snapshot.edges)


def _write_nodes(
    connection: sqlite3.Connection,
    workflow_id: str,
    nodes: tuple[WorkflowNode, ...],
    payloads: tuple[str, ...],
) -> None:
    for position, (node, payload) in enumerate(zip(nodes, payloads)):
        connection.execute(
            "INSERT INTO workflow_nodes(id, workflow_id, position, status, payload) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "workflow_id=excluded.workflow_id, position=excluded.position, "
            "status=excluded.status, payload=excluded.payload",
            (node.id, workflow_id, position, node.status.value, payload),
        )


def _write_edges(
    connection: sqlite3.Connection,
    workflow_id: str,
    edges: tuple[WorkflowEdge, ...],
) -> None:
    connection.executemany(
        "INSERT INTO workflow_edges(workflow_id, source_node_id, target_node_id, kind, position) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            (workflow_id, edge.source_node_id, edge.target_node_id, edge.kind.value, position)
            for position, edge in enumerate(edges)
        ),
    )


def _validate_threads(
    connection: sqlite3.Connection, snapshot: WorkflowSnapshot
) -> None:
    root = snapshot.workflow.root_thread_id
    _require_thread(connection, root)
    for node in snapshot.nodes:
        thread_id = node.assigned_thread_id
        if thread_id is None or thread_id == root:
            continue
        row = connection.execute(
            "SELECT parent_thread_id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None or row["parent_thread_id"] != root:
            raise ValueError("workflow node thread is outside the root thread tree")


def _remove_stale_nodes(
    connection: sqlite3.Connection,
    workflow_id: str,
    nodes: tuple[WorkflowNode, ...],
) -> None:
    if not nodes:
        connection.execute(
            "DELETE FROM workflow_nodes WHERE workflow_id = ?", (workflow_id,)
        )
        return
    placeholders = ",".join("?" for _ in nodes)
    connection.execute(
        f"DELETE FROM workflow_nodes WHERE workflow_id = ? AND id NOT IN ({placeholders})",
        (workflow_id, *(node.id for node in nodes)),
    )


def _load_snapshot(
    connection: sqlite3.Connection, workflow: Workflow
) -> WorkflowSnapshot:
    node_rows = connection.execute(
        "SELECT payload FROM workflow_nodes WHERE workflow_id = ? ORDER BY position",
        (workflow.id,),
    ).fetchall()
    edge_rows = connection.execute(
        "SELECT source_node_id, target_node_id, kind FROM workflow_edges "
        "WHERE workflow_id = ? ORDER BY position",
        (workflow.id,),
    ).fetchall()
    nodes = tuple(_decode_node(row["payload"]) for row in node_rows)
    edges = tuple(
        WorkflowEdge(
            workflow.id,
            row["source_node_id"],
            row["target_node_id"],
            WorkflowEdgeKind(row["kind"]),
        )
        for row in edge_rows
    )
    try:
        return WorkflowGraph(workflow, nodes, edges).snapshot()
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted workflow graph") from error


def _workflow_payload(workflow: Workflow) -> str:
    return _json(
        {
            "id": workflow.id,
            "root_thread_id": workflow.root_thread_id,
            "task_id": workflow.task_id,
            "title": workflow.title,
            "status": workflow.status.value,
            "created_at": encode_datetime(workflow.created_at),
            "updated_at": encode_datetime(workflow.updated_at),
        }
    )


def _node_payload(node: WorkflowNode) -> str:
    return _json(
        {
            "id": node.id,
            "workflow_id": node.workflow_id,
            "kind": node.kind,
            "title": node.title,
            "status": node.status.value,
            "assigned_thread_id": node.assigned_thread_id,
            "role": node.role,
            "input_refs": node.input_refs,
            "output_refs": node.output_refs,
            "evidence_refs": node.evidence_refs,
            "git_checkpoint": node.git_checkpoint,
            "created_at": encode_datetime(node.created_at),
            "started_at": None if node.started_at is None else encode_datetime(node.started_at),
            "completed_at": None if node.completed_at is None else encode_datetime(node.completed_at),
        }
    )


def _decode_workflow(payload: str) -> Workflow:
    try:
        value = json.loads(payload)
        return Workflow(
            value["id"],
            value["root_thread_id"],
            value["task_id"],
            value["title"],
            WorkflowStatus(value["status"]),
            decode_datetime(value["created_at"], "workflow"),
            decode_datetime(value["updated_at"], "workflow"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid persisted workflow") from error


def _decode_node(payload: str) -> WorkflowNode:
    try:
        value = json.loads(payload)
        return WorkflowNode(
            value["id"],
            value["workflow_id"],
            value["kind"],
            value["title"],
            WorkflowNodeStatus(value["status"]),
            value["assigned_thread_id"],
            value["role"],
            tuple(value["input_refs"]),
            tuple(value["output_refs"]),
            tuple(value["evidence_refs"]),
            value["git_checkpoint"],
            decode_datetime(value["created_at"], "workflow node"),
            None if value["started_at"] is None else decode_datetime(value["started_at"], "workflow node"),
            None if value["completed_at"] is None else decode_datetime(value["completed_at"], "workflow node"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SessionCorruptionError("invalid persisted workflow node") from error


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
