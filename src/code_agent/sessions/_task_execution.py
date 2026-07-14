from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable

from ._codec import encode_datetime, encode_metadata, utc_now
from ._records import _touch_thread, _text
from .errors import SessionNotFound


OwnerAlive = Callable[[int, float], bool]


async def register(database: object, task_id: str, instance_id: str, owner_pid: int, owner_create_time: float) -> None:
    task_id = _text(task_id, "task_id")
    instance_id = _text(instance_id, "instance_id")
    if len(instance_id) > 128:
        raise ValueError("instance_id must be at most 128 characters")
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ValueError("owner_pid must be a positive integer")
    if isinstance(owner_create_time, bool) or not isinstance(owner_create_time, (int, float)) or owner_create_time <= 0:
        raise ValueError("owner_create_time must be positive")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise SessionNotFound("task not found")
        if row["status"] not in {"running", "verifying"}:
            raise ValueError("only active tasks can register an execution")
        connection.execute("INSERT INTO task_executions(task_id, instance_id, owner_pid, owner_create_time, started_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET instance_id = excluded.instance_id, owner_pid = excluded.owner_pid, owner_create_time = excluded.owner_create_time, started_at = excluded.started_at", (task_id, instance_id, owner_pid, float(owner_create_time), timestamp))

    await database.write(write)  # type: ignore[attr-defined]


async def reconcile_stale(database: object, owner_alive: OwnerAlive) -> tuple[str, ...]:
    if not callable(owner_alive):
        raise TypeError("owner_alive must be callable")
    timestamp = encode_datetime(utc_now())

    def write(connection: sqlite3.Connection) -> tuple[str, ...]:
        rows = connection.execute("SELECT t.id, t.thread_id, e.owner_pid, e.owner_create_time FROM tasks t JOIN task_executions e ON e.task_id = t.id WHERE t.status IN ('running', 'verifying')").fetchall()
        interrupted: list[str] = []
        for row in rows:
            if owner_alive(int(row["owner_pid"]), float(row["owner_create_time"])):
                continue
            task_id, thread_id = row["id"], row["thread_id"]
            connection.execute("UPDATE tasks SET status = 'interrupted', stop_reason = ?, updated_at = ? WHERE id = ?", ("execution owner is no longer alive", timestamp, task_id))
            connection.execute("INSERT INTO checkpoints(id, thread_id, label, metadata, created_at) VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().hex, thread_id, "task-interrupted", encode_metadata({"task_id": task_id, "status": "interrupted", "reason": "execution owner is no longer alive"}), timestamp))
            connection.execute("DELETE FROM task_executions WHERE task_id = ?", (task_id,))
            _touch_thread(connection, thread_id, timestamp)
            interrupted.append(task_id)
        return tuple(interrupted)

    return await database.write(write)  # type: ignore[attr-defined]
