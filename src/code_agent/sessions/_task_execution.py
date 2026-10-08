from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable

from code_agent.core.task import TaskRecord, TaskStatus

from ._codec import encode_datetime, encode_metadata, encode_task, utc_now
from ._records import _touch_thread, _text
from ._task_records import _task_row, row_task
from .errors import SessionNotFound


OwnerAlive = Callable[[int, float], bool]


async def release(database: object, task_id: str, instance_id: str) -> bool:
    """Release only this execution; an old finally cannot erase a newer owner."""
    task_id, instance_id = _text(task_id, "task_id"), _text(instance_id, "instance_id")
    def write(connection: sqlite3.Connection) -> bool:
        return connection.execute("DELETE FROM task_executions WHERE task_id=? AND instance_id=?",
            (task_id, instance_id)).rowcount == 1
    return await database.write(write)


def _identity(task_id: str, instance_id: str, owner_pid: int, owner_create_time: float) -> tuple[str, str, int, float]:
    task_id = _text(task_id, "task_id")
    instance_id = _text(instance_id, "instance_id")
    if len(instance_id) > 128:
        raise ValueError("instance_id must be at most 128 characters")
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ValueError("owner_pid must be a positive integer")
    if isinstance(owner_create_time, bool) or not isinstance(owner_create_time, (int, float)) or owner_create_time <= 0:
        raise ValueError("owner_create_time must be positive")
    return task_id, instance_id, owner_pid, float(owner_create_time)


def _same_owner(connection: sqlite3.Connection, task_id: str, instance_id: str,
                owner_pid: int, owner_create_time: float) -> bool:
    existing = connection.execute(
        "SELECT instance_id, owner_pid, owner_create_time FROM task_executions WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if existing is None:
        return False
    if tuple(existing) == (instance_id, owner_pid, owner_create_time):
        return True
    raise ValueError("task execution already has a different owner; release or reconcile it first")


def _register(connection: sqlite3.Connection, identity: tuple[str, str, int, float]) -> None:
    if _same_owner(connection, *identity):
        return
    connection.execute(
        "INSERT INTO task_executions(task_id, instance_id, owner_pid, owner_create_time, started_at) "
        "VALUES (?, ?, ?, ?, ?)", (*identity, encode_datetime(utc_now())),
    )


async def begin(database: object, task_id: str, instance_id: str,
                owner_pid: int, owner_create_time: float) -> TaskRecord:
    """Claim and activate atomically; an exact repeat preserves all task facts.

    Check ownership before transition so a denied resume cannot alter lifecycle.
    The existing TaskRecord transition rules reject terminal activation.
    """
    identity = _identity(task_id, instance_id, owner_pid, owner_create_time)
    task_id = identity[0]

    def write(connection: sqlite3.Connection) -> TaskRecord:
        row = _task_row(connection, "id", task_id)
        if row is None:
            raise SessionNotFound("task not found")
        task = row_task(row)
        if _same_owner(connection, *identity):
            return task
        updated = task if task.status is TaskStatus.RUNNING else task.transition(TaskStatus.RUNNING)
        if updated is not task:
            connection.execute(
                "UPDATE tasks SET contract = ?, status = ?, stop_reason = ?, updated_at = ? WHERE id = ?",
                (encode_task(updated), updated.status.value, updated.stop_reason,
                 encode_datetime(updated.updated_at), updated.id),
            )
        _register(connection, identity)
        return updated

    return await database.write(write)  # type: ignore[attr-defined]


async def register(database: object, task_id: str, instance_id: str, owner_pid: int, owner_create_time: float) -> None:
    """Claim an unowned active task; only the exact existing owner is idempotent.

    Registration never replaces an owner, including one believed to be stale.
    Callers must explicitly release or reconcile the prior execution first.
    """
    identity = _identity(task_id, instance_id, owner_pid, owner_create_time)
    task_id = identity[0]

    def write(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise SessionNotFound("task not found")
        if row["status"] not in {"running", "verifying"}:
            raise ValueError("only active tasks can register an execution")
        _register(connection, identity)

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
