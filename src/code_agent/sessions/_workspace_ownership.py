from __future__ import annotations

import sqlite3

from .errors import SessionCorruptionError, SessionNotFound
from .workspace_models import (
    CheckpointCursor,
    WorkspaceLineageStatus,
    WorkspaceSnapshotRecord,
)


def require_owner_task(connection: sqlite3.Connection, task_id: str | None) -> None:
    if task_id is None:
        return
    row = connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise SessionNotFound("lineage owner task not found")


def require_checkpoint_owner(
    connection: sqlite3.Connection,
    thread_id: str,
    cursor: CheckpointCursor,
    snapshot: WorkspaceSnapshotRecord | None,
) -> None:
    if cursor.lineage_id is None:
        raise ValueError("checkpoint cursor must identify a workspace lineage")
    lineage = connection.execute(
        "SELECT status, owner_task_id FROM workspace_lineages WHERE id = ?",
        (cursor.lineage_id,),
    ).fetchone()
    if lineage is None:
        raise SessionNotFound("checkpoint lineage not found")
    task = connection.execute(
        "SELECT id, workspace_lineage_id FROM tasks WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    _validate_checkpoint_owner(task, lineage, cursor.lineage_id)
    if snapshot is not None and snapshot.lineage_id != cursor.lineage_id:
        raise ValueError("snapshot belongs to another checkpoint lineage")


def _validate_checkpoint_owner(
    task: sqlite3.Row | None, lineage: sqlite3.Row, lineage_id: str
) -> None:
    if task is None:
        raise SessionNotFound("checkpoint thread has no task")
    if task["workspace_lineage_id"] != lineage_id:
        raise ValueError("checkpoint task is not bound to the cursor lineage")
    try:
        status = WorkspaceLineageStatus(lineage["status"])
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted lineage status") from error
    if status is not WorkspaceLineageStatus.ACTIVE:
        raise ValueError("checkpoint lineage is not active")
    if lineage["owner_task_id"] != task["id"]:
        raise ValueError("checkpoint task does not own the lineage")


def bind_task_lineage(
    connection: sqlite3.Connection, task_id: str, lineage_id: str
) -> None:
    row = connection.execute(
        "SELECT workspace_lineage_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise SessionNotFound("task not found")
    if row["workspace_lineage_id"] not in (None, lineage_id):
        raise ValueError("task already belongs to another workspace lineage")
    connection.execute(
        "UPDATE tasks SET workspace_lineage_id = ? WHERE id = ?", (lineage_id, task_id)
    )
