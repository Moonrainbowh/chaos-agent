"""Read durable execution identity; request roots cannot grant authority."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from ._task_records import _task_row, row_task
from .errors import SessionNotFound


def text(value, name, limit=1024):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


def binding(connection, task_id, workspace_root, kind="approval", *, allow_terminal=False):
    text(task_id, "task_id")
    text(workspace_root, "workspace_root", 4096)
    if not Path(workspace_root).is_absolute():
        raise ValueError("workspace_root must be absolute")
    row = _task_row(connection, "id", task_id)
    if row is None:
        raise SessionNotFound("task not found")
    task = row_task(row)
    if kind not in {"approval", "decision"}:
        raise ValueError("invalid approval kind")
    statuses = {"running", "verifying"} if kind == "approval" else {"paused", "interrupted", "waiting_decision"}
    if task.status.value not in statuses and not (allow_terminal and kind == "decision" and task.status.value in {"accepted_partial", "failed"}):
        raise ValueError("task is not executing")
    root = task.contract.authorization.workspace_root
    lineage = connection.execute("SELECT workspace_lineage_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    if lineage:
        root = connection.execute("SELECT worktree_root FROM workspace_lineages WHERE id=?", (lineage,)).fetchone()[0]
    normalize = lambda p: os.path.normcase(str(Path(p).resolve()))
    if normalize(root) != normalize(workspace_root):
        raise ValueError("execution workspace mismatch")
    owner = connection.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
    if kind == "approval" and owner is None:
        raise ValueError("execution owner missing")
    if kind == "decision" and owner is not None:
        raise ValueError("decision requires reconciled released owner")
    state = connection.execute("SELECT payload,updated_at FROM task_states WHERE thread_id=?", (task.thread_id,)).fetchone()
    material = [kind, task.to_dict(), None if owner is None else list(owner), None if state is None else list(state), normalize(root)]
    if kind == "decision":
        from ._history_queries import history_stats
        material.append(history_stats(connection, task.thread_id))
    version = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"task_id": task.id, "thread_id": task.thread_id, "workspace_root": normalize(root),
            "state_version": version, "owner_instance_id": "" if owner is None else owner["instance_id"], "kind": kind}


def record(row):
    value = dict(row)
    value["preview"] = json.loads(value["preview"])
    if value["response"] is not None:
        value["response"] = bool(value["response"])
    return value
